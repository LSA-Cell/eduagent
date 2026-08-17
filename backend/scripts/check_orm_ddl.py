"""交叉校验：shared ORM (Base.metadata) 与 infra/postgres/init.sql DDL 列对齐检查。"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # 仓库根（backend/scripts/ 上两级）
sys.path.insert(0, str(ROOT / "backend" / "shared"))

from eduagent_shared.db import Base  # noqa: E402
import eduagent_shared.models  # noqa: E402,F401  注册全部表到 metadata

DDL = (ROOT / "infra" / "postgres" / "init.sql").read_text(encoding="utf-8")

# 解析 DDL：table_name -> {col_name: type_str}
ddl_tables: dict[str, dict[str, str]] = {}
for m in re.finditer(
    r"CREATE TABLE IF NOT EXISTS (\w+) \((.*?)\n\);", DDL, re.S
):
    name, body = m.group(1), m.group(2)
    cols: dict[str, str] = {}
    for line in body.splitlines():
        line = line.strip().rstrip(",")
        if not line or line.startswith("--"):
            continue
        cm = re.match(r"(\w+)\s+([A-Z]+(?:\(\d+\))?(?:\[\])?)", line)
        if cm and cm.group(1).upper() not in {
            "PRIMARY", "FOREIGN", "UNIQUE", "CHECK", "CONSTRAINT",
        }:
            cols[cm.group(1)] = cm.group(2).upper()
    ddl_tables[name] = cols

# ORM 类型 -> DDL 期望基型
def orm_type(col) -> str:
    t = str(col.type).upper()
    if "TIMESTAMP" in t or "DATETIME" in t:
        return "TIMESTAMPTZ"
    if t.startswith("VARCHAR") or t.startswith("STRING"):
        n = getattr(col.type, "length", None)
        return f"VARCHAR({n})" if n else "VARCHAR"
    if "JSONB" in t:
        return "JSONB"
    if "VECTOR" in t:
        return "VECTOR(1536)"
    if "BIGINT" in t:
        return "BIGINT"
    if "INTEGER" in t or "INT" in t:
        return "INTEGER"
    if "FLOAT" in t or "DOUBLE" in t:
        return "FLOAT"
    if "BOOLEAN" in t or "BOOL" in t:
        return "BOOLEAN"
    if t == "DATE":
        return "DATE"
    if "TEXT" in t:
        return "TEXT"
    if "UUID" in t or "CHAR(32)" in t:  # SQLAlchemy 泛型 Uuid 在非 PG 方言渲染为 CHAR(32)
        return "UUID"
    return t

ok, problems = [], []
orm_names = set()
for table in Base.metadata.sorted_tables:
    name = table.name
    orm_names.add(name)
    if name not in ddl_tables:
        problems.append(f"[MISSING IN DDL] {name}")
        continue
    ddl_cols = ddl_tables[name]
    for col in table.columns:
        cname = col.name
        if cname not in ddl_cols:
            problems.append(f"[{name}] 列缺失: {cname}")
            continue
        want, got = orm_type(col), ddl_cols[cname]
        # VARCHAR 长度容差：ORM String(50) vs DDL VARCHAR(50) 精确比；TEXT/JSONB 宽松
        if want != got and not (
            want.startswith("VARCHAR") and got.startswith("VARCHAR")
            and want == got
        ):
            problems.append(f"[{name}.{cname}] 类型: ORM={want} DDL={got}")
    extra = set(ddl_cols) - {c.name for c in table.columns}
    if extra:
        problems.append(f"[{name}] DDL 多余列: {sorted(extra)}")
    ok.append(name)

ddl_only = sorted(set(ddl_tables) - orm_names)
print(f"ORM 表数: {len(orm_names)}; DDL 表数: {len(ddl_tables)}")
print(f"DDL-only（编排审计/注册表，可接受）: {ddl_only}")
if problems:
    print("\n!!! 不一致项:")
    for p in problems:
        print("  " + p)
    sys.exit(1)
print("\n全部 ORM 表与 DDL 列对齐 ✔")
