#!/usr/bin/env python
"""
纯 LLM 模式 Demo：LLM 因果骨架 + 经典规划器补全 → 试验方案 + 可视化 PNG。

用法（在 backend 目录）：
  python -m blueprints.FZSJ.JYLLM.pure_llm.demo

或：
  python blueprints/FZSJ/JYLLM/pure_llm/demo.py
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[4]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from bootstrap_offline import apply_offline_bootstrap

apply_offline_bootstrap()

from app import create_app  # noqa: E402
from blueprints.FZSJ.JYLLM.pure_llm.pipeline import run_pure_llm_pipeline  # noqa: E402

DEFAULT_QUERY = (
    "对 Matrice 350 RTK 型无人机进行低温 -30℃ 环境下的发动机启动特性测试，"
    "需满足 GB/T 38924.6 及相关低温试验标准，形成可执行的试验步骤序列。"
)


def main():
    task_id = f"demo_{uuid.uuid4().hex[:10]}"
    query = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_QUERY
    print("=" * 60)
    print("纯 LLM 试验方案生成 Demo")
    print("=" * 60)
    print(f"任务描述：{query}\n")

    def on_progress(phase: str, data: dict):
        msg = data.get("message") or phase
        print(f"[{phase}] {msg}")

    app = create_app()
    with app.app_context():
        result = run_pure_llm_pipeline(query, task_id, on_progress=on_progress)

    print("\n" + "=" * 60)
    print("可视化输出：")
    for k, p in (result.get("figure_paths") or {}).items():
        print(f"  {k}: {p}")
    print("\n方案摘要（前 800 字）：")
    print((result.get("scheme_markdown") or "")[:800])
    print("\n完整方案已写入：", Path(result["figure_paths"]["kg_subgraph"]).parent / "scheme.md")


if __name__ == "__main__":
    main()
