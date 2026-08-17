"""web_search 工具：Serper / Bing 教育资料检索（密钥来自 WEB_SEARCH_* 配置）。"""

from __future__ import annotations

from typing import Any

import httpx

from app.tools.base import ToolContext, registry

SERPER_URL = "https://google.serper.dev/search"
BING_URL = "https://api.bing.microsoft.com/v7.0/search"


def _normalize_serper(data: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {"title": item.get("title", ""), "link": item.get("link", ""),
         "snippet": item.get("snippet", "")}
        for item in data.get("organic", [])
    ]


def _normalize_bing(data: dict[str, Any]) -> list[dict[str, str]]:
    pages = (data.get("webPages") or {}).get("value") or []
    return [
        {"title": item.get("name", ""), "link": item.get("url", ""),
         "snippet": item.get("snippet", "")}
        for item in pages
    ]


async def search_web(
    query: str,
    *,
    provider: str = "serper",
    api_key: str = "",
    num: int = 5,
    timeout: float = 10.0,
) -> list[dict[str, str]]:
    """执行网页检索并归一化为 [{title, link, snippet}]；未配置密钥时抛错由调用方处理。"""
    if not api_key:
        raise RuntimeError("未配置 WEB_SEARCH_API_KEY")
    async with httpx.AsyncClient(timeout=timeout) as client:
        if provider == "bing":
            resp = await client.get(
                BING_URL,
                params={"q": query, "count": num, "mkt": "zh-CN"},
                headers={"Ocp-Apim-Subscription-Key": api_key},
            )
            resp.raise_for_status()
            return _normalize_bing(resp.json())[:num]
        # 默认 serper
        resp = await client.post(
            SERPER_URL,
            json={"q": query, "num": num, "hl": "zh-cn"},
            headers={"X-API-KEY": api_key},
        )
        resp.raise_for_status()
        return _normalize_serper(resp.json())[:num]


WEB_SEARCH_SCHEMA: dict[str, Any] = {
    "name": "web_search",
    "description": "检索教育资料（教案、试题、知识点讲解等），返回标题/链接/摘要列表。",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "检索关键词"},
            "num": {"type": "integer", "description": "返回条数，默认 5", "default": 5},
        },
        "required": ["query"],
    },
}


async def handle_web_search(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    query = str(args.get("query", "")).strip()
    if not query:
        return {"error": "缺少 query 参数"}
    settings = ctx.settings
    if settings is None:
        return {"error": "工具上下文缺少配置"}
    num = int(args.get("num") or 5)
    try:
        results = await search_web(
            query,
            provider=str(getattr(settings, "web_search_provider", "serper") or "serper"),
            api_key=str(getattr(settings, "web_search_api_key", "") or ""),
            num=max(1, min(num, 10)),
        )
    except Exception as exc:  # noqa: BLE001 - 检索失败需回填给 LLM
        return {"error": str(exc), "results": []}
    return {"query": query, "results": results}


registry.register_tool(WEB_SEARCH_SCHEMA, handle_web_search)
