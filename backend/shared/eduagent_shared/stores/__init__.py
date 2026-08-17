"""共享仓储层（跨服务共用的 DB 访问深模块）。

目前收纳 user_api_keys 域（原分散在 bff 与 proxy 两份重复实现）。
红线：同一张表的仓储只能有一份——新增方法在这里扩展，禁止在服务内另起炉灶。
"""

from .api_keys import (
    ApiKeyRecord,
    ApiKeyStore,
    DuplicateKeyError,
    InMemoryApiKeyStore,
    SqlAlchemyApiKeyStore,
)

__all__ = [
    "ApiKeyRecord",
    "ApiKeyStore",
    "DuplicateKeyError",
    "InMemoryApiKeyStore",
    "SqlAlchemyApiKeyStore",
]
