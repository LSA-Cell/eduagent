"""pytest 引导（本服务不做成 pip 包，采用 sys.path 方案，见 README）。

- 将 bff/ 与 shared/ 加入 sys.path，使 `import app` / `import eduagent_shared` 可用；
  若已通过 `pip install -e shared` 安装公共库，则后者不生效也无影响。
- 设置测试用环境变量，保证不依赖真实 .env。
"""

import base64
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_HERE, "..", "shared"))

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret")
os.environ.setdefault(
    "KEY_ENCRYPTION_MASTER_KEY",
    base64.urlsafe_b64encode(b"0" * 32).decode(),  # 测试用 32 字节主密钥
)
os.environ.setdefault("INTERNAL_SERVICE_TOKEN", "test-internal-token")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("BILLING_WEBHOOK_SECRETS", '{"mock": "test-webhook-secret"}')
