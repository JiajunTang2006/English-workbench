"""视觉模型适配器。

支持 OpenAI-compatible 多模态端点和腾讯云 GeneralBasicOCR，统一返回
Agent 的 ``ModelResponse``，供附件 OCR、试卷录入和编排器复用。
"""

from __future__ import annotations

import base64
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from .base import (
    ModelError,
    ModelErrorType,
    ModelResponse,
    ModelUsage,
    VisionModelProvider,
)

if TYPE_CHECKING:
    from ..config import AgentConfig
    from ..cost import CostEstimate, CostEstimator

logger = logging.getLogger(__name__)

class GenericVisionProvider(VisionModelProvider):
    """通用 OpenAI-compatible 多模态适配器。"""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model_name: str = "generic-vision",
        timeout: int = 60,
        max_retries: int = 3,
        endpoint_path: str | None = None,
        thinking_enabled: bool = False,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url
        self._model_name = model_name
        self._timeout = timeout
        # 与文本模型一致：该配置表示首次请求之后的最大重试次数。
        self._max_attempts = max(0, int(max_retries)) + 1
        self._endpoint_path = endpoint_path
        self._thinking_enabled = thinking_enabled

    async def analyze_images(
        self,
        image_urls: list[str],
        prompt: str,
        *,
        max_tokens: int = 4096,
        temperature: float = 0.0,
    ) -> ModelResponse:
        """调用 OpenAI-compatible ``chat/completions`` 视觉端点。"""
        if not self._api_key or not self._base_url:
            raise ModelError("视觉模型未配置 API Key 或 Base URL", ModelErrorType.AUTH_FAILED)
        import httpx
        content: list[dict] = [{"type": "text", "text": prompt}]
        for image in image_urls:
            path = Path(image)
            try:
                encoded = base64.b64encode(path.read_bytes()).decode("ascii")
            except OSError as exc:
                raise ModelError("视觉输入文件不可读", ModelErrorType.INVALID_RESPONSE) from exc
            mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}.get(path.suffix.lower(), "image/jpeg")
            content.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}})
        base = self._base_url.rstrip("/")
        endpoint_path = self._endpoint_path or (
            "/chat/completions" if base.endswith("/v1") else "/v1/chat/completions"
        )
        url = base if base.endswith("/chat/completions") else base + endpoint_path
        payload = {
            "model": self._model_name,
            "messages": [{"role": "user", "content": content}],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if self._thinking_enabled:
            payload["thinking"] = {"type": "enabled"}
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = None
                for attempt in range(self._max_attempts):
                    response = await client.post(url, headers={"Authorization": f"Bearer {self._api_key}"}, json=payload)
                    if response.status_code not in {429, 500, 502, 503, 504} or attempt + 1 >= self._max_attempts:
                        break
                assert response is not None
            if response.status_code == 401:
                raise ModelError("视觉 API 鉴权失败", ModelErrorType.AUTH_FAILED)
            if response.status_code == 429:
                raise ModelError("视觉 API 请求频率受限", ModelErrorType.RATE_LIMIT)
            if response.status_code == 402:
                raise ModelError("视觉 API 余额不足", ModelErrorType.INSUFFICIENT_BALANCE)
            if response.status_code >= 400:
                raise ModelError(f"视觉 API 返回 HTTP {response.status_code}", ModelErrorType.NETWORK)
            body = response.json()
            choices = body.get("choices") or []
            message = choices[0].get("message", {}) if choices else {}
            text = message.get("content", "")
            if isinstance(text, list):
                text = "\n".join(str(part.get("text", "")) for part in text if isinstance(part, dict))
            usage_data = body.get("usage") or {}
            usage = ModelUsage(input_tokens=int(usage_data.get("prompt_tokens", 0) or 0), output_tokens=int(usage_data.get("completion_tokens", 0) or 0), provider_request_id=response.headers.get("x-request-id"))
            return ModelResponse(content=str(text), usage=usage, finish_reason=str((choices[0] if choices else {}).get("finish_reason", "stop")))
        except ModelError:
            raise
        except httpx.TimeoutException as exc:
            raise ModelError("视觉 API 请求超时", ModelErrorType.TIMEOUT) from exc
        except (httpx.HTTPError, json.JSONDecodeError, ValueError) as exc:
            raise ModelError("视觉 API 响应无效", ModelErrorType.INVALID_RESPONSE) from exc

    def estimate_cost(
        self,
        image_count: int,
        *,
        max_tokens: int = 4096,
    ) -> "CostEstimate":
        """估算视觉分析的成本。"""
        from ..cost import CostEstimate

        return CostEstimate(
            input_tokens=0,
            output_tokens=max_tokens,
            estimated_cost_yuan=0.0,
            model_name=self._model_name,
            image_count=image_count,
        )

    def get_capabilities(self) -> dict:
        """返回该视觉模型支持的能力。"""
        return {
            "model": self._model_name,
            "supports_ocr": True,
            "supports_handwriting": True,
            "supports_table_extraction": True,
            "max_images_per_request": 10,
            "supported_formats": ["png", "jpg", "jpeg", "pdf"],
        }


class TencentOCRProvider(GenericVisionProvider):
    """腾讯云 GeneralBasicOCR 适配器（仅发送单页脱敏派生图）。"""

    def __init__(self, secret_id: str | None = None, secret_key: str | None = None, **kwargs) -> None:
        super().__init__(model_name="tencent-ocr", **kwargs)
        self._secret_id = secret_id
        self._secret_key = secret_key

    async def analyze_images(
        self,
        image_urls: list[str],
        prompt: str,
        *,
        max_tokens: int = 4096,
        temperature: float = 0.0,
    ) -> ModelResponse:
        if not self._secret_id or not self._secret_key:
            raise ModelError("腾讯云 OCR 未配置 SecretId/SecretKey", ModelErrorType.AUTH_FAILED)
        import hashlib
        import hmac
        import time
        import httpx
        try:
            endpoint = "ocr.tencentcloudapi.com"
            service = "ocr"
            timestamp = int(time.time())
            if not image_urls:
                raise ModelError("腾讯云 OCR 缺少图片", ModelErrorType.INVALID_RESPONSE)
            payload = {"ImageBase64": base64.b64encode(Path(image_urls[0]).read_bytes()).decode("ascii")}
            body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
            date = time.strftime("%Y-%m-%d", time.gmtime(timestamp))
            canonical_headers = "content-type:application/json; charset=utf-8\nhost:" + endpoint + "\n"
            signed_headers = "content-type;host"
            hashed_payload = hashlib.sha256(body.encode()).hexdigest()
            canonical_request = "POST\n/\n\n" + canonical_headers + "\n" + signed_headers + "\n" + hashed_payload
            credential_scope = f"{date}/{service}/tc3_request"
            hashed_request = hashlib.sha256(canonical_request.encode()).hexdigest()
            string_to_sign = f"TC3-HMAC-SHA256\n{timestamp}\n{credential_scope}\n{hashed_request}"
            def sign(key, msg): return hmac.new(key, msg.encode(), hashlib.sha256).digest()
            secret_date = sign(("TC3" + self._secret_key).encode(), date)
            secret_service = sign(secret_date, service)
            secret_signing = sign(secret_service, "tc3_request")
            signature = hmac.new(secret_signing, string_to_sign.encode(), hashlib.sha256).hexdigest()
            authorization = f"TC3-HMAC-SHA256 Credential={self._secret_id}/{credential_scope}, SignedHeaders={signed_headers}, Signature={signature}"
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post("https://" + endpoint, content=body, headers={"Authorization": authorization, "Content-Type": "application/json; charset=utf-8", "Host": endpoint, "X-TC-Action": "GeneralBasicOCR", "X-TC-Version": "2018-11-19", "X-TC-Timestamp": str(timestamp), "X-TC-Region": "ap-guangzhou"})
            if response.status_code == 401:
                raise ModelError("腾讯云 OCR 鉴权失败", ModelErrorType.AUTH_FAILED)
            if response.status_code >= 400:
                raise ModelError(f"腾讯云 OCR 返回 HTTP {response.status_code}", ModelErrorType.NETWORK)
            data = response.json()
            error = (data.get("Response") or {}).get("Error")
            if error:
                raise ModelError(str(error.get("Message") or "腾讯云 OCR 请求失败"), ModelErrorType.INVALID_RESPONSE)
            detections = (data.get("Response") or {}).get("TextDetections") or []
            text = "\n".join(str(item.get("DetectedText", "")) for item in detections)
            return ModelResponse(content=json.dumps({"pages": [{"page_no": 1, "text": text}], "regions": detections}, ensure_ascii=False), usage=ModelUsage(provider_request_id=response.headers.get("x-request-id")))
        except ModelError:
            raise
        except httpx.TimeoutException as exc:
            raise ModelError("腾讯云 OCR 请求超时", ModelErrorType.TIMEOUT) from exc
        except (httpx.HTTPError, json.JSONDecodeError, OSError) as exc:
            raise ModelError("腾讯云 OCR 响应无效", ModelErrorType.INVALID_RESPONSE) from exc


def create_vision_provider(config: "AgentConfig") -> VisionModelProvider | None:
    """根据配置创建视觉模型供应商实例。

    :param config: Agent 配置
    :return: 视觉供应商实例，若未配置则返回 None
    """
    provider_type = config.vision_provider

    if provider_type == "tencent_ocr":
        import os
        secret_id = os.environ.get("TENCENT_SECRET_ID")
        secret_key = os.environ.get("TENCENT_SECRET_KEY")
        if not secret_id or not secret_key:
            logger.warning("腾讯云 OCR 未配置 SecretId/SecretKey，视觉分析保持关闭")
            return None
        return TencentOCRProvider(
            secret_id=secret_id,
            secret_key=secret_key,
        )
    elif provider_type in {"generic", "openai", "openai_compat", "deepseek", "qwen", "gemini", "zhipu"}:
        if not config.vision_api_key or not config.vision_api_base_url or not config.vision_model_name:
            logger.warning("视觉 Provider 配置不完整，视觉分析保持关闭")
            return None
        return GenericVisionProvider(
            api_key=config.vision_api_key,
            base_url=config.vision_api_base_url,
            model_name=config.vision_model_name or "vision-model",
            timeout=config.model_timeout_seconds,
            max_retries=config.model_max_retries,
            endpoint_path="/chat/completions" if provider_type == "zhipu" else None,
            thinking_enabled=provider_type == "zhipu",
        )
    else:
        logger.warning("未配置视觉模型供应商，视觉分析功能将不可用")
        return None
