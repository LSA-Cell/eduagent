"""pytest 引导（与 proxy 服务同一 sys.path 方案）。"""

import base64
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_HERE, "..", "shared"))

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("INTERNAL_SERVICE_TOKEN", "test-internal-token")
os.environ.setdefault(
    "KEY_ENCRYPTION_MASTER_KEY",
    base64.urlsafe_b64encode(b"0" * 32).decode(),
)
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test")
os.environ.setdefault(
    "BILLING_WEBHOOK_SECRETS", '{"mock": "test-webhook-secret"}'
)
