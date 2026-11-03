#!/usr/bin/env python3
"""Check the shared ORM against a local PostgreSQL test database.

Set EDUAGENT_SCHEMA_TEST_DSN to an explicit postgresql+asyncpg URL whose host is
loopback. The checks insert disposable rows in one transaction and always roll
it back. This script never creates or alters tables.
"""

from __future__ import annotations

import asyncio
import ipaddress
import os
import sys
import uuid
from datetime import date
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from eduagent_shared.db import Base
from eduagent_shared import models


DSN_ENV = "EDUAGENT_SCHEMA_TEST_DSN"
TARGETS = (
    models.MistakeRecord,
    models.Flashcard,
    models.StudyPlan,
    models.KnowledgeDocument,
    models.KbChunk,
    models.Memory,
    models.LessonPlan,
    models.Presentation,
    models.ExamPaper,
    models.LearningAnalysis,
)
SQL_START = "-- BEGIN EDUAGENT ORM COLUMN COMPATIBILITY"
SQL_END = "-- END EDUAGENT ORM COLUMN COMPATIBILITY"


def local_test_url() -> URL:
    raw = os.environ.get(DSN_ENV)
    if not raw:
        raise ValueError(f"Set {DSN_ENV} to an explicit local test DSN")
    try:
        url = make_url(raw)
    except Exception as exc:
        raise ValueError(f"{DSN_ENV} is not a valid SQLAlchemy URL") from exc
    if url.drivername != "postgresql+asyncpg" or not url.host or not url.database:
        raise ValueError(f"{DSN_ENV} must use postgresql+asyncpg with a host and database")
    if url.host != "localhost":
        try:
            if not ipaddress.ip_address(url.host).is_loopback:
                raise ValueError(f"{DSN_ENV} must point to a loopback host")
        except ValueError as exc:
            raise ValueError(f"{DSN_ENV} must point to a loopback host") from exc
    return url


def sql_patch_matches_init() -> bool:
    root = Path(__file__).resolve().parents[2]
    init_sql = (root / "infra/postgres/init.sql").read_text(encoding="utf-8")
    patch_sql = (root / "infra/postgres/compat_orm_columns.sql").read_text(encoding="utf-8")
    bodies = []
    for sql in (init_sql, patch_sql):
        if sql.count(SQL_START) != 1 or sql.count(SQL_END) != 1:
            return False
        bodies.append(sql.split(SQL_START, 1)[1].split(SQL_END, 1)[0].strip())
    return bodies[0] == bodies[1]


def error_label(exc: Exception) -> str:
    """Report the failure without ever rendering a DSN or SQL parameters."""
    original = getattr(exc, "orig", exc)
    code = getattr(original, "sqlstate", None)
    table = getattr(original, "table_name", None)
    column = getattr(original, "column_name", None)
    details = [type(original).__name__]
    if code:
        details.append(f"SQLSTATE={code}")
    if table:
        details.append(f"table={table}")
    if column:
        details.append(f"column={column}")
    return " ".join(details)


async def verify(url: URL) -> list[str]:
    issues: list[str] = []
    engine = create_async_engine(url, poolclass=NullPool, hide_parameters=True)
    all_models = sorted((mapper.class_ for mapper in Base.registry.mappers),
                        key=lambda model: model.__tablename__)
    checked_selects = 0
    checked_writes = 0
    probe_user_id = uuid.uuid4()
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                async with AsyncSession(bind=connection, expire_on_commit=False) as session:
                    for model in all_models:
                        try:
                            async with session.begin_nested():
                                await session.execute(select(model).limit(0))
                            checked_selects += 1
                        except Exception as exc:
                            issues.append(f"SELECT {model.__tablename__}: {error_label(exc)}")

                    issues.extend(await check_columns(session))

                    try:
                        async with session.begin_nested():
                            session.add(models.User(id=probe_user_id))
                            await session.flush()
                    except Exception as exc:
                        issues.append(f"INSERT users prerequisite: {error_label(exc)}")
                    else:
                        document_id: uuid.UUID | None = None
                        for model in TARGETS:
                            if model is models.KbChunk and document_id is None:
                                issues.append("INSERT kb_chunks: prerequisite knowledge_documents failed")
                                continue
                            sample = make_sample(model, probe_user_id, document_id)
                            try:
                                async with session.begin_nested():
                                    session.add(sample)
                                    await session.flush()
                                    row = await session.scalar(
                                        select(model).where(model.id == sample.id)
                                    )
                                    if row is None:
                                        raise AssertionError("inserted row was not readable")
                                checked_writes += 1
                                if model is models.KnowledgeDocument:
                                    document_id = sample.id
                            except Exception as exc:
                                issues.append(f"INSERT/READ {model.__tablename__}: {error_label(exc)}")
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()

    print(f"ORM SELECT LIMIT 0: {checked_selects}/{len(all_models)}")
    print(f"Transactional INSERT/READ: {checked_writes}/{len(TARGETS)}")
    print("Probe transaction: rolled back")
    return issues


async def check_columns(session: AsyncSession) -> list[str]:
    """Catch nullable and legacy mandatory columns that SELECT alone misses."""
    issues: list[str] = []
    rows = (await session.execute(text("""
        SELECT table_name, column_name, is_nullable, column_default,
               is_identity, is_generated
        FROM information_schema.columns
        WHERE table_schema = current_schema()
    """))).mappings().all()
    actual = {(row["table_name"], row["column_name"]): row for row in rows}
    for model in TARGETS:
        table = model.__tablename__
        mapped = {column.name: column for column in model.__table__.columns}
        for name, column in mapped.items():
            found = actual.get((table, name))
            if found is None:
                issues.append(f"COLUMN {table}.{name}: missing")
            elif column.nullable and found["is_nullable"] == "NO":
                issues.append(f"NULL {table}.{name}: ORM permits NULL, database rejects it")
        for (actual_table, name), found in actual.items():
            if actual_table != table or name in mapped:
                continue
            if (found["is_nullable"] == "NO" and found["column_default"] is None
                    and found["is_identity"] != "YES" and found["is_generated"] == "NEVER"):
                issues.append(f"COLUMN {table}.{name}: unmapped mandatory field")
    return issues


def make_sample(model: type, user_id: uuid.UUID,
                document_id: uuid.UUID | None) -> object:
    common = {"id": uuid.uuid4(), "user_id": user_id}
    if model is models.MistakeRecord:
        return model(**common, question="Schema contract probe", knowledge_points=[])
    if model is models.Flashcard:
        return model(**common, front="Question", back="Answer")
    if model is models.StudyPlan:
        return model(**common, title="Schema contract probe", start_date=date(2026, 1, 1),
                     end_date=date(2026, 1, 2), plan_content={})
    if model is models.KnowledgeDocument:
        return model(**common, title="Schema contract probe", metadata_={})
    if model is models.KbChunk:
        return model(**common, document_id=document_id, chunk_index=0, content="Probe",
                     embedding=[0.0] * 1536, metadata_={})
    if model is models.Memory:
        return model(**common, memory_type="profile", content="Probe",
                     embedding=[0.0] * 1536, metadata_={})
    if model is models.LessonPlan:
        return model(**common, title="Schema contract probe", objectives=[], content={})
    if model is models.Presentation:
        return model(**common, title="Schema contract probe", outline=[])
    if model is models.ExamPaper:
        return model(**common, title="Schema contract probe", questions=[])
    if model is models.LearningAnalysis:
        return model(**common, analysis_type="weekly", period_start=date(2026, 1, 1),
                     period_end=date(2026, 1, 2), metrics={}, suggestions=[])
    raise AssertionError(f"No sample for {model.__name__}")


async def main() -> int:
    try:
        url = local_test_url()
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2
    try:
        issues = await verify(url)
        if not sql_patch_matches_init():
            issues.append("SQL compatibility patch differs from init.sql marker contents")
    except Exception as exc:
        print(f"Schema check could not complete: {error_label(exc)}", file=sys.stderr)
        return 2
    for issue in issues:
        print(f"FAIL {issue}")
    if issues:
        print(f"Schema contract: FAIL ({len(issues)} issue(s))")
        return 1
    print("Schema contract: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
