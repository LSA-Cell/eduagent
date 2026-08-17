"""WebSocket /ws/chat：echo / ping / chat（非流式转发推理网关占位）。

鉴权：连接 query 参数 token=<access_token>，无效则以 4401 关闭。
消息协议（JSON）：
- {"type": "ping"} -> {"type": "pong"}
- {"type": "echo", ...} -> {"type": "echo", "data": ...}
- {"type": "chat", "model"?, "subject"?, "messages": [...]} -> {"type": "chat", "content", "raw"}
"""

from __future__ import annotations

import httpx
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from eduagent_shared.config import get_settings
from eduagent_shared.logging import get_logger
from eduagent_shared.security.jwt import TokenError, decode_token

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
            body = {
                "model": data.get("model") or "edu-general-14b",
                "messages": data.get("messages") or [],
                "stream": False,
                "user_id": user_id,
            }
            if data.get("subject"):
                body["subject"] = data["subject"]
            try:
                resp = await client.post(
                    f"{settings.inference_gateway_url}/v1/chat/completions",
                    json=body,
                    headers={"X-Internal-Token": settings.internal_service_token},
                    timeout=120.0,
                )
                payload = resp.json()
                content = payload["choices"][0]["message"]["content"]
                await ws.send_json({"type": "chat", "content": content, "raw": payload})
            except (httpx.HTTPError, ValueError, KeyError, IndexError) as exc:
                log.warning("ws_chat_forward_error", error=str(exc))
                await ws.send_json({"type": "error", "message": "推理服务暂不可用"})
        else:  # echo
            await ws.send_json({"type": "echo", "data": data})
