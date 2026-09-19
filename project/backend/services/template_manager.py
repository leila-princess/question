"""
模板管理模块 - 用于保存和管理用户自定义模板
支持将模板保存到Chroma数据库进行向量检索
"""
import json
import uuid
import hashlib
from datetime import datetime
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, asdict

@dataclass
class TemplateVariable:
    """模板变量定义"""
    name: str
    type: str  # text, multiline, number, select, multiselect, date, boolean
    required: bool
    label: str
    placeholder: str = ""
    default: Any = None
    description: str = ""
    options: List[str] = None
    
    def to_dict(self) -> Dict:
        return asdict(self)

@dataclass
class TemplateSection:
    """模板章节定义"""
    id: str
    title: str
    required: bool
    can_edit_title: bool
    can_delete: bool
    has_retrieval_mark: bool
    depends_on: List[str] = None
    content: str = ""
    
    def to_dict(self) -> Dict:
        return asdict(self)

@dataclass
class TemplateMetadata:
    """模板元数据"""
    id: str
    name: str
    description: str
    version: str
    category: str
    created_by: str
    created_at: str
    updated_at: str
    is_system: bool
    is_default: bool
    status: str  # draft, active, archived
    
    def to_dict(self) -> Dict:
        return asdict(self)

@dataclass
class UserTemplate:
    """用户模板完整定义"""
    metadata: TemplateMetadata
    variables: List[TemplateVariable]
    sections: List[TemplateSection]
    content: str  # Markdown内容
    retrieval_marks: List[Dict[str, Any]]
    validation_rules: Dict[str, Any]
    
    def to_dict(self) -> Dict:
        return {
            "metadata": self.metadata.to_dict(),
            "variables": [v.to_dict() for v in self.variables],
            "sections": [s.to_dict() for s in self.sections],
            "content": self.content,
            "retrieval_marks": self.retrieval_marks,
            "validation_rules": self.validation_rules
        }

class TemplateManager:
    """模板管理器 - 处理模板的CRUD和Chroma存储"""
    
    def __init__(self, chroma_collection=None):
        self.chroma_collection = chroma_collection
        
    def _generate_template_id(self, user_id: str, name: str) -> str:
        """生成模板ID"""
        hash_input = f"{user_id}:{name}:{datetime.now().isoformat()}"
        return f"tpl_{hashlib.md5(hash_input.encode()).hexdigest()[:12]}"
    
    def create_default_template(self, user_id: str) -> UserTemplate:
        """创建默认模板"""
        template_id = self._generate_template_id(user_id, "通用环境试验方案模板")
        
        metadata = TemplateMetadata(
            id=template_id,
            name="通用环境试验方案模板",
            description="适用于各类设备的环境及边界性能试验方案",
            version="1.0",
            category="环境试验",
            created_by=user_id,
            created_at=datetime.now().isoformat(),
            updated_at=datetime.now().isoformat(),
            is_system=False,
            is_default=True,
            status="active"
        )
        
        # 系统变量
        system_variables = [
            TemplateVariable(
                name="equipment_name",
                type="text",
                required=True,
                label="设备名称",
                placeholder="请输入设备名称",
                description="被测试的设备名称"
            ),
            TemplateVariable(
                name="environment_desc",
                type="multiline",
                required=True,
                label="环境描述",
                placeholder="描述试验环境条件",
                description="试验环境的详细描述"
            ),
            TemplateVariable(
                name="doc_id",
                type="text",
                required=True,
                label="文档标识",
                placeholder="如：WRJ-HJSY-2025-001",
                description="试验方案文档编号"
            ),
            TemplateVariable(
                name="purpose",
                type="text",
                required=True,
                label="试验目的",
                placeholder="如：型号定型/使用批准",
                default="型号定型",
                description="试验的目的和用途"
            ),
            TemplateVariable(
                name="task_source",
                type="text",
                required=False,
                label="任务来源",
                placeholder="如：年度试验工作计划",
                default="年度试验工作计划",
                description="试验任务的来源"
            ),
            TemplateVariable(
                name="abbreviations",
                type="multiline",
                required=False,
                label="术语和缩略语",
                placeholder="列出设备相关缩略语，如RTK、IMU等",
                description="文档中使用的专业术语和缩略语"
            )
        ]
        
        # 必要章节
        required_sections = [
            TemplateSection(
                id="section_1",
                title="1. 范围",
                required=True,
                can_edit_title=False,
                can_delete=False,
                has_retrieval_mark=False,
                content="### 1.1 标识\n- **文档标识**：`{{doc_id}}`（用户提供）\n- **文档标题**：`{{equipment_name}}`环境及边界性能试验方案\n- **术语和缩略语**：`{{abbreviations}}`\n\n### 1.2 试验目的\n考核`{{equipment_name}}`在`{{environment_desc}}`下的环境适应性和边界性能，发现缺陷，提出改进和使用建议，为`{{purpose}}`提供依据。\n**【检索】** 无。"
            ),
            TemplateSection(
                id="section_2",
                title="2. 试验依据",
                required=True,
                can_edit_title=False,
                can_delete=False,
                has_retrieval_mark=False,
                content="### 2.1 任务来源\n`{{task_source}}`\n### 2.2 依据文件\n列出用户提供的方法标准。\n**【检索】** 无。"
            ),
            TemplateSection(
                id="section_3",
                title="3. 试验环境与设备",
                required=True,
                can_edit_title=False,
                can_delete=False,
                has_retrieval_mark=True,
                content="### 3.1 具体环境下的设备\n按测试项目类别列出所需的主要试验设备及其关键性能参数。\n**【检索】** 每项**标准中规定的设备能力要求**，以及**标准中规定的试验条件参数**。必须从标准原文检索。"
            ),
            TemplateSection(
                id="section_4",
                title="4. {{环境类别A}}试验内容",
                required=False,
                can_edit_title=True,
                can_delete=True,
                has_retrieval_mark=True,
                depends_on=["environment_category_a"],
                content="### 4.1 试验项目\n列出该环境类别下的所有试验项目。\n### 4.2 试验方法\n详细描述试验方法和步骤。\n**【检索】** 试验方法的具体步骤和参数。"
            ),
            TemplateSection(
                id="section_5",
                title="5. 试验中断与恢复",
                required=True,
                can_edit_title=False,
                can_delete=False,
                has_retrieval_mark=False,
                content="描述试验中断的条件和恢复方法。\n**【检索】** 无。"
            ),
            TemplateSection(
                id="section_6",
                title="6. 试验结果评定",
                required=True,
                can_edit_title=False,
                can_delete=False,
                has_retrieval_mark=False,
                content="`{{equipment_name}}`在各项环境试验前、后的外观/结构检查结果以及功能/性能检测结果满足本方案规定的合格判据时，环境试验结果为合格。\n**【检索】** 无。"
            )
        ]
        
        # 默认内容
        default_content = """---
id: {template_id}
name: 通用环境试验方案模板
description: 适用于各类设备的环境及边界性能试验方案
version: "1.0"
category: 环境试验
created_by: {user_id}
created_at: {created_at}
updated_at: {updated_at}
is_default: true
is_system: false
status: active

system_variables:
  - name: equipment_name
    type: text
    required: true
    label: 设备名称
    placeholder: 请输入设备名称
  - name: environment_desc
    type: multiline
    required: true
    label: 环境描述
    placeholder: 描述试验环境条件
  - name: doc_id
    type: text
    required: true
    label: 文档标识
    placeholder: 如：WRJ-HJSY-2025-001

required_sections:
  - id: section_1
    title: 1. 范围
    required: true
    can_edit_title: false
    can_delete: false
    has_retrieval_mark: false
  - id: section_2
    title: 2. 试验依据
    required: true
    can_edit_title: false
    can_delete: false
    has_retrieval_mark: false
  - id: section_3
    title: 3. 试验环境与设备
    required: true
    can_edit_title: false
    can_delete: false
    has_retrieval_mark: true
  - id: section_6
    title: 6. 试验结果评定
    required: true
    can_edit_title: false
    can_delete: false
    has_retrieval_mark: false

retrieval_marks:
  - mark: "【检索】"
    description: "需要从知识库检索相关内容"
    required: true
    can_edit_note: true
---

## 1. 范围
### 1.1 标识
- **文档标识**：`{{doc_id}}`（用户提供）
- **文档标题**：`{{equipment_name}}`环境及边界性能试验方案
- **术语和缩略语**：`{{abbreviations}}`

### 1.2 试验目的
考核`{{equipment_name}}`在`{{environment_desc}}`下的环境适应性和边界性能，发现缺陷，提出改进和使用建议，为`{{purpose}}`提供依据。
**【检索】** 无。

---

## 2. 试验依据
### 2.1 任务来源
`{{task_source}}`
### 2.2 依据文件
列出用户提供的方法标准。
**【检索】** 无。

---

## 3. 试验环境与设备
### 3.1 具体环境下的设备
按测试项目类别列出所需的主要试验设备及其关键性能参数。
**【检索】** 每项**标准中规定的设备能力要求**，以及**标准中规定的试验条件参数**。必须从标准原文检索。

---

## 6. 试验结果评定
`{{equipment_name}}`在各项环境试验前、后的外观/结构检查结果以及功能/性能检测结果满足本方案规定的合格判据时，环境试验结果为合格。
**【检索】** 无。
""".format(
            template_id=template_id,
            user_id=user_id,
            created_at=datetime.now().isoformat(),
            updated_at=datetime.now().isoformat()
        )
        
        return UserTemplate(
            metadata=metadata,
            variables=system_variables,
            sections=required_sections,
            content=default_content,
            retrieval_marks=[{
                "mark": "【检索】",
                "description": "需要从知识库检索相关内容",
                "required": True,
                "can_edit_note": True
            }],
            validation_rules={
                "min_sections": 4,
                "max_sections": 20,
                "required_placeholders": ["{{equipment_name}}", "{{environment_desc}}"]
            }
        )
    
    def save_to_chroma(self, template: UserTemplate) -> bool:
        """将模板保存到Chroma向量数据库"""
        if not self.chroma_collection:
            print("[TemplateManager] Chroma collection not available")
            return False
        
        try:
            # 生成chunk ID
            chunk_id = f"template_{template.metadata.id}"
            
            # 准备文档内容（用于向量检索）
            document_text = f"""
模板名称: {template.metadata.name}
描述: {template.metadata.description}
分类: {template.metadata.category}
内容摘要: {template.content[:1000]}...
""".strip()
            
            # 准备元数据
            metadata = {
                "type": "template",
                "template_id": template.metadata.id,
                "name": template.metadata.name,
                "description": template.metadata.description,
                "category": template.metadata.category,
                "created_by": template.metadata.created_by,
                "created_at": template.metadata.created_at,
                "updated_at": template.metadata.updated_at,
                "is_default": template.metadata.is_default,
                "is_system": template.metadata.is_system,
                "status": template.metadata.status,
                "version": template.metadata.version,
                "variables_count": len(template.variables),
                "sections_count": len(template.sections),
                "has_retrieval_marks": len(template.retrieval_marks) > 0,
                # 存储完整模板数据（JSON序列化）
                "full_data": json.dumps(template.to_dict(), ensure_ascii=False)
            }
            
            # 添加到Chroma
            self.chroma_collection.add(
                ids=[chunk_id],
                documents=[document_text],
                metadatas=[metadata]
            )
            
            print(f"[TemplateManager] Template saved to Chroma: {chunk_id}")
            return True
            
        except Exception as e:
            print(f"[TemplateManager] Failed to save template to Chroma: {e}")
            return False
    
    def load_from_chroma(self, template_id: str) -> Optional[UserTemplate]:
        """从Chroma加载模板"""
        if not self.chroma_collection:
            return None
        
        try:
            # 查询模板
            result = self.chroma_collection.get(
                ids=[f"template_{template_id}"],
                include=["metadatas"]
            )
            
            if not result or not result.get("metadatas"):
                return None
            
            # 解析模板数据
            metadata = result["metadatas"][0]
            full_data = json.loads(metadata.get("full_data", "{}"))
            
            # 重建Template对象
            return self._dict_to_template(full_data)
            
        except Exception as e:
            print(f"[TemplateManager] Failed to load template from Chroma: {e}")
            return None
    
    def search_templates(self, query: str, user_id: str = None, n_results: int = 5) -> List[Dict]:
        """在Chroma中搜索模板"""
        if not self.chroma_collection:
            return []
        
        try:
            # 构建过滤条件
            if user_id:
                where_filter = {"$and": [{"type": "template"}, {"created_by": user_id}]}
            else:
                where_filter = {"type": "template"}
            
            # 执行向量搜索
            results = self.chroma_collection.query(
                query_texts=[query],
                n_results=n_results,
                where=where_filter,
                include=["metadatas", "distances"]
            )
            
            templates = []
            if results and results.get("metadatas"):
                for i, metadata in enumerate(results["metadatas"][0]):
                    try:
                        full_data = json.loads(metadata.get("full_data", "{}"))
                        templates.append({
                            "template": full_data,
                            "distance": results["distances"][0][i] if results.get("distances") else None,
                            "relevance_score": 1 - results["distances"][0][i] if results.get("distances") else 0
                        })
                    except:
                        continue
            
            return templates
            
        except Exception as e:
            print(f"[TemplateManager] Failed to search templates: {e}")
            return []
    
    def delete_from_chroma(self, template_id: str) -> bool:
        """从Chroma删除模板"""
        if not self.chroma_collection:
            return False
        
        try:
            self.chroma_collection.delete(ids=[f"template_{template_id}"])
            print(f"[TemplateManager] Template deleted from Chroma: {template_id}")
            return True
        except Exception as e:
            print(f"[TemplateManager] Failed to delete template: {e}")
            return False
    
    def update_in_chroma(self, template: UserTemplate) -> bool:
        """更新Chroma中的模板"""
        # 先删除旧版本
        self.delete_from_chroma(template.metadata.id)
        # 保存新版本
        return self.save_to_chroma(template)
    
    def _dict_to_template(self, data: Dict) -> UserTemplate:
        """将字典转换为Template对象"""
        metadata = TemplateMetadata(**data["metadata"])
        variables = [TemplateVariable(**v) for v in data.get("variables", [])]
        sections = [TemplateSection(**s) for s in data.get("sections", [])]
        
        return UserTemplate(
            metadata=metadata,
            variables=variables,
            sections=sections,
            content=data.get("content", ""),
            retrieval_marks=data.get("retrieval_marks", []),
            validation_rules=data.get("validation_rules", {})
        )


class TemplateValidator:
    """模板校验器"""
    
    @staticmethod
    def validate(template: UserTemplate) -> Dict[str, Any]:
        """校验模板合法性"""
        errors = []
        warnings = []
        
        # 1. 校验元数据
        if not template.metadata.name.strip():
            errors.append({"field": "name", "message": "模板名称不能为空", "level": "critical"})
        
        # 2. 校验章节
        required_sections = [s for s in template.sections if s.required]
        for req in required_sections:
            if not any(s.id == req.id for s in template.sections):
                errors.append({"field": "sections", "message": f"缺少必要章节: {req.title}", "level": "critical"})
        
        # 3. 校验占位符
        import re
        placeholders = re.findall(r'\{\{(\w+)\}\}', template.content)
        variable_names = [v.name for v in template.variables]
        
        undefined = set(placeholders) - set(variable_names)
        if undefined:
            warnings.append({"field": "placeholders", "message": f"未定义的占位符: {undefined}", "level": "warning"})
        
        # 4. 计算得分
        score = 100
        for error in errors:
            if error["level"] == "critical":
                score -= 30
            elif error["level"] == "error":
                score -= 15
        
        for warning in warnings:
            score -= 5
        
        return {
            "valid": len([e for e in errors if e["level"] in ["critical", "error"]]) == 0,
            "score": max(0, score),
            "errors": errors,
            "warnings": warnings
        }


# 导出便捷函数
def get_template_manager(chroma_collection=None):
    """获取模板管理器实例"""
    return TemplateManager(chroma_collection)


def create_default_template_for_user(user_id: str, chroma_collection=None) -> Optional[UserTemplate]:
    """为用户创建默认模板"""
    manager = TemplateManager(chroma_collection)
    template = manager.create_default_template(user_id)
    
    if chroma_collection:
        manager.save_to_chroma(template)
    
    return template


# 示例用法
if __name__ == "__main__":
    # 测试创建模板
    template = create_default_template_for_user("user_001")
    print(f"Created template: {template.metadata.name}")
    print(f"Variables: {[v.name for v in template.variables]}")
    print(f"Sections: {[s.title for s in template.sections]}")
    
    # 测试校验
    validator = TemplateValidator()
    result = validator.validate(template)
    print(f"Validation result: {result}")
