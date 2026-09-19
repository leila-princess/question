"""路由 Agent 系统提示（由 routing_prompt_builder 生成，不含全量 catalog）。"""

from .routing_prompt_builder import build_routing_system_prompt

ROUTING_SYSTEM_PROMPT = build_routing_system_prompt()
