"""隐私门禁（方案 §10.8）。

视觉外发前对姓名、学号、学校、二维码、联系方式区域做本地遮挡；
教师可预览将发送的页面。原始身份映射只留本地，不随图像外发。
不把图像 Base64 / OCR 原文 / 完整 Provider 响应写入普通日志。
"""

from __future__ import annotations

import re

from PIL import Image, ImageDraw

# 简单规则：匹配常见隐私字段附近的文本块（供遮挡坐标计算）。
# 真实 PII 检测应由视觉 Provider 返回 bbox；这里提供确定性兜底。
PII_PATTERNS = [
    re.compile(r"姓名[：:\s]*([\u4e00-\u9fa5]{2,4})"),
    re.compile(r"学号[：:\s]*([0-9A-Za-z]{4,})"),
    re.compile(r"学校[：:\s]*([\u4e00-\u9fa5]{2,12})"),
    re.compile(r"(1[3-9]\d{9})"),  # 手机号
]


def redact_image(path: str, regions: list[list[float]] | None = None) -> str:
    """对图像做矩形遮挡，返回遮挡后路径。

    ``regions`` 为归一化 bbox 列表 [x1,y1,x2,y2]（0..1）；
    若为空，则对整图不做自动化遮挡（交由教师人工确认）。
    占位实现：对给定区域填充黑色块。
    """
    out_path = f"{path}.redacted.jpg"
    with Image.open(path) as im:
        im = im.convert("RGB")
        w, h = im.size
        draw = ImageDraw.Draw(im)
        for bbox in regions or []:
            x1, y1, x2, y2 = [c for c in bbox]
            draw.rectangle(
                [int(x1 * w), int(y1 * h), int(x2 * w), int(y2 * h)],
                fill=(0, 0, 0),
            )
        im.save(out_path, "JPEG", quality=85)
    return out_path


def detect_pii_regions(text: str) -> list[list[float]]:
    """从 OCR 文本中识别疑似 PII（占位：返回空，需视觉 Provider 提供 bbox）。

    规则匹配仅用于日志告警标记，不直接产生坐标；真实坐标来自 Provider。
    """
    found = []
    for pat in PII_PATTERNS:
        if pat.search(text or ""):
            found.append(pat.pattern)
    # 返回空 bbox 列表（不自动遮挡），由教师预览决策；仅用于审计提示。
    return found
