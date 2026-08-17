"""路由公共工具：multipart 文件与 URL 两种输入的统一取字节逻辑。"""

from __future__ import annotations

from urllib.parse import urlparse

import httpx
from fastapi import HTTPException, UploadFile, status

DOWNLOAD_TIMEOUT = 30.0
# 与 agent 知识库上限对齐（kb.py: 50MB），防止大文件/恶意 URL 打爆内存
MAX_FILE_BYTES = 50 * 1024 * 1024

# URL 输入仅允许 http/https（SSRF 基础防线：挡掉 file://、gopher:// 等 scheme）。
# 注意：内网 MinIO 预签名 URL 本身是合法输入，故不在此处做私网 IP 过滤；
# 调用方不得将终端用户可控的 URL 直接透传进来。
_ALLOWED_SCHEMES = {"http", "https"}


def _too_large(what: str) -> HTTPException:
    return HTTPException(
        status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
        f"{what}超过大小上限（{MAX_FILE_BYTES // 1024 // 1024}MB）",
    )


async def resolve_bytes(upload: UploadFile | None, url: str | None, what: str) -> bytes:
    """从 multipart 文件或 URL 取得字节流，两者必须且只能有其一。"""
    if upload is not None:
        data = await upload.read(MAX_FILE_BYTES + 1)
        if not data:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"上传的{what}为空")
        if len(data) > MAX_FILE_BYTES:
            raise _too_large(what)
        return data
    if url:
        if urlparse(url).scheme.lower() not in _ALLOWED_SCHEMES:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, f"{what} URL 仅支持 http/https"
            )
        try:
            async with httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT) as client:
                async with client.stream("GET", url) as resp:
                    resp.raise_for_status()
                    content_length = resp.headers.get("content-length")
                    if content_length and int(content_length) > MAX_FILE_BYTES:
                        raise _too_large(what)
                    chunks: list[bytes] = []
                    total = 0
                    async for chunk in resp.aiter_bytes(1 << 20):
                        total += len(chunk)
                        if total > MAX_FILE_BYTES:
                            raise _too_large(what)
                        chunks.append(chunk)
                    data = b"".join(chunks)
                    if not data:
                        raise HTTPException(
                            status.HTTP_400_BAD_REQUEST, f"下载的{what}为空"
                        )
                    return data
        except HTTPException:
            raise
        except httpx.HTTPError as exc:
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY, f"下载{what}失败: {exc}"
            ) from exc
    raise HTTPException(
        status.HTTP_400_BAD_REQUEST, f"必须提供 multipart 文件或 {what} URL"
    )


def guess_file_type(filename: str | None) -> str:
    """从文件名推断文档类型。"""
    if filename and "." in filename:
        return filename.rsplit(".", 1)[-1].lower()
    return ""
