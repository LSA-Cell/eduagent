#!/usr/bin/env python3
"""EduAgent 后端联调冒烟测试（真实 docker compose 环境）。

前置条件：
  docker compose up -d   # 全部服务健康（无 GPU，vLLM 不启动）

覆盖链路：
  1. 六服务 /health
  2. BFF 鉴权链：register -> login -> refresh -> users/me（PostgreSQL 落库）
  3. BFF 会话链：创建/列出 chat sessions、消息列表
  4. 内部服务直连：gateway /v1/models、multimodal /ocr（mock provider）、
     docgen 渲染任务 -> MinIO 预签名 URL
  5. 鉴权负例：无/错 X-Internal-Token -> 401/403
  6. 推理降级链（无 GPU 预期失败但需可解释）：chat/completions -> 502/404

用法：python3 backend/scripts/smoke_integration.py [--base http://localhost]
仅依赖标准库。
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid

# 内部服务令牌必须从环境变量读取，不提供默认值（红线：代码库内不出现真实令牌）。
# 用法：export INTERNAL_SERVICE_TOKEN=<与 .env 中 INTERNAL_SERVICE_TOKEN 一致>
INTERNAL_TOKEN = os.environ.get("INTERNAL_SERVICE_TOKEN", "")

# 1x1 透明 PNG
TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"
results: list[tuple[str, str, str]] = []  # (name, status, detail)


def record(name: str, status: str, detail: str = "") -> None:
    results.append((name, status, detail))
    print(f"[{status}] {name}" + (f" — {detail}" if detail else ""))


def req(method: str, url: str, body: dict | None = None,
        headers: dict | None = None, timeout: int = 15) -> tuple[int, dict | str]:
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        r.add_header(k, v)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            raw = resp.read().decode()
            try:
                return resp.status, json.loads(raw)
            except json.JSONDecodeError:
                return resp.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, raw


def req_multipart(url: str, field: str, filename: str, content: bytes,
                  headers: dict | None = None, timeout: int = 15) -> tuple[int, dict | str]:
    boundary = "smoke-boundary-" + uuid.uuid4().hex
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'
        f"Content-Type: application/octet-stream\r\n\r\n"
    ).encode() + content + f"\r\n--{boundary}--\r\n".encode()
    r = urllib.request.Request(url, data=body, method="POST")
    r.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    for k, v in (headers or {}).items():
        r.add_header(k, v)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            raw = resp.read().decode()
            try:
                return resp.status, json.loads(raw)
            except json.JSONDecodeError:
                return resp.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, raw


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost")
    args = ap.parse_args()
    if not INTERNAL_TOKEN:
        print("[FAIL] 缺少环境变量 INTERNAL_SERVICE_TOKEN（冒烟脚本不再提供默认令牌）")
        return 2
    base = args.base.rstrip("/")
    ports = {"bff": 8000, "gateway": 8001, "agent": 8002,
             "multimodal": 8003, "proxy": 8004, "docgen": 8005}
    url = {name: f"{base}:{port}" for name, port in ports.items()}
    tok = {"X-Internal-Token": INTERNAL_TOKEN}

    # ---------- 1. 健康检查 ----------
    for name in ports:
        code, _ = req("GET", f"{url[name]}/health")
        record(f"health/{name}", PASS if code == 200 else FAIL, f"HTTP {code}")

    # ---------- 2. BFF 鉴权链（真实 PostgreSQL） ----------
    phone = "139" + "".join(str(uuid.uuid4().int % 10) for _ in range(8))
    password = os.getenv("SMOKE_TEST_PASSWORD", "smoke-test-123")
    code, body = req("POST", f"{url['bff']}/api/v1/auth/register",
                     {"phone": phone, "password": password})
    if code == 201 and isinstance(body, dict) and "tokens" in body:
        record("bff/register", PASS, f"phone={phone}")
        access = body["tokens"]["access_token"]
        refresh = body["tokens"]["refresh_token"]
    else:
        record("bff/register", FAIL, f"HTTP {code}: {str(body)[:200]}")
        return summarize()  # 后续链路都依赖令牌，直接终止

    code, body = req("POST", f"{url['bff']}/api/v1/auth/login",
                     {"phone": phone, "password": password})
    record("bff/login", PASS if code == 200 else FAIL, f"HTTP {code}")

    code, body = req("POST", f"{url['bff']}/api/v1/auth/refresh",
                     {"refresh_token": refresh})
    if code == 200 and isinstance(body, dict):
        record("bff/refresh", PASS)
        access = body.get("access_token", access)
    else:
        record("bff/refresh", FAIL, f"HTTP {code}: {str(body)[:200]}")

    auth = {"Authorization": f"Bearer {access}"}
    code, body = req("GET", f"{url['bff']}/api/v1/users/me", headers=auth)
    ok = code == 200 and isinstance(body, dict) and body.get("phone") == phone
    record("bff/users_me", PASS if ok else FAIL, f"HTTP {code}")

    # ---------- 3. BFF 会话链 ----------
    code, body = req("POST", f"{url['bff']}/api/v1/chat/sessions",
                     {"title": "smoke 会话"}, headers=auth)
    session_id = None
    if code in (200, 201) and isinstance(body, dict):
        session_id = body.get("id") or body.get("session_id")
        record("bff/create_session", PASS, f"id={session_id}")
    else:
        record("bff/create_session", FAIL, f"HTTP {code}: {str(body)[:200]}")

    code, body = req("GET", f"{url['bff']}/api/v1/chat/sessions", headers=auth)
    record("bff/list_sessions", PASS if code == 200 else FAIL, f"HTTP {code}")

    if session_id:
        code, _ = req("GET", f"{url['bff']}/api/v1/chat/sessions/{session_id}/messages",
                      headers=auth)
        record("bff/session_messages", PASS if code == 200 else FAIL, f"HTTP {code}")

    # ---------- 4. 内部服务直连 ----------
    code, body = req("GET", f"{url['gateway']}/v1/models")
    n = len(body.get("data", [])) if isinstance(body, dict) else 0
    record("gateway/models", PASS if code == 200 else FAIL,
           f"HTTP {code}, {n} models")

    code, body = req_multipart(f"{url['multimodal']}/ocr", "file", "smoke.png",
                               TINY_PNG, headers=tok)
    record("multimodal/ocr(mock)", PASS if code == 200 else FAIL,
           f"HTTP {code}: {str(body)[:120]}")

    code, body = req("POST", f"{url['docgen']}/render/lesson_plan",
                     {"title": "二次函数复习课", "course_name": "数学",
                      "grade": "初三", "subject": "数学", "duration": "45分钟",
                      "content": {
                          "objectives": ["掌握二次函数的图象与性质"],
                          "key_points": ["顶点式与最值"],
                          "procedure": [
                              {"stage": "导入", "duration": "5分钟",
                               "teacher_activity": "复习提问一次函数",
                               "student_activity": "回答回顾"},
                              {"stage": "新授", "duration": "25分钟",
                               "teacher_activity": "讲解 y=a(x-h)²+k 的图象变换",
                               "student_activity": "动手画图象",
                               "design_intent": "数形结合"},
                          ],
                          "board_design": "顶点式 -> 开口方向 -> 最值",
                          "homework": "课本 P32 第 3、5 题",
                      }},
                     headers=tok)
    if code == 202 and isinstance(body, dict) and body.get("task_id"):
        task_id = body["task_id"]
        record("docgen/submit", PASS, f"task={task_id}")
        final = None
        for _ in range(15):
            time.sleep(2)
            c, b = req("GET", f"{url['docgen']}/tasks/{task_id}", headers=tok)
            if c == 200 and isinstance(b, dict) and b.get("status") in ("completed", "failed"):
                final = b
                break
        if final and final.get("status") == "completed":
            record("docgen/render+minio", PASS, str(final.get("file_url") or final)[:150])
        else:
            record("docgen/render+minio", FAIL, str(final or "timeout")[:200])
    else:
        record("docgen/submit", FAIL, f"HTTP {code}: {str(body)[:200]}")

    # ---------- 5. 鉴权负例 ----------
    for name, path, expect in [("gateway", "/v1/chat/completions", (403,)),
                               ("agent", "/agent/chat", (401,)),
                               ("proxy", "/proxy/chat", (401, 403)),
                               ("multimodal", "/ocr", (401,)),
                               ("docgen", "/render/lesson_plan", (401,))]:
        code, _ = req("POST", f"{url[name]}{path}", {})
        record(f"auth-negative/{name}", PASS if code in expect else FAIL,
               f"HTTP {code} (expect {'/'.join(map(str, expect))})")

    # ---------- 6. 推理降级链（无 GPU：预期 502/404，验证错误可解释） ----------
    code, body = req("POST", f"{url['bff']}/api/v1/chat/completions",
                     {"model": "edu-general-14b",
                      "messages": [{"role": "user", "content": "hi"}],
                      "stream": False},
                     headers=auth, timeout=30)
    detail = str(body)[:150]
    if code in (502, 404):
        record("bff/chat_failover(no-key)", PASS,
               f"HTTP {code} 符合预期(无 vLLM 且未绑 Key): {detail}")
    elif code == 200:
        record("bff/chat_failover(no-key)", PASS, f"意外成功? {detail}")
    else:
        record("bff/chat_failover(no-key)", FAIL, f"HTTP {code}: {detail}")

    return summarize()


def summarize() -> int:
    n_pass = sum(1 for _, s, _ in results if s == PASS)
    n_fail = sum(1 for _, s, _ in results if s == FAIL)
    n_skip = sum(1 for _, s, _ in results if s == SKIP)
    print(f"\n===== 冒烟结果: {n_pass} PASS / {n_fail} FAIL / {n_skip} SKIP =====")
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
