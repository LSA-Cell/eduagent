"""Pydantic v2 请求/响应模型。"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from eduagent_shared.contracts import ChatMessage

# 中国大陆手机号
PhoneStr = Annotated[str, StringConstraints(pattern=r"^1[3-9]\d{9}$")]

ProviderName = Literal["openai", "deepseek", "claude", "gemini"]
UserMode = Literal["student", "teacher", "parent"]


# ---------- 通用分页 ----------


T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int


# ---------- 鉴权 ----------


class RegisterRequest(BaseModel):
    phone: PhoneStr
    password: str = Field(min_length=8, max_length=128)
    email: str | None = Field(default=None, max_length=255)
    grade_level: str | None = None
    default_subject: str | None = None


class LoginRequest(BaseModel):
    phone: PhoneStr
    password: str = Field(min_length=1, max_length=128)


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int  # access token 有效期（秒）


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    phone: str
    email: str | None
    grade_level: str | None
    default_subject: str | None
    is_teacher: bool
    is_paid: bool
    user_mode: str
    created_at: datetime


class RegisterResponse(BaseModel):
    user: UserOut
    tokens: TokenResponse


class ModeUpdate(BaseModel):
    mode: UserMode


class ProfileUpdate(BaseModel):
    """资料更新（仅提交非空字段；服务端暂无 nickname/subjects 列，移动端本地保留）。"""

    grade_level: str | None = Field(default=None, max_length=50)
    default_subject: str | None = Field(default=None, max_length=50)


class TeacherCertificationRequest(BaseModel):
    """教师认证材料提交：文件先经 /kb/documents/upload-url 上传，这里只登记地址。"""

    file_url: str = Field(min_length=1, max_length=1024)
    note: str | None = Field(default=None, max_length=500)


# ---------- 用户 API Key ----------


class ApiKeyCreate(BaseModel):
    provider: ProviderName
    api_key: str = Field(min_length=8, max_length=256)
    monthly_budget: int | None = Field(default=None, ge=0, description="月度预算（USD 美分），None 表示不限额")


class ApiKeyOut(BaseModel):
    """Key 出参：只含掩码后 4 位，绝不明文回显。"""

    id: uuid.UUID
    provider: str
    masked_key: str
    monthly_budget: int | None
    used_budget: int
    is_active: bool
    created_at: datetime


# ---------- 聊天会话与消息 ----------


class SessionCreate(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    subject: str | None = None
    model: str | None = None
    inference_source: Literal["internal", "external"] | None = None


class SessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str | None
    subject: str | None
    model: str | None
    inference_source: str | None
    created_at: datetime
    updated_at: datetime


class ChatMessageIn(ChatMessage):
    """BFF 侧聊天消息（契约统一在 eduagent_shared.contracts，此处仅保留本服务命名）。"""


class ChatCompletionRequest(BaseModel):
    """BFF 侧聊天入参；user_id 由服务端注入，客户端不可伪造。"""

    session_id: uuid.UUID | None = None
    model: str | None = None
    messages: list[ChatMessageIn]
    stream: bool = True
    subject: str | None = None
    inference_source: Literal["auto", "internal", "external"] = "auto"
    provider: ProviderName | None = None
    auto_failover: bool = True
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None


class MessageOut(BaseModel):
    """消息出参；ORM/内存记录属性名为 metadata_，对外序列化为 metadata。"""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: uuid.UUID
    session_id: uuid.UUID
    parent_id: uuid.UUID | None
    role: str
    content_type: str
    content: str
    metadata_: dict = Field(default_factory=dict, serialization_alias="metadata")
    created_at: datetime
