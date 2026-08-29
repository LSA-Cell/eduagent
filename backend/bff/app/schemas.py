"""Pydantic v2 请求/响应模型。"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

# 中国大陆手机号
PhoneStr = Annotated[str, StringConstraints(pattern=r"^1[3-9]\d{9}$")]
# 设置密码统一规则：8-128 位（前后端一致；登录入参不限长度，只做非空校验）
PasswordStr = Annotated[str, StringConstraints(min_length=8, max_length=128)]

ProviderName = Literal["openai", "deepseek", "claude", "gemini"]
UserMode = Literal["student", "teacher", "parent"]
SmsSceneName = Literal["register", "login", "reset"]
OAuthProviderName = Literal["wechat", "apple"]


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
    password: PasswordStr
    # SMS_VERIFY_REGISTER=true 时必传（服务端校验）；false 时可省略
    sms_code: str | None = Field(default=None, max_length=8)
    email: str | None = Field(default=None, max_length=255)
    grade_level: str | None = None
    default_subject: str | None = None
    device_id: str | None = Field(default=None, max_length=64)
    device_name: str | None = Field(default=None, max_length=100)


class LoginRequest(BaseModel):
    phone: PhoneStr
    password: str = Field(min_length=1, max_length=128)
    device_id: str | None = Field(default=None, max_length=64)
    device_name: str | None = Field(default=None, max_length=100)
    # 连续失败达到阈值后由服务端要求图形验证码
    captcha_id: str | None = Field(default=None, max_length=64)
    captcha_code: str | None = Field(default=None, max_length=8)


class GuestLoginRequest(BaseModel):
    """游客登录：设备 ID 换令牌，同设备重进找回同一游客账号。"""

    device_id: str = Field(min_length=8, max_length=64)
    device_name: str | None = Field(default=None, max_length=100)


class GuestUpgradeRequest(BaseModel):
    """游客转正：绑定手机号 + 密码（需短信验证码），保留游客期间全部数据。"""

    phone: PhoneStr
    password: PasswordStr
    sms_code: str = Field(min_length=4, max_length=8)


class SmsCodeRequest(BaseModel):
    phone: PhoneStr
    scene: SmsSceneName


class SmsLoginRequest(BaseModel):
    """短信验证码登录；手机号未注册时自动建号（密码后补）。"""

    phone: PhoneStr
    code: str = Field(min_length=4, max_length=8)
    device_id: str | None = Field(default=None, max_length=64)
    device_name: str | None = Field(default=None, max_length=100)


class EmailLoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=1, max_length=128)
    device_id: str | None = Field(default=None, max_length=64)
    device_name: str | None = Field(default=None, max_length=100)


class OAuthLoginRequest(BaseModel):
    """第三方登录：微信传 code，Apple 传 identity_token；dev/test 可用 dev_openid。"""

    code: str | None = Field(default=None, max_length=256)
    identity_token: str | None = Field(default=None, max_length=4096)
    dev_openid: str | None = Field(default=None, max_length=128)
    device_id: str | None = Field(default=None, max_length=64)
    device_name: str | None = Field(default=None, max_length=100)


class PasswordChangeRequest(BaseModel):
    old_password: str = Field(min_length=1, max_length=128)
    new_password: PasswordStr


class PasswordResetRequest(BaseModel):
    """找回密码：短信验证码核验通过后重置，并吊销全部设备会话。"""

    phone: PhoneStr
    sms_code: str = Field(min_length=4, max_length=8)
    new_password: PasswordStr


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int  # access token 有效期（秒）


class DeviceSessionOut(BaseModel):
    sid: str
    device_id: str
    device_name: str
    created_at: int
    last_seen: int
    current: bool  # 是否当前请求所用会话


class CaptchaResponse(BaseModel):
    captcha_id: str
    svg: str
    expires_in: int


class SmsCodeResponse(BaseModel):
    expires_in: int
    dev_code: str | None = None  # 仅 dev/test 环境返回


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    phone: str | None  # 游客/纯第三方账号无手机号
    email: str | None
    grade_level: str | None
    default_subject: str | None
    is_teacher: bool
    is_paid: bool
    is_guest: bool = False
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


class ChatMessageIn(BaseModel):
    role: str
    content: Any  # str 或多模态分段数组，原样转发给推理网关


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
