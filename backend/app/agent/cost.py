"""
成本估算与记录 (CostEstimator)

职责：
- 供应商价格配置（不硬编码实时汇率）
- 输入/输出 Token 估算
- 文本、视觉阶段分项预算
- 人民币换算率可配置
- 预计超过软上限时进入等待确认
- 实际 usage 和供应商 request ID 记录
- 不保存 API Key
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any


# 预算控制全局开关（B3 验收结论：模型用量仅为理论估算、未实测，默认关闭不拦截，
# 用户可在设置中按需开启严格的「未知模型拒绝 + 超限确认」预算门禁）。
# 优先级：显式构造参数 > 运行时开关（API 切换）> WORKBENCH_BUDGET_CONTROL_ENABLED env。
_runtime_budget_control: bool | None = None


def set_budget_control(enabled: bool | None) -> None:
    """设置运行时预算控制开关（None = 恢复跟随 env 默认）。"""
    global _runtime_budget_control
    _runtime_budget_control = enabled


def get_budget_control() -> bool:
    """当前生效的预算控制开关。"""
    if _runtime_budget_control is not None:
        return _runtime_budget_control
    raw = os.getenv("WORKBENCH_BUDGET_CONTROL_ENABLED", "").strip().lower()
    return raw in ("1", "true", "yes", "on")


@dataclass
class ModelPricing:
    """模型定价配置（每百万 Token，单位：CNY）。"""
    input_price_per_million: float = 0.0
    output_price_per_million: float = 0.0


# 默认定价表（可由设置页更新）
DEFAULT_PRICING: dict[str, ModelPricing] = {
    # DeepSeek V4 定价（每百万 token，CNY）
    # Cache miss input: $0.14/M, output: $0.28/M (flash)
    # Cache miss input: $0.435/M, output: $0.87/M (pro)
    # 按 7.2 CNY/USD 换算
    "deepseek-chat": ModelPricing(
        input_price_per_million=1.008,   # $0.14 * 7.2
        output_price_per_million=2.016,  # $0.28 * 7.2
    ),
    "deepseek-reasoner": ModelPricing(
        input_price_per_million=3.132,   # $0.435 * 7.2
        output_price_per_million=6.264,  # $0.87 * 7.2
    ),
    # DeepSeek V4 flash（与 deepseek-chat 同档：$0.14/$0.28 每 M）
    "deepseek-v4-flash": ModelPricing(
        input_price_per_million=1.008,   # $0.14 * 7.2
        output_price_per_million=2.016,  # $0.28 * 7.2
    ),
    # Anthropic Claude 定价（每百万 token，CNY）
    # Claude 4 Sonnet: $3/M input, $15/M output
    # Claude 4 Opus: $15/M input, $75/M output
    # Claude 3.5 Sonnet: $3/M input, $15/M output
    # Claude 3.5 Haiku: $0.8/M input, $4/M output
    # 按 7.2 CNY/USD 换算
    "claude-sonnet-4-20250514": ModelPricing(
        input_price_per_million=21.6,    # $3 * 7.2
        output_price_per_million=108.0,  # $15 * 7.2
    ),
    "claude-opus-4-20250514": ModelPricing(
        input_price_per_million=108.0,   # $15 * 7.2
        output_price_per_million=540.0,  # $75 * 7.2
    ),
    "claude-3-5-sonnet-20241022": ModelPricing(
        input_price_per_million=21.6,    # $3 * 7.2
        output_price_per_million=108.0,  # $15 * 7.2
    ),
    "claude-3-5-haiku-20241022": ModelPricing(
        input_price_per_million=5.76,    # $0.8 * 7.2
        output_price_per_million=28.8,   # $4 * 7.2
    ),
    # OpenAI 定价（每百万 token，CNY）
    # GPT-4o: $2.5/M input, $10/M output
    # GPT-4o-mini: $0.15/M input, $0.6/M output
    "gpt-4o": ModelPricing(
        input_price_per_million=18.0,    # $2.5 * 7.2
        output_price_per_million=72.0,   # $10 * 7.2
    ),
    "gpt-4o-mini": ModelPricing(
        input_price_per_million=1.08,    # $0.15 * 7.2
        output_price_per_million=4.32,   # $0.6 * 7.2
    ),
    # codexapis.com（New API 网关）中转模型 —— 占位价，需按供应商实际报价更新
    "gpt-5.6-luna": ModelPricing(
        input_price_per_million=7.2,     # 占位 $1 * 7.2
        output_price_per_million=28.8,   # 占位 $4 * 7.2
    ),
    "gpt-5.6-terra-openai-compact": ModelPricing(
        input_price_per_million=7.2,     # 占位 $1 * 7.2
        output_price_per_million=28.8,   # 占位 $4 * 7.2
    ),
}


@dataclass
class CostEstimate:
    """单次成本估算结果。"""
    estimated_cost_yuan: float
    estimated_input_tokens: int
    estimated_output_tokens: int
    stage: str  # text_analysis / vision_analysis / report_generation
    model_name: str
    requires_confirmation: bool = False


class UnknownModelPricingError(ValueError):
    """模型没有显式定价时拒绝按零成本继续执行。"""

    def __init__(self, model_name: str) -> None:
        self.model_name = model_name
        super().__init__(f"模型 {model_name!r} 尚未配置价格，无法执行预算控制")


class CostEstimator:
    """成本估算器。"""

    def __init__(
        self,
        pricing: dict[str, ModelPricing] | None = None,
        budget_soft_limit_yuan: float = 0.5,
        cny_usd_rate: float = 7.2,
        budget_control_enabled: bool | None = None,
    ):
        self._pricing = pricing or dict(DEFAULT_PRICING)
        self._budget_soft_limit_yuan = budget_soft_limit_yuan
        self._cny_usd_rate = cny_usd_rate
        # 预算控制关闭时：未知模型不拒绝、超限不要求确认（理论价格未经实测，
        # 默认不拦截，避免影响用户体验；用户可显式开启严格门禁）。
        self._budget_control_enabled = (
            budget_control_enabled
            if budget_control_enabled is not None
            else get_budget_control()
        )

    def estimate_text(
        self,
        model_name: str,
        estimated_input_tokens: int,
        estimated_output_tokens: int,
        stage: str = "text_analysis",
    ) -> CostEstimate:
        """估算文本模型调用成本。"""
        pricing = self._pricing.get(model_name)
        if pricing is None:
            if not self._budget_control_enabled:
                # 宽松模式：未知模型按零成本放行（预算门禁关闭），不阻塞用户
                return CostEstimate(
                    estimated_cost_yuan=0.0,
                    estimated_input_tokens=estimated_input_tokens,
                    estimated_output_tokens=estimated_output_tokens,
                    stage=stage,
                    model_name=model_name,
                    requires_confirmation=False,
                )
            raise UnknownModelPricingError(model_name)
        input_cost = (estimated_input_tokens / 1_000_000) * pricing.input_price_per_million
        output_cost = (estimated_output_tokens / 1_000_000) * pricing.output_price_per_million
        total = input_cost + output_cost
        return CostEstimate(
            estimated_cost_yuan=round(total, 4),
            estimated_input_tokens=estimated_input_tokens,
            estimated_output_tokens=estimated_output_tokens,
            stage=stage,
            model_name=model_name,
            # 预算控制开启时才按软上限要求确认；关闭时永不确认
            requires_confirmation=(
                self._budget_control_enabled and total > self._budget_soft_limit_yuan
            ),
        )

    def estimate_vision(
        self,
        model_name: str,
        num_images: int,
        estimated_input_tokens: int,
        estimated_output_tokens: int,
    ) -> CostEstimate:
        """估算视觉模型调用成本。"""
        return self.estimate_text(
            model_name=model_name,
            estimated_input_tokens=estimated_input_tokens,
            estimated_output_tokens=estimated_output_tokens,
            stage="vision_analysis",
        )

    def check_budget(self, accumulated_cost_yuan: float) -> bool:
        """检查累计成本是否超过预算软上限（预算控制关闭时恒不拦截）。"""
        if not self._budget_control_enabled:
            return False
        return accumulated_cost_yuan >= self._budget_soft_limit_yuan

    def update_pricing(self, model_name: str, pricing: ModelPricing) -> None:
        """更新模型定价。"""
        self._pricing[model_name] = pricing

    def get_pricing(self, model_name: str) -> ModelPricing | None:
        """获取模型定价。"""
        return self._pricing.get(model_name)

    def actual_cost(
        self,
        model_name: str,
        input_tokens: int,
        output_tokens: int,
    ) -> float:
        """计算实际调用成本（CNY）。预算控制关闭时未知模型按 0 放行；开启时抛错。"""
        pricing = self._pricing.get(model_name)
        if pricing is None:
            if not self._budget_control_enabled:
                return 0.0
            raise UnknownModelPricingError(model_name)
        input_cost = (input_tokens / 1_000_000) * pricing.input_price_per_million
        output_cost = (output_tokens / 1_000_000) * pricing.output_price_per_million
        return round(input_cost + output_cost, 4)

    def safe_actual_cost(
        self,
        model_name: str,
        input_tokens: int,
        output_tokens: int,
    ) -> float | None:
        """计算实际调用成本（CNY）。未知模型返回 None（表示费用未知）。"""
        try:
            return self.actual_cost(model_name, input_tokens, output_tokens)
        except UnknownModelPricingError:
            return None
