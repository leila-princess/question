"""在导入 chromadb / posthog 之前调用，避免无网环境 DNS 报错。"""
from __future__ import annotations

import os


def apply_offline_bootstrap() -> None:
    os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")
    os.environ.setdefault("CHROMA_TELEMETRY", "FALSE")
    os.environ.setdefault("CHROMA_TELEMETRY_DISABLED", "1")
    os.environ.setdefault("POSTHOG_DISABLED", "true")

    try:
        import posthog  # type: ignore

        posthog.disabled = True
        if hasattr(posthog, "Project"):
            posthog.Project().disabled = True
    except Exception:
        pass
