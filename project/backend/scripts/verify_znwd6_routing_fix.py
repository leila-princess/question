#!/usr/bin/env python3
"""新机部署后运行：校验 ZNWD6 装备指标路由修复是否生效。"""

from __future__ import annotations

import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def main() -> int:
    try:
        from blueprints.GLCX.ZNWD.session_context import resolve_question_context
        from blueprints.GLCX.ZNWD6.config import ROUTING_FIX_REV
        from blueprints.GLCX.ZNWD6.intent_router import (
            enforce_binding_template,
            refine_template_by_binding,
            suggest_template,
        )
    except ImportError as exc:
        print(f"FAIL import: {exc}")
        return 1

    print(f"routing_fix_rev={ROUTING_FIX_REV}")

    ctx = {
        "focus_entity": {"type": "Equipment", "name": "无人机", "id": "ZB_1"},
        "last_template_id": "equip_to_projects",
        "slots": {"equipment": ["无人机"]},
    }
    resolved = resolve_question_context("考核指标呢", session_ctx=ctx)
    q = resolved.resolved_question
    tau = {"flat_candidates": []}
    tid, _ = suggest_template(q, tau, template_hint=resolved.template_hint)

    checks = [
        ("resolve_contains_uav", "无人机" in q and "指标" in q),
        ("template_hint", resolved.template_hint == "equip_to_indicators"),
        ("suggest_template", tid == "equip_to_indicators"),
    ]

    binding = {
        "bindings": [{"node_type": "Equipment", "name": "无人机", "entity_id": "ZB_1"}],
        "template_id": "enum_indicator",
        "intent": "llm",
    }
    fixed = enforce_binding_template(
        binding,
        suggested_tid="enum_indicator",
        suggest_reason="global_enum_indicator",
        template_hint=None,
        question=q,
        tau=tau,
    )
    refined = refine_template_by_binding(
        fixed["template_id"],
        fixed,
        template_hint=None,
        question=q,
        tau=tau,
    )
    checks.extend(
        [
            ("enforce_binding", fixed["template_id"] == "equip_to_indicators"),
            ("refine_template", refined == "equip_to_indicators"),
        ]
    )

    ok = True
    for name, passed in checks:
        status = "OK" if passed else "FAIL"
        print(f"  [{status}] {name}")
        ok = ok and passed

    if ok:
        print("ALL PASSED — 路由修复已生效，请重启 backend 后再测前端。")
        return 0
    print("SOME FAILED — 请确认已复制全部文件并重启服务。")
    return 2


if __name__ == "__main__":
    sys.exit(main())
