"""成长树服务包（方案 §5）。

模块职责：
- ``rules``：确定性计分规则（纯函数，可单元测试）。
- ``events``：学习事件账本，幂等写入与按学生/学期重建奖励。
- ``performance``：分项学业表现（可比测评得分率 + 指数平滑）。
- ``snapshots``：学期成长快照（可重建缓存）。
- ``service``：路由层使用的高层编排。
- ``legacy_import``：Windows 参考版旧记录导入预览与确认。
"""

from __future__ import annotations

from . import events, legacy_import, performance, rules, snapshots, service

__all__ = ["events", "legacy_import", "performance", "rules", "snapshots", "service"]
