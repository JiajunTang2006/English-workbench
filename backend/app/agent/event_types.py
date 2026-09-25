"""运行事件类型常量。

后端和前端契约共享，确保事件名一致。
前端不得监听此处未定义的别名。
"""

from __future__ import annotations

# 运行生命周期事件
RUN_STARTED = "run.started"
RUN_COMPLETED = "run.completed"
RUN_FAILED = "run.failed"
RUN_CANCELLED = "run.cancelled"
RUN_WAITING_CONFIRMATION = "run.waiting_confirmation"
RUN_REGISTERED = "run.registered"
RUN_IDLE = "run.idle"
RUN_DEGRADED = "run.degraded"
RUN_INTERRUPTED = "run.interrupted"

# 模型事件
MODEL_STARTED = "model.started"
MODEL_DELTA = "model.delta"
MODEL_THINKING = "model.thinking"
MODEL_COMPLETED = "model.completed"

# 工具事件
TOOL_STARTED = "tool.started"
TOOL_COMPLETED = "tool.completed"

# 用量事件
USAGE_UPDATED = "usage.updated"

# 验证事件
VALIDATION_STARTED = "validation.started"
VALIDATION_FAILED = "validation.failed"

# 面向教师的语义进度事件。它们描述“正在做什么”和“下一步是什么”，
# 不承载模型原始思维链、完整 prompt 或工具原始输出。
PROGRESS_STEP_STARTED = "progress.step_started"
PROGRESS_STEP_UPDATED = "progress.step_updated"
PROGRESS_STEP_COMPLETED = "progress.step_completed"
PROGRESS_HEARTBEAT = "progress.heartbeat"

# 所有合法的事件类型
ALL_EVENT_TYPES = frozenset({
    RUN_REGISTERED,
    RUN_STARTED,
    RUN_COMPLETED,
    RUN_FAILED,
    RUN_CANCELLED,
    RUN_WAITING_CONFIRMATION,
    RUN_IDLE,
    RUN_DEGRADED,
    RUN_INTERRUPTED,
    MODEL_STARTED,
    MODEL_DELTA,
    MODEL_THINKING,
    MODEL_COMPLETED,
    TOOL_STARTED,
    TOOL_COMPLETED,
    USAGE_UPDATED,
    VALIDATION_STARTED,
    VALIDATION_FAILED,
    PROGRESS_STEP_STARTED,
    PROGRESS_STEP_UPDATED,
    PROGRESS_STEP_COMPLETED,
    PROGRESS_HEARTBEAT,
})

# 终态事件 —— 收到这些事件后不再继续轮询
TERMINAL_EVENT_TYPES = frozenset({
    RUN_COMPLETED,
    RUN_FAILED,
    RUN_CANCELLED,
    RUN_DEGRADED,
})
