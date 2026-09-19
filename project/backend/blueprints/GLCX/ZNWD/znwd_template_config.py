"""
ZNWD 业务模板配置加载器（可选，现网默认未启用）

从 business_templates.json 加载模板。仓库未附带该 JSON 时，快速路径以
znwd_business_templates.py + znwd_thinking_narrative.py 为准。
services_v5._plan_thinking_display_text 中 JSON 展示分支已注释。
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# 配置文件路径（与当前文件同目录）
CONFIG_FILE = Path(__file__).parent / "business_templates.json"


class BusinessTemplateConfig:
    """业务模板配置加载器"""

    def __init__(self, config_path: Optional[str] = None):
        self.config_path = Path(config_path) if config_path else CONFIG_FILE
        self._config: Dict[str, Any] = {}
        self._templates: Dict[str, Dict[str, Any]] = {}
        self._loaded = False
        self.load()

    def load(self) -> bool:
        """加载配置文件"""
        try:
            if not self.config_path.exists():
                logger.warning("[ZNWD][TemplateConfig] 配置文件不存在: %s", self.config_path)
                return False

            with open(self.config_path, "r", encoding="utf-8") as f:
                self._config = json.load(f)

            # 解析模板
            self._templates = {}
            for tpl in self._config.get("templates", []):
                code = tpl.get("template_code")
                if code:
                    self._templates[code] = tpl

            self._loaded = True
            logger.info(
                "[ZNWD][TemplateConfig] 已加载 %d 个业务模板: %s",
                len(self._templates),
                list(self._templates.keys()),
            )
            return True

        except Exception as e:
            logger.error("[ZNWD][TemplateConfig] 加载配置文件失败: %s", e)
            self._loaded = False
            return False

    def reload(self) -> bool:
        """重新加载配置文件（支持热更新）"""
        self._loaded = False
        return self.load()

    def get_template(self, template_code: str) -> Optional[Dict[str, Any]]:
        """根据模板代码获取模板配置"""
        return self._templates.get(template_code)

    def get_all_templates(self) -> Dict[str, Dict[str, Any]]:
        """获取所有模板配置"""
        return dict(self._templates)

    def match_template(self, question: str, intent_type: str) -> Optional[Dict[str, Any]]:
        """
        根据问法和意图匹配模板
        
        返回第一个匹配的模板，如果没有匹配则返回 None
        """
        question_lower = (question or "").lower()

        for code, template in self._templates.items():
            # 检查意图类型是否匹配
            if template.get("intent_type") != intent_type:
                continue

            # 检查问法模式是否匹配
            patterns = template.get("question_patterns", [])
            for pattern in patterns:
                try:
                    # 将 {focus} 替换为通用匹配
                    regex_pattern = pattern.replace("{focus}", r".*")
                    if re.search(regex_pattern, question_lower):
                        logger.info(
                            "[ZNWD][TemplateConfig] 模板 %s 匹配问法: %s",
                            code,
                            question[:50],
                        )
                        return template
                except re.error as e:
                    logger.warning(
                        "[ZNWD][TemplateConfig] 模板 %s 的正则表达式有误: %s",
                        code,
                        e,
                    )
                    continue

        return None

    def build_program(
        self,
        template_code: str,
        slots: Dict[str, Any],
        retrieval_question: str = "",
    ) -> Optional[Dict[str, Any]]:
        """
        根据模板和槽位生成 program
        
        Args:
            template_code: 模板代码
            slots: 槽位值
            retrieval_question: 检索问题（用于模板变量替换）
        
        Returns:
            生成的 program，如果模板不存在则返回 None
        """
        template = self.get_template(template_code)
        if not template:
            logger.warning("[ZNWD][TemplateConfig] 模板不存在: %s", template_code)
            return None

        # 替换步骤中的模板变量
        steps = []
        for step in template.get("steps", []):
            params = self._replace_template_vars(step.get("params", {}), slots, retrieval_question)
            purpose = self._replace_template_vars(
                step.get("purpose", ""), slots, retrieval_question
            )

            steps.append({
                "step_id": step.get("step_id"),
                "op_code": step.get("op_code"),
                "params": params,
                "purpose": purpose,
            })

        # 构建 program
        program = {
            "_v5_fast_route": True,
            "_business_template": template_code,
            "_business_slots": slots,
            "_v5_intent": template.get("intent_type", ""),
            "intent_summary": template.get("template_name", ""),
            "answer_mode": template.get("answer_mode", "llm_synthesize"),
            "fallback_v4": False,
            "missing_capability": "",
            "steps": steps,
        }

        return program

    def _replace_template_vars(
        self,
        template_str: Any,
        slots: Dict[str, Any],
        retrieval_question: str = "",
    ) -> Any:
        """
        替换模板中的变量
        
        支持格式：
        - ${variable_name}: 从 slots 中取值
        - ${retrieval_question}: 替换为检索问题
        """
        if not isinstance(template_str, str):
            return template_str

        result = template_str

        # 替换 retrieval_question
        if "${retrieval_question}" in result:
            result = result.replace("${retrieval_question}", retrieval_question)

        # 替换 slots 变量
        for key, value in slots.items():
            placeholder = f"${{{key}}}"
            if placeholder in result:
                result = result.replace(placeholder, str(value))

        return result

    def is_loaded(self) -> bool:
        """检查配置是否已加载"""
        return self._loaded


# 全局单例
_config_instance: Optional[BusinessTemplateConfig] = None


def get_template_config() -> BusinessTemplateConfig:
    """获取全局模板配置实例（单例）"""
    global _config_instance
    if _config_instance is None:
        _config_instance = BusinessTemplateConfig()
    return _config_instance


def reload_template_config() -> bool:
    """重新加载模板配置（支持热更新）"""
    global _config_instance
    _config_instance = BusinessTemplateConfig()
    return _config_instance.is_loaded()
