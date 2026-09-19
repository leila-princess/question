import pathlib
import json
from openai import OpenAI  # 如果用Qwen或其他API，请替换为对应SDK

client = OpenAI(
    api_key="REPLACE_WITH_YOUR_API_KEY",
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
)
MODEL = "qwen-plus"


def read_markdown(md_path):
    """读取Markdown文件内容为字符串"""
    return pathlib.Path(md_path).read_text(encoding="utf-8")

def build_prompt(md_text):
    """构造提示词，要求大模型返回严格JSON格式"""
    return f"""
### **提示词：标准文档知识图谱构建专家**

#### **1. 你的角色与任务**
你是一名专业的知识工程师。你的任务是将输入的标准文档全文，转化为一个结构化的、包含实体（节点）和关系（边）的JSON对象，以便直接导入图数据库（如Neo4j）或用于构建知识图谱。

#### **2. 核心处理逻辑**
你必须执行以下两个核心步骤，但最终输出仅为一步到位的JSON结果。

1.  **深度解析与抽取：**
    *   对文档进行解构，识别出所有关键实体（如标准、章节、术语、机构、方法等）以及它们之间的关系（如发布、定义、引用、使用等）。
    *   每个最底层的标题（例如：章节、条、款、项、附录等）必须将正文内容存放在该节点的 `definition` 属性中。
    *   如果正文中仍然存在子结构（如 a、b、c 或 1、2、3、i、ii、iii），必须把这些子内容解析为新的 `Clause` 节点，而不是合并在父条款正文里。
    *   父级条款节点的 `definition` 只存放属于该层级自身的正文，不包括拆分出去的子条款。
    *   **特别地，对于表格：**
        *   将每个表格识别为一个 `Table` 实体。
        *   **必须完整解析表格结构**，包括表头、行列数据以及任何**合并单元格**。需通过分析行列布局和内容推断合并关系。
        *   在 `tableData` 属性中，需使用结构化对象（如包含 `headers`, `rows`, `merges` 等字段）精确表示表格原始形态，确保信息无损且能逆向还原表格。
        *   明确建立 `CONTAINS_TABLE` 关系（从包含它的标准或条款指向表格）和 `REFERENCED_BY` 关系（从表格指向引用它的条款）。

2.  **知识图谱建模：**
    *   将抽取到的信息建模为两个核心集合：
        *   `nodes`: 代表所有实体，每个实体都是一个对象，包含唯一标识符 (`id`)、标签 (`labels`，代表实体类型) 和属性 (`properties`)。
        *   `relationships`: 代表所有关系，每个关系都是一个对象，包含唯一标识符 (`id`)、类型 (`type`)、起始节点ID (`startNodeId`) 和终止节点ID (`endNodeId`)。
    *   父子条款之间用 `HAS_PART` 关系链接，确保图谱能展开到最细的层级。

#### **3. 输出格式要求**
你必须严格遵循以下JSON Schema输出结果，不能包含任何额外的解释或文本。你的整个回答必须是且仅是一个合法的JSON对象。

```json
{{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "type": "object",
  "properties": {{
    "metadata": {{
      "type": "object",
      "properties": {{
        "standardNumber": {{"type": "string"}},
        "standardTitle_zh": {{"type": "string"}},
        "standardTitle_en": {{"type": "string"}},
        "publishingOrganization": {{"type": "string"}},
        "issueDate": {{"type": "string"}},
        "implementationDate": {{"type": "string"}},
        "status": {{"type": "string"}}
      }},
      "required": ["standardNumber", "standardTitle_zh", "publishingOrganization"]
    }},
    "nodes": {{
      "type": "array",
      "items": {{
        "type": "object",
        "properties": {{
          "id": {{"type": "string"}},
          "labels": {{
            "type": "array",
            "items": {{"type": "string"}}
          }},
          "properties": {{
            "type": "object",
            "properties": {{
              "name": {{"type": "string"}},
              "definition": {{"type": "string"}},
              "value": {{"type": "string"}},
              "context": {{"type": "string"}},
              "number": {{"type": "string"}},
              "date": {{"type": "string"}},
              "tableData": {{
                "type": "object",
                "properties": {{
                  "headers": {{"type": "array", "items": {{"type": "string"}}}},
                  "rows": {{"type": "array", "items": {{"type": "array", "items": {{"type": ["string", "number", "null"]}}}}}},
                  "merges": {{
                    "type": "array",
                    "items": {{
                      "type": "object",
                      "properties": {{
                        "rowStart": {{"type": "number"}},
                        "colStart": {{"type": "number"}},
                        "rowSpan": {{"type": "number"}},
                        "colSpan": {{"type": "number"}}
                      }},
                      "required": ["rowStart", "colStart", "rowSpan", "colSpan"]
                    }}
                  }}
                }},
                "required": ["headers", "rows"]
              }},
              "imagePath": {{"type": "string"}}
            }},
            "required": ["name"]
          }}
        }},
        "required": ["id", "labels", "properties"]
      }}
    }},
    "relationships": {{
      "type": "array",
      "items": {{
        "type": "object",
        "properties": {{
          "id": {{"type": "string"}},
          "type": {{"type": "string"}},
          "startNodeId": {{"type": "string"}},
          "endNodeId": {{"type": "string"}},
          "properties": {{"type": "object"}}
        }},
        "required": ["id", "type", "startNodeId", "endNodeId"]
      }}
    }}
  }},
  "required": ["metadata", "nodes", "relationships"]
}}
```

#### **4. 实体与关系定义（你的内部处理指南）**
为确保一致性，请使用以下本体进行实体分类和关系定义。

*   **实体类型 (用于 `labels`):**
    *   `Standard`: 标准本身。
    *   `Clause`: 章节、条、款、项、附录。属性应包含编号 (`number`) 和标题 (`name`)。
    *   `Term`: 术语。属性应包含术语 (`name`) 和定义 (`definition`)。
    *   `Organization`: 机构。
    *   `Person`: 人员。
    *   `TestMethod`: 试验方法。
    *   `Condition`: 试验条件。属性应包含数值 (`value`) 和上下文 (`context`)。
    *   `Device`: 设备、仪器。
    *   `Material`: 材料。
    *   `Table`: **表格**。属性应包含编号 (`number`)、标题 (`name`) 和**完整数据及结构 (`tableData`)**，`tableData` 需包含 `headers`（表头数组）、`rows`（数据二维数组）和 `merges`（合并单元格信息数组）等字段以还原复杂表格。
    *   `Figure`: 图/图片。属性应包含编号 (`number`)、标题 (`name`) 和本地路径 (`imagePath`)。

*   **关系类型 (用于 `type`):**
    *   `HAS_PART`: 标准 -> 章节，章节 -> 子条款。
    *   `DEFINES`: 标准/术语条款 -> 术语。
    *   `PUBLISHED_BY`: 标准 -> 机构。
    *   `DRAFTED_BY`: 标准 -> 人员。
    *   `CITES`: 标准/条款 -> 被引用的标准。
    *   `USES_METHOD`: 标准/条款 -> 试验方法。
    *   `UNDER_CONDITION`: 试验方法 -> 条件。
    *   `REQUIRES_DEVICE`: 试验方法 -> 设备。
    *   `REQUIRES_MATERIAL`: 试验方法 -> 材料。
    *   `CONTAINS_TABLE`: 标准/条款 -> 表格。
    *   `CONTAINS_FIGURE`: 标准/条款 -> 图/图片。
    *   `REFERENCED_BY`: 表格/图 -> 引用它的条款。

*   **ID 生成规则 (关键):**
    *   `Standard`: `std_[标准号]` (e.g., `std_GB/T12345-2010`)
    *   `Clause`: `clause_[标准号]_[条款编号]` (e.g., `clause_GB/T12345-2010_4.5.2`)
    *   `Term`: `term_[标准号]_[术语名]` (e.g., `term_GB/T12345-2010_耐电弧性`)
    *   `Organization`: `org_[机构名]` (e.g., `org_国家标准化管理委员会`)
    *   `Person`: `person_[人名]`
    *   `TestMethod`: `method_[方法名]`
    *   `Condition`: `cond_[标准号]_[简短上下文哈希]` (e.g., `cond_GB/T12345-2010_a3f8`)
    *   `Device`: `device_[设备名]`
    *   `Material`: `mat_[材料名]`
    *   `Table`: `table_[标准号]_[表格编号或标记]` (e.g., `table_GB/T12345-2010_A1`)
    *   `Figure`: `figure_[标准号]_[图片编号或标记]` (e.g., `figure_GB/T12345-2010_Fig1`)

#### **5. 对表格和图片的特殊处理说明 (增强版)**

1.  **表格处理 (增强):**
    *   将文档中的每个表格识别为一个 `Table` 实体。
    *   在 `properties` 中，`name` 属性为表格标题，`number` 属性为表格编号（如“表 1”）。
    *   **必须**包含 `tableData` 属性，其值为一个结构化对象，用于**完整且精确地表示整个表格的内容和结构**，特别是处理**多行列合并**的情况。
    *   `tableData` 对象应包含：
        *   `headers`: 数组，表示表头行的内容。对于复杂表头（多行表头或有合并），需按视觉顺序和逻辑推断出最有效的单行表头表示，或在 `merges` 中详细描述合并关系。
        *   `rows`: 二维数组，表示表格数据行的内容。空单元格或合并单元格导致的空缺位置可用 `null` 或空字符串 `""` 占位。
        *   `merges`: (可选但强烈推荐用于复杂表格) 数组，包含描述单元格合并情况的对象。每个对象定义一個合并区域，包含 `rowStart` (起始行索引), `colStart` (起始列索引), `rowSpan` (合并行数), `colSpan` (合并列数)。行和列的索引从0开始。
    *   创建 `CONTAINS_TABLE` 关系，从包含该表格的 `Standard` 或 `Clause` 节点指向此 `Table` 节点。
    *   如果条款正文中明确引用了该表格（如“见表1”），创建 `REFERENCED_BY` 关系，从 `Table` 节点指向引用它的 `Clause` 节点。

2.  **图片处理:**
    *   将文档中的每个图（Figure）识别为一个 `Figure` 实体。
    *   在 `properties` 中，`name` 属性为图标题，`number` 属性为图编号（如“图 1”）。
    *   **必须**包含 `imagePath` 属性，其值为字符串，格式为 `[页数]_img[顺序数]`（例如 `15_img2`），对应本地存储的图片文件。请根据图片在文档中出现的顺序和所在页码确定此路径。
    *   创建 `CONTAINS_FIGURE` 关系，从包含该图的 `Standard` 或 `Clause` 节点指向此 `Figure` 节点。
    *   如果条款正文中明确引用了该图（如“见图1”），创建 `REFERENCED_BY` 关系，从 `Figure` 节点指向引用它的 `Clause` 节点。
# 标准文档全文内容如下：
{md_text}
**现在，请开始处理输入的标准文档文本，并输出符合上述要求的JSON对象，不要有任何解释或多余内容。对于表格，务必解析其完整结构，包括合并单元格。不要省略，不要只输出最重要的，要的是全部！输出文档的全部实体和关系，包括所有表格和图片。我将直接根据你的答案构建知识图谱，所以答案一定要准确、全面。如果内容过多，可告知并询问是否继续或分次输出。**


"""

def call_llm(prompt, model="qwen-plus"):
    """调用大模型API，返回结果字符串"""

    completion = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}]
    )
    return completion.choices[0].message.content

def save_json(data, out_path):
    """保存JSON数据到文件"""
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def main():
    # 1. 读取Markdown文件
    md_file = "0903output/standardtxt/GBT+38924.7-2020_parsed.md"  # 替换为你的文件名
    md_text = read_markdown(md_file)

    # 2. 构造prompt
    prompt = build_prompt(md_text)

    # 3. 调用大模型API
    response_text = call_llm(prompt)

    # 4. 解析JSON
    try:
        result = json.loads(response_text)
    except Exception as e:
        print("模型返回内容不是有效JSON，请检查prompt或模型输出。")
        print(response_text)
        return

    # 5. 保存JSON
    out_file = pathlib.Path(md_file).stem + "_kg.json"
    save_json(result, out_file)
    print(f"知识图谱数据已保存到 {out_file}")

if __name__ == "__main__":
    main()