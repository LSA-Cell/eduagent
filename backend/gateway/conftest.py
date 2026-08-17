"""pytest 引导：sys.path + 测试环境变量（本服务不做成 pip 包，见 README）。"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_HERE, "..", "shared"))

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("INTERNAL_SERVICE_TOKEN", "test-internal-token")
os.environ.setdefault("API_PROXY_URL", "http://proxy:8004")
os.environ.setdefault(
    "VLLM_ROUTES",
    '{"edu-general-14b": ["http://vllm-general:8100"], '
    '"edu-math-7b": ["http://vllm-math:8101"]}',
)
os.environ.setdefault("MODEL_HEALTH_TTL_SECONDS", "30")
os.environ.setdefault("FAILOVER_ENABLED", "true")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret")
os.environ.setdefault(
    "KEY_ENCRYPTION_MASTER_KEY", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
)
