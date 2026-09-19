"""试验方法章节抽取：可编辑 JSON 配置（标题关键词 / 展示名称）。"""
from __future__ import annotations

import json
import os
from functools import lru_cache
from typing import Any, Dict, List, Tuple

_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "section_extract_config.json")

_DEFAULT: Dict[str, Any] = {
    "display_labels": {"sytj": "试验条件", "sygc": "试验过程", "jgpd": "结果评定"},
    "title_keywords": {
        "sytj": ["试验条件"],
        "sygc": ["试验过程", "试验程序", "试验步骤"],
        "jgpd": ["结果评定", "试验结果评定", "试验结果"],
    },
    "content_hints": {
        "sytj": ["条件", "设备", "温度", "湿度"],
        "sygc": ["步骤", "程序", "过程", "中断", "恢复"],
        "jgpd": ["结果", "评定", "判定", "合格", "不合格"],
    },
    "exclude_title_keywords": [
        "范围",
        "规范性引用文件",
        "术语和定义",
        "试验设备",
        "仪器设备",
        "试验报告",
    ],
    "prefer_title_name_over_chapter_number": True,
}


@lru_cache(maxsize=1)
def load_section_extract_config() -> Dict[str, Any]:
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return dict(_DEFAULT)


def reload_section_extract_config() -> Dict[str, Any]:
    load_section_extract_config.cache_clear()
    return load_section_extract_config()


def title_keywords(bucket: str) -> Tuple[str, ...]:
    cfg = load_section_extract_config()
    items = (cfg.get("title_keywords") or {}).get(bucket) or _DEFAULT["title_keywords"].get(bucket, [])
    return tuple(str(x).strip() for x in items if str(x).strip())


def content_hints(bucket: str) -> Tuple[str, ...]:
    cfg = load_section_extract_config()
    items = (cfg.get("content_hints") or {}).get(bucket) or _DEFAULT["content_hints"].get(bucket, [])
    return tuple(str(x).strip() for x in items if str(x).strip())


def exclude_title_keywords() -> Tuple[str, ...]:
    cfg = load_section_extract_config()
    items = cfg.get("exclude_title_keywords") or _DEFAULT["exclude_title_keywords"]
    return tuple(str(x).strip() for x in items if str(x).strip())


def display_label(bucket: str) -> str:
    cfg = load_section_extract_config()
    labels = cfg.get("display_labels") or {}
    return str(labels.get(bucket) or _DEFAULT["display_labels"].get(bucket, bucket))


def prefer_title_name_over_chapter_number() -> bool:
    cfg = load_section_extract_config()
    return bool(cfg.get("prefer_title_name_over_chapter_number", True))
