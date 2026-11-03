#!/usr/bin/env python3
"""EduAgent 后端联调冒烟测试（真实 docker compose 环境）。

前置条件：
  docker compose up -d bff agent-worker  # dev/test，相关服务健康；不启动 beat/GPU
  导出与服务一致的 INTERNAL_SERVICE_TOKEN（脚本不会读取或打印 .env 密钥）

覆盖链路：
  1. 七服务 /health
  2. BFF 鉴权链：sms-code(dev_code) -> register -> login -> refresh -> users/me
  3. BFF 会话链：创建/列出 chat sessions、消息列表
  4. 内部服务直连：gateway /v1/models、multimodal /ocr（mock provider）、
     docgen 渲染任务 -> MinIO 预签名 URL
  5. 鉴权负例：无/错 X-Internal-Token -> 401/403
  6. 本地推理不可用链（强制 internal、不调用付费外部模型）：chat/completions -> 502

用法：python3 backend/scripts/smoke_integration.py [--base http://localhost]
仅依赖标准库；会创建专用测试账号/会话/文档，仅用于隔离的 dev/test 数据库。
MinIO 检查确认任务成功并返回 ≤1h 的签名 URL，不替代真实文件下载验收。
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request
import uuid
from urllib.parse import parse_qs, urlsplit

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
    except (urllib.error.URLError, TimeoutError, socket.timeout):
        return 0, "连接失败或请求超时"


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
    except (urllib.error.URLError, TimeoutError, socket.timeout):
        return 0, "连接失败或请求超时"


def valid_presigned_url(value: object) -> bool:
    """验证任务结果的签名地址及过期时间；签名本身不输出到日志。"""
    if not isinstance(value, str):
        return False
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    query = parse_qs(parsed.query)
    try:
        expires = int(query.get("X-Amz-Expires", ["0"])[0])
    except ValueError:
        return False
    return (
        parsed.scheme in ("http", "https")
        and bool(parsed.netloc and parsed.path)
        and bool(query.get("X-Amz-Signature", [""])[0])
        and 0 < expires <= 3600
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost")
    ap.add_argument("--docker-network", action="store_true",
                    help="在本项目容器内按既有 Compose 服务名访问")
    args = ap.parse_args(argv)
    internal_token = os.environ.get("INTERNAL_SERVICE_TOKEN", "")
    if not internal_token:
        ap.error("请先设置 INTERNAL_SERVICE_TOKEN（须与 dev/test 服务配置一致）")
    results.clear()
    base = args.base.rstrip("/")
    ports = {"bff": 8000, "gateway": 8001, "agent": 8002,
             "multimodal": 8003, "proxy": 8004, "docgen": 8005, "billing": 8006}
    url = {name: f"http://{name}:{port}" if args.docker_network else f"{base}:{port}"
           for name, port in ports.items()}
    tok = {"X-Internal-Token": internal_token}

    # ---------- 1. 健康检查 ----------
    for name in ports:
        code, _ = req("GET", f"{url[name]}/health")
        record(f"health/{name}", PASS if code == 200 else FAIL, f"HTTP {code}")

    # ---------- 2. BFF 鉴权链（真实 PostgreSQL） ----------
    phone = "139" + "".join(str(uuid.uuid4().int % 10) for _ in range(8))
    password = "smoke-test-123"
    code, body = req("POST", f"{url['bff']}/api/v1/auth/sms-code",
                     {"phone": phone, "scene": "register"})
    if code != 200 or not isinstance(body, dict) or not body.get("dev_code"):
        record("bff/sms_code", FAIL,
               f"HTTP {code}; 需要 dev/test 的 dev_code，禁止在生产环境运行此脚本")
        return summarize()
    sms_code = body["dev_code"]
    record("bff/sms_code", PASS)
    code, body = req("POST", f"{url['bff']}/api/v1/auth/register",
                     {"phone": phone, "password": password, "sms_code": sms_code})
    if code == 201 and isinstance(body, dict) and "tokens" in body:
        record("bff/register", PASS)
        access = body["tokens"]["access_token"]
        refresh = body["tokens"]["refresh_token"]
    else:
        record("bff/register", FAIL, f"HTTP {code}")
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
        record("bff/refresh", FAIL, f"HTTP {code}")

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
        record("bff/create_session", FAIL, f"HTTP {code}")

    code, body = req("GET", f"{url['bff']}/api/v1/chat/sessions", headers=auth)
    record("bff/list_sessions", PASS if code == 200 else FAIL, f"HTTP {code}")

    if session_id:
        code, _ = req("GET", f"{url['bff']}/api/v1/chat/sessions/{session_id}/messages",
                      headers=auth)
        record("bff/session_messages", PASS if code == 200 else FAIL, f"HTTP {code}")

    # ---------- 4. 内部服务直连 ----------
    code, body = req("GET", f"{url['gateway']}/v1/models", headers=tok)
    n = len(body.get("data", [])) if isinstance(body, dict) else 0
    record("gateway/models", PASS if code == 200 else FAIL,
           f"HTTP {code}, {n} models")

    code, body = req_multipart(f"{url['multimodal']}/ocr", "file", "smoke.png",
                               TINY_PNG, headers=tok)
    record("multimodal/ocr", PASS if code == 200 else FAIL, f"HTTP {code}")

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
        rendered = (final or {}).get("result") or {}
        if final and final.get("status") == "completed" and isinstance(rendered, dict) and valid_presigned_url(rendered.get("file_url")):
            record("docgen/render+minio", PASS, "任务完成且返回有效期不超过 1h 的签名 URL")
        else:
            record("docgen/render+minio", FAIL, "任务超时、失败或缺少有效签名结果")
    else:
        record("docgen/submit", FAIL, f"HTTP {code}")

    # ---------- 5. 鉴权负例 ----------
    for method, name, path, expect in [
        ("POST", "gateway", "/v1/chat/completions", (403,)),
        ("POST", "agent", "/agent/chat", (401,)),
        ("POST", "proxy", "/proxy/chat", (401, 403)),
        ("POST", "multimodal", "/ocr", (401,)),
        ("POST", "docgen", "/render/lesson_plan", (401,)),
        ("GET", "billing", "/billing/plans", (401, 403)),
    ]:
        for label, headers in (("missing", None), ("wrong", {"X-Internal-Token": uuid.uuid4().hex})):
            code, _ = req(method, f"{url[name]}{path}", {} if method == "POST" else None, headers=headers)
            record(f"auth-negative/{name}/{label}", PASS if code in expect else FAIL,
                   f"HTTP {code} (expect {'/'.join(map(str, expect))})")

    # ---------- 6. 仅内部模型错误链，不触发平台或用户付费 Key ----------
    code, body = req("POST", f"{url['bff']}/api/v1/chat/completions",
                     {"model": "edu-general-14b",
                      "messages": [{"role": "user", "content": "hi"}],
                      "stream": False, "inference_source": "internal",
                      "auto_failover": False},
                     headers=auth, timeout=30)
    if code == 502 and isinstance(body, dict) and body.get("error"):
        record("bff/chat_unavailable(internal-only)", PASS,
               "HTTP 502 且返回结构化错误（未启用 vLLM；未请求外部模型）")
    elif code == 200 and isinstance(body, dict) and body.get("choices"):
        record("bff/chat_unavailable(internal-only)", PASS, "内部推理通道已可用，返回有效响应")
    else:
        record("bff/chat_unavailable(internal-only)", FAIL, f"HTTP {code}")

    return summarize()


def summarize() -> int:
    n_pass = sum(1 for _, s, _ in results if s == PASS)
    n_fail = sum(1 for _, s, _ in results if s == FAIL)
    n_skip = sum(1 for _, s, _ in results if s == SKIP)
    print(f"\n===== 冒烟结果: {n_pass} PASS / {n_fail} FAIL / {n_skip} SKIP =====")
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
