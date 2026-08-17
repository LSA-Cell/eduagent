"""ocr_extract 工具：图片/文件的 OCR 与公式识别（只读）。

经 ctx.deps.multimodal（MultimodalClient 协议）HTTP 调用多模态服务；
编排层不 import 任何 OCR 实现（红线），测试注入 fake 客户端即可。
无写副作用：is_read_only=True 且可并发。
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field, model_validator

from app.tools.base import EduToolBase, ToolContext, ToolResult


class OcrExtractArgs(BaseModel):
    image_url: Optional[str] = Field(default=None, description="图片地址（MinIO 预签名 URL）")
    file_url: Optional[str] = Field(default=None, description="文件地址（PDF/图片等）")
    prompt: Optional[str] = Field(default=None, description="识别重点提示（可选）")

    @model_validator(mode="after")
    def _need_one_source(self) -> "OcrExtractArgs":
        if not (self.image_url or self.file_url):
            raise ValueError("image_url 与 file_url 至少提供一个")
        return self


class OcrExtractTool(EduToolBase):
    """OCR 文字/公式提取（只读）：返回识别文本给模型。"""

    name = "ocr_extract"
    description = "识别图片或文件中的文字与数学公式（LaTeX），返回纯文本结果。"
    input_model = OcrExtractArgs
    is_read_only = True
    is_concurrency_safe = True
    timeout_s = 20
    max_result_chars = 20_000  # 超限由执行器瘦身（全文存 MinIO）

    async def call(self, args: OcrExtractArgs, ctx: ToolContext) -> ToolResult:
        client = getattr(ctx.deps, "multimodal", None)
        if client is None:
            return ToolResult(content="OCR 服务未配置（deps.multimodal 缺失），请改用文字描述题目。")
        result = await client.ocr_extract(
            image_url=args.image_url, file_url=args.file_url, prompt=args.prompt
        )
        text = str(result.get("text") or "").strip()
        latex = [str(b) for b in result.get("latex_blocks") or []]
        parts = [text] if text else []
        if latex:
            parts.append("公式：" + "；".join(latex))
        content = "\n".join(parts) or "（未识别到文字内容，请确认图片清晰度）"
        return ToolResult(
            content=content,
            card_data={"chars": len(content), "latex_blocks": len(latex)},
        )
