"""注册表包：工具注册表 + 能力注册表。"""

from .tools import ToolRegistry, ToolDefinition, ToolError, create_default_registry
from .capabilities import (
    CapabilityRegistry,
    CapabilityDefinition,
    create_default_capability_registry,
)

__all__ = [
    "ToolRegistry",
    "ToolDefinition",
    "ToolError",
    "create_default_registry",
    "CapabilityRegistry",
    "CapabilityDefinition",
    "create_default_capability_registry",
]
