"""WebSocket /ws/chat：echo / ping / chat（经 Agent 编排层的非流式问答）。

鉴权：连接 query 参数 token=<access_token>，无效则以 4401 关闭。
消息协议（JSON）：
- {"type": "ping"} -> {"type": "pong"}
- {"type": "echo", ...} -> {"type": "echo", "data": ...}
- {"type": "chat", "model"?, "subject"?, "messages": [...]} -> {"type": "chat", "content", "raw"}

红线：与 HTTP 聊天同一入口——一律经 Agent 编排层，禁止直连推理网关。
"""

from __future__ import annotations

import httpx
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from eduagent_shared.config import get_settings
from eduagent_shared.logging import get_logger
from eduagent_shared.security.jwt import TokenError, decode_token

from .services import agent_chat

router = APIRouter()
log = get_logger("bff.ws")


@router.websocket("/ws/chat")
async def ws_chat(ws: WebSocket) -> None:
    try:
        claims = decode_token(ws.query_params.get("token") or "", expected_type="access")
        user_id = str(claims["sub"])
    except (TokenError, KeyError):
        await ws.close(code=4401)
        return

    await ws.accept()
    settings = get_settings()
    client: httpx.AsyncClient = ws.app.state.http_client

    while True:
        try:
            data = await ws.receive_json()
        except WebSocketDisconnect:
            break
        except ValueError:
            await ws.send_json({"type": "error", "message": "消息必须是 JSON"})
            continue

        mtype = data.get("type", "echo")
        if mtype == "ping":
            await ws.send_json({"type": "pong"})
        elif mtype == "chat":
            messages = data.get("messages") or []
            message = agent_chat.last_user_text(messages)
            body = {
                "user_id": user_id,
                "message": message,
                "model": data.get("model"),
                "subject": data.get("subject"),
            }
            try:
                upstream = await client.send(
                    client.build_request(
                        "POST",
                        f"{settings.agent_orchestrator_url}/v1/chat/stream",
                        json=body,
                        headers={
                            "X-Internal-Token": settings.internal_service_token,
                            "X-User-Id": user_id,
                        },
                    ),
                    stream=True,
                )
                try:
                    if upstream.status_code != 200:
                        await upstream.aread()
                        await ws.send_json({"type": "error", "message": "编排服务暂不可用"})
                        continue
                    completion = await agent_chat.collect_completion(
                        upstream.aiter_raw(),
                        model=data.get("model") or "edu-general-14b",
                    )
                finally:
                    await upstream.aclose()
                content = completion["choices"][0]["message"]["content"]
                await ws.send_json({"type": "chat", "content": content, "raw": completion})
            except (httpx.HTTPError, ValueError, KeyError, IndexError) as exc:
                log.warning("ws_chat_forward_error", error=str(exc))
                await ws.send_json({"type": "error", "message": "编排服务暂不可用"})
        else:  # echo
            await ws.send_json({"type": "echo", "data": data})
