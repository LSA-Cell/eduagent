"""路由公共工具：multipart 文件与 URL 两种输入的统一取字节逻辑。"""

from __future__ import annotations

import httpx
from fastapi import HTTPException, UploadFile, status

DOWNLOAD_TIMEOUT = 30.0


async def resolve_bytes(upload: UploadFile | None, url: str | None, what: str) -> bytes:
    """从 multipart 文件或 URL 取得字节流，两者必须且只能有其一。"""
    if upload is not None:
        data = await upload.read()
        if not data:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"上传的{what}为空")
        return data
    if url:
        try:
            async with httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                return resp.content
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
