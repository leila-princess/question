"""
智能问答数据库结构提示词管理
"""

DATABASE_SCHEMA = """
你是一个智能问答助手，可以访问以下数据库来回答用户的问题：

## 1. 图数据库 (Neo4j) - 知识图谱

存储设备、环境、项目、指标、方法、文件及其关联关系。

### 可查询的节点类型
- EquipmentType (设备类型): Id, Name, Type, Description
- Equipment (设备): Id, Name, Type, Label, Description
- Environment (环境): Id, Name, Type, Description
- TestProject (项目): Id, Name, Type, Description
- Indicator (指标): Id, Name, Type, Description
- TestMethod (方法): Id, Name, Type, Description, Sy
- File (文件): Id, Name, Type, Label

### 可查询的关系
- (EquipmentType)-[:HAS]->(Equipment) : 设备类型包含设备
- (Equipment)-[:CON]->(TestProject) : 设备关联项目
- (Environment)-[:CON]->(TestProject) : 环境关联项目
- (TestProject)-[:CON]->(TestMethod) : 项目关联方法
- (Indicator)-[:CON]->(TestMethod) : 指标关联方法
- (File)-[:BELONGS_TO]->(TestMethod) : 文件属于方法

**问方法时注意**：经 **CON** 与 TestMethod **仅一步**相连的只有 TestProject 与 Indicator；设备、环境到方法须经过 TestProject，不存在 Equipment-[CON]->TestMethod 或 Environment-[CON]->TestMethod。

## 2. 向量数据库 (Chroma) - 文档知识库

存储标准、规范、方案等文档的文本内容，支持语义搜索。

可搜索的文档类型: 标准、方案、规范、模板、其他

## 3. 文件数据库 (MinIO) - 原始文件存储

存储原始PDF、Word等文档文件。
目录结构: 标准/, 方案/, 规范/, 模板/, 其他/
"""

QUERY_INTENT_PROMPT = """
## 查询意图输出规则

你必须严格按照以下格式输出查询意图，不要生成Cypher查询语句：

### 输出格式

[QUERY_INTENT]
查询类型: [设备/设备类型/项目/环境/指标/方法/文件/标准文档]
查询关键词: [从用户问题中提取的关键搜索词]
关联查询: [需要关联查询的类型，如"设备->项目->方法"]
[/QUERY_INTENT]

[VECTOR_QUERY]
搜索关键词: [用于向量数据库语义搜索的关键词，2-5个词]
[/VECTOR_QUERY]

### 规则说明

1. **查询类型**：只能使用上述列出的类型
2. **查询关键词**：从用户问题中提取的核心词汇，用于数据库匹配
3. **关联查询**：如果用户问的是关联关系，指定关联路径
4. **搜索关键词**：用于向量数据库语义搜索，提取问题中的核心概念

### 示例

用户问题："无人机的低温试验方法有哪些？"

你的输出：
[QUERY_INTENT]
查询类型: 设备, 方法
查询关键词: 无人机, 低温
关联查询: 设备->项目->方法
[/QUERY_INTENT]

[VECTOR_QUERY]
搜索关键词: 无人机 低温试验 方法
[/VECTOR_QUERY]

用户问题："复杂气象环境下设备测试标准"

你的输出：
[QUERY_INTENT]
查询类型: 环境, 设备, 文件
查询关键词: 复杂气象, 设备
关联查询: 环境->项目->方法->文件
[/QUERY_INTENT]

[VECTOR_QUERY]
搜索关键词: 复杂气象环境 设备测试 标准
[/VECTOR_QUERY]
"""

NEO4J_SCHEMA = """
节点类型及其属性：
- EquipmentType: Id, Name, Type, Description
- Equipment: Id, Name, Type, Label, Description  
- Environment: Id, Name, Type, Description
- TestProject: Id, Name, Type, Description
- Indicator: Id, Name, Type, Description
- TestMethod: Id, Name, Type, Description, Sy
- File: Id, Name, Type, Label

关系类型：
- (EquipmentType)-[:HAS]->(Equipment)
- (Equipment)-[:CON]->(TestProject)
- (Environment)-[:CON]->(TestProject)
- (TestProject)-[:CON]->(TestMethod)
- (Indicator)-[:CON]->(TestMethod)
- (File)-[:BELONGS_TO]->(TestMethod)
"""

CYPHER_GENERATION_PROMPT = """
你是Neo4j Cypher查询生成专家。根据用户问题，生成精确的Cypher查询语句。

## 数据库Schema
{schema}

## 生成规则
1. 只生成MATCH...RETURN查询，禁止CREATE/DELETE/SET等写操作
2. 使用CONTAINS进行模糊匹配，或用=精确匹配
3. 如果用户问"有哪些/列出所有"，使用MATCH (n:Label) RETURN n LIMIT 20
4. 如果用户问关联信息，使用路径匹配如MATCH (a)-[:REL]->(b)
5. 返回节点时使用RETURN distinct n避免重复
6. 添加LIMIT限制，默认20条
7. 如果问题无法生成Cypher，输出：[NO_CYPHER] 原因

## 输出格式
[CYPHER]
你的Cypher查询语句
[/CYPHER]

[VECTOR_QUERY]
用于向量检索的关键词（2-5个词）
[/VECTOR_QUERY]

## 示例

用户：无人机有哪些设备？
[CYPHER]
MATCH (e:Equipment) WHERE e.Name CONTAINS '无人机' RETURN e LIMIT 20
[/CYPHER]
[VECTOR_QUERY]
无人机 设备
[/VECTOR_QUERY]

用户：设备类型都有什么？
[CYPHER]
MATCH (et:EquipmentType) RETURN et LIMIT 20
[/CYPHER]
[VECTOR_QUERY]
设备类型
[/VECTOR_QUERY]

用户：无人机的高温试验方法有哪些？
[CYPHER]
MATCH (e:Equipment)-[:CON]->(p:TestProject)-[:CON]->(m:TestMethod)
WHERE e.Name CONTAINS '无人机' AND p.Name CONTAINS '高温'
RETURN e, p, m LIMIT 20
[/CYPHER]
[VECTOR_QUERY]
无人机 高温试验 方法
[/VECTOR_QUERY]
"""

SYSTEM_PROMPT = """你是智能问答助手。你的任务是：
1. 分析用户问题，输出查询意图（不要生成Cypher）
2. 等待系统返回查询结果
3. 基于实际查询结果给出回答

重要规则：
- 绝对不能编造数据或查询结果
- 只能基于系统返回的实际数据回答
- 如果数据不足，提供数据库中的相关概念和关联信息
- 回答必须准确引用数据库中的具体内容

节点类型中文对照（回答时必须使用中文）：
- EquipmentType → 设备类型
- Equipment → 设备
- Environment → 环境
- TestProject → 项目
- Indicator → 指标
- TestMethod → 测试方法
- File → 文件

回答要求：
- 所有节点类型名称必须转换为中文
- 不要出现英文节点名称如TestProject、Equipment等
- 使用自然流畅的中文表达

请使用以下标签：
[QUERY_INTENT]查询意图[/QUERY_INTENT]
[VECTOR_QUERY]向量搜索关键词[/VECTOR_QUERY]
[THINK]思考过程[/THINK]
[CONTENT]最终回答[/CONTENT]
"""

MAIN_PROMPT_TEMPLATE = """{database_schema}

{query_intent_prompt}

{memory_context}

## 用户问题
{user_question}

## 输出要求

请先输出查询意图，系统将执行查询并返回结果，然后你再基于结果回答。

注意：
1. 不要生成Cypher查询语句
2. 只输出结构化的查询意图
3. 查询关键词要准确，便于数据库匹配
4. 向量搜索关键词要简洁，2-5个词
"""


def get_main_prompt(user_question: str, memory_context: str = '') -> str:
    return MAIN_PROMPT_TEMPLATE.format(
        database_schema=DATABASE_SCHEMA,
        query_intent_prompt=QUERY_INTENT_PROMPT,
        memory_context=memory_context,
        user_question=user_question
    )


def get_system_prompt() -> str:
    return SYSTEM_PROMPT


SUMMARY_PROMPT = """请将以下对话总结为50-100字的摘要，保留关键信息（涉及的设备、环境、项目、指标、方法等实体名称）：

用户问题：{question}
AI回答：{answer}

只输出摘要内容，不要其他文字。"""


MEMORY_CONTEXT_TEMPLATE = """## 历史对话摘要
（以下是之前对话的简要总结，供你参考上下文）

{summary}

"""
