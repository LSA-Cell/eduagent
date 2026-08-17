"""PPT 风格模板元数据：minimal / academic / lively 三种风格的配色/字体/版式常量。

不依赖物理 .pptx 模板文件，渲染器按这里的常量用 python-pptx 代码构建版式。
后续若引入物理模板文件，可在此登记模板文件在 MINIO_BUCKET_TEMPLATES 桶中的 key。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class StyleTheme:
    """风格主题常量。颜色为 RGB 十六进制（不含 #）。"""

    name: str
    bg_color: str
    title_color: str
    body_color: str
    accent_color: str
    title_font: str
    body_font: str
    title_size_pt: int
    body_size_pt: int
    bullet_char: str = "•"
    extra: dict = field(default_factory=dict)


STYLES: dict[str, StyleTheme] = {
    # 极简：白底黑字，蓝色点缀，无衬线字体
    "minimal": StyleTheme(
        name="minimal",
        bg_color="FFFFFF",
        title_color="1A1A1A",
        body_color="333333",
        accent_color="2B6CB0",
        title_font="Microsoft YaHei",
        body_font="Microsoft YaHei",
        title_size_pt=30,
        body_size_pt=16,
        bullet_char="•",
    ),
    # 学术：米白底深海军蓝标题，衬线风格
    "academic": StyleTheme(
        name="academic",
        bg_color="FAF8F2",
        title_color="1F3864",
        body_color="262626",
        accent_color="8C6D1F",
        title_font="SimSun",
        body_font="SimSun",
        title_size_pt=28,
        body_size_pt=15,
        bullet_char="◆",
    ),
    # 活泼：浅色渐变感（亮底），橙红点缀，圆体倾向
    "lively": StyleTheme(
        name="lively",
        bg_color="FFF9EC",
        title_color="D5541A",
        body_color="404040",
        accent_color="F2A93B",
        title_font="Microsoft YaHei",
        body_font="Microsoft YaHei",
        title_size_pt=32,
        body_size_pt=17,
        bullet_char="★",
    ),
}

DEFAULT_STYLE = "minimal"


def get_style(name: str) -> StyleTheme:
    """取风格主题，未知名称回退默认风格。"""
    return STYLES.get(name, STYLES[DEFAULT_STYLE])
