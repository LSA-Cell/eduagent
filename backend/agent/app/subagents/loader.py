"""子 Agent 定义加载：YAML 内置基线 + 校验 + DB 表双读预留接口。

Schema（设计文档 §3.3 固定字段）：name/description/subject_routing/
tools_allowlist/async_via/timeout_s/card_type/input_form。
扩展字段（本实现新增，可选）：role_required（教师专属子 Agent 的派发门槛——
§3.2 规定教师 4 工具 role_required="teacher"，它们不注册为独立工具，
门槛只能落在 dispatch 处，故在定义上自描述携带）。

DB 双读预留：``load_registry(session_factory=...)`` 在提供会话工厂时尝试读
``sub_agent_definitions`` 表（同名行覆盖 YAML 基线，is_active=false 下架），
读表失败/表不存在时 fail-soft 回退纯 YAML；未提供工厂时直接 YAML-only。
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Callable, Optional

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator

logger = logging.getLogger(__name__)

DEFINITIONS_DIR = Path(__file__).parent / "definitions"

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_VALID_ASYNC = {"none", "celery"}


class SubAgentError(RuntimeError):
    """子 Agent 定义加载/校验失败。"""


class SubAgentDefinition(BaseModel):
    """一条子 Agent 注册表记录（YAML 与 DB 行同构）。"""

    name: str
    description: str = Field(min_length=1)
    subject_routing: dict[str, str] = Field(default_factory=dict)
    tools_allowlist: list[str] = Field(default_factory=list)
    async_via: str = "none"  # none=进程内同步；celery=投递异步任务
    timeout_s: int = Field(default=30, gt=0, le=600)
    card_type: Optional[str] = None
    input_form: Optional[list[dict[str, Any]]] = None
    role_required: Optional[str] = None  # 扩展字段：派发门槛（如 teacher）
    version: int = 1
    is_active: bool = True

    @field_validator("name")
    @classmethod
    def _name_snake(cls, value: str) -> str:
        if not _NAME_RE.match(value or ""):
            raise ValueError(f"子 Agent name 需 snake_case: {value!r}")
        return value

    @field_validator("async_via")
    @classmethod
    def _async_via_valid(cls, value: str) -> str:
        if value not in _VALID_ASYNC:
            raise ValueError(f"async_via 只能是 none/celery: {value!r}")
        return value

    @field_validator("tools_allowlist")
    @classmethod
    def _allowlist_names(cls, value: list[str]) -> list[str]:
        for name in value:
            if not _NAME_RE.match(name or ""):
                raise ValueError(f"tools_allowlist 含非法工具名: {name!r}")
        return value


class SubAgentRegistry:
    """子 Agent 注册表：按名查找、列出可用项。"""

    def __init__(self, definitions: dict[str, SubAgentDefinition]) -> None:
        self._defs = dict(definitions)

    def get(self, name: str) -> SubAgentDefinition:
        try:
            return self._defs[name]
        except KeyError:
            raise SubAgentError(f"未注册的子 Agent: {name}") from None

    def maybe_get(self, name: str) -> Optional[SubAgentDefinition]:
        return self._defs.get(name)

    def names(self) -> list[str]:
        return sorted(self._defs)

    def active(self) -> list[SubAgentDefinition]:
        return [d for d in self._defs.values() if d.is_active]


def _parse_definition(raw: Any, *, source: str) -> SubAgentDefinition:
    if not isinstance(raw, dict):
        raise SubAgentError(f"{source}: 定义必须是映射，得到 {type(raw).__name__}")
    try:
        return SubAgentDefinition.model_validate(raw)
    except ValidationError as exc:
        raise SubAgentError(f"{source}: 定义校验失败: {exc}") from exc


def load_definitions_dir(path: Optional[Path] = None) -> dict[str, SubAgentDefinition]:
    """加载目录下全部 *.yaml 定义（任一非法即整体拒绝，启动期暴露）。"""
    directory = path or DEFINITIONS_DIR
    definitions: dict[str, SubAgentDefinition] = {}
    for file in sorted(directory.glob("*.yaml")):
        try:
            raw = yaml.safe_load(file.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise SubAgentError(f"{file.name}: YAML 解析失败: {exc}") from exc
        definition = _parse_definition(raw, source=file.name)
        if definition.name in definitions:
            raise SubAgentError(f"子 Agent 重复定义: {definition.name}")
        definitions[definition.name] = definition
    return definitions


def merge_db_rows(
    base: dict[str, SubAgentDefinition],
    rows: list[dict[str, Any]],
) -> dict[str, SubAgentDefinition]:
    """DB 行覆盖/新增 YAML 基线；is_active=false 的行下架同名定义。"""
    merged = dict(base)
    for row in rows:
        definition = _parse_definition(row, source="sub_agent_definitions")
        if definition.is_active:
            # 安全网：role_required 不在 §六 DDL 列内，DB 行覆盖时保留 YAML 基线门槛
            baseline = merged.get(definition.name)
            if baseline is not None and baseline.role_required and not row.get("role_required"):
                definition = definition.model_copy(
                    update={"role_required": baseline.role_required}
                )
            merged[definition.name] = definition
        else:
            merged.pop(definition.name, None)
    return merged


async def _fetch_db_rows(session_factory: Callable[[], Any]) -> list[dict[str, Any]]:
    """读 sub_agent_definitions 表（文本 SQL，表不存在时抛错由调用方兜底）。"""
    from sqlalchemy import text

    async with session_factory() as session:
        result = await session.execute(
            text(
                "SELECT name, description, subject_routing, tools_allowlist, "
                "async_via, timeout_s, card_type, input_form, version, is_active "
                "FROM sub_agent_definitions"
            )
        )
        rows = result.all() if hasattr(result, "all") else []
    return [dict(row._mapping) if hasattr(row, "_mapping") else dict(row) for row in rows]


def load_registry(
    *,
    definitions_dir: Optional[Path] = None,
    db_rows: Optional[list[dict[str, Any]]] = None,
) -> SubAgentRegistry:
    """同步入口：YAML 基线 +（可选）DB 行合并。"""
    definitions = load_definitions_dir(definitions_dir)
    if db_rows:
        definitions = merge_db_rows(definitions, db_rows)
    return SubAgentRegistry(definitions)


async def load_registry_async(
    *,
    definitions_dir: Optional[Path] = None,
    session_factory: Optional[Callable[[], Any]] = None,
) -> SubAgentRegistry:
    """异步入口（DB 双读预留）：提供 session_factory 时尝试合并 DB 行。

    读表失败（表未建/连接异常）fail-soft 回退纯 YAML 基线，仅记日志。
    """
    definitions = load_definitions_dir(definitions_dir)
    if session_factory is not None:
        try:
            rows = await _fetch_db_rows(session_factory)
            definitions = merge_db_rows(definitions, rows)
        except Exception:  # noqa: BLE001 - 注册表以 YAML 为准，DB 为增量
            logger.warning("sub_agent_definitions 读表失败，回退 YAML 基线", exc_info=True)
    return SubAgentRegistry(definitions)
