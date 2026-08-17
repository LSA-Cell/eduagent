"""pytest 全局配置：测试环境变量（须在 app 导入前生效）。

共享包 eduagent_shared 的令牌校验无开发模式放行，因此测试统一携带
X-Internal-Token: test-internal-token；REDIS_URL 置空以使用内存任务表。
"""

from __future__ import annotations

import os

os.environ.setdefault("INTERNAL_SERVICE_TOKEN", "test-internal-token")
os.environ.setdefault("REDIS_URL", "")

TEST_HEADERS = {"X-Internal-Token": "test-internal-token"}
