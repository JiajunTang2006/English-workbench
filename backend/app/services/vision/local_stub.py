"""本地占位 Provider：无任何外部 API 依赖，保证链路可测与 fail-closed。

当没有配置真实视觉 Provider 时，路由使用本 stub：对图片返回
``pending_review`` 占位结果（标记需要教师人工校对，不产生费用、不向外发送），
对纯文本/已有本地解析结果则直接复用。
"""

from __future__ import annotations

import uuid

from .provider import (
    ImageInput,
    ProviderCapabilities,
    VisionResult,
    VisionTask,
)


class LocalStubProvider:
    """零依赖的本地回退视觉 Provider。

    - 不发起任何网络请求；
    - 不产生费用；
    - 返回 ``safety_filtered=False``、``error=None`` 的占位结构，
      交由服务层标记为 ``pending_review``（需教师确认，不进正式上下文）。
    """

    capabilities = ProviderCapabilities(
        name="local_stub",
        supports_image_input=True,
        supports_pdf_input=False,  # 本地无 PDF 渲染依赖（pdf2image/fitz 缺失）
        supports_structured_output=False,
        supports_handwriting=False,
        max_images_per_request=1,
        max_image_bytes=0,
        max_total_pixels=0,
        data_retention_mode="local_only",
        price_per_page_yuan=0.0,
        price_per_image_yuan=0.0,
    )

    async def analyze(
        self,
        *,
        images: list[ImageInput],
        task: VisionTask,
        response_schema: dict,
    ) -> VisionResult:
        # 本地 stub 不调用外部服务：返回占位页级结构，明确提示需人工校对。
        pages = []
        for img in images:
            pages.append(
                {
                    "page_no": img.page_no,
                    "text": "",
                    "note": "local_stub: 未配置真实视觉 Provider，需教师人工校对原图",
                }
            )
        return VisionResult(
            provider=self.capabilities.name,
            model_name=None,
            request_id=f"local-{uuid.uuid4().hex[:12]}",
            pages=pages,
            low_confidence=[
                {
                    "page_no": img.page_no,
                    "reason": "no_vision_provider_configured",
                }
                for img in images
            ],
            cost_yuan=0.0,
            safety_filtered=False,
            error=None,
        )
