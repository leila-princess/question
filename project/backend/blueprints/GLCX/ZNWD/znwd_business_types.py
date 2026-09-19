"""业务模板代号（与 znwd_business_templates 解耦，避免循环 import）。"""

from __future__ import annotations

from enum import Enum


class BusinessTemplate(str, Enum):
    ENUM = "BIZ_ENUM"
    REL_ONE_HOP = "BIZ_REL_ONE_HOP"
    REL_TWO_HOP = "BIZ_REL_TWO_HOP"
    PROJECT_TO_METHOD = "BIZ_PROJECT_TO_METHOD"
    EQUIP_DISCOVERY = "BIZ_EQUIP_DISCOVERY"
    DOCUMENT = "BIZ_DOCUMENT"


__all__ = ("BusinessTemplate",)
