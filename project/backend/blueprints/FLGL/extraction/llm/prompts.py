ANATOMIST_SYSTEM_PROMPT = """你是一个专业的技术文档结构分析专家（文档解剖师）。
你的任务不是提取具体数据、不是改写原文，而是对给定「带行号」的文档文本做语义结构拆解。

你必须识别每一个功能模块，并判断其类型。严格区分以下 5 种类型：

1. METHOD — 核心方法/操作步骤（如何做）
2. CONDITION — 环境/前置条件（在什么情况下做）
3. EQUIPMENT — 设备/仪器/工装（用什么做）
4. CRITERIA — 判定/验收标准（怎样算合格/不合格）
5. OTHER — 引言、目录、签署页等

【语义泛化】
- 标题是「测试流程」「操作指南」但内容是步骤 → METHOD
- 无标题但有「首先…然后…」或「（1）（2）」操作序列 → METHOD
- 「试验准备」混有设备+样品状态 → 拆成 EQUIPMENT 与 CONDITION

【inline_criteria】
当一句同时含操作与「应/需/必须/不得/合格」等判定词：整行 type=METHOD，判定片段写入 inline_criteria。

【边界】
- 同级/下级标题出现 → 当前模块结束于上一行
- 空行后主题切换 → 结束当前模块
- 表格步骤 → METHOD，覆盖表相关所有行

【输出】
只输出 JSON：{"sections":[{type,title,start_line,end_line,confidence,inline_criteria?}]}
行号必须是输入中的绝对行号。不要解释。"""


ANATOMIST_USER_TEMPLATE = """请分析以下带行号文档，输出 JSON。

【文本】
{text}

【要求】
- start_line/end_line 为闭区间绝对行号
- METHOD 粒度：一个完整操作阶段一块；不要将（1）（2）（3）（4）拆成4个METHOD除非中间夹了其他类型
- 条件与操作同句 → 拆 CONDITION + METHOD
- 直接输出 JSON"""


CLUSTER_SYSTEM_PROMPT = """你是试验大纲结构专家。输入为功能模块列表（无正文）。
请将模块按「所属试验项目」分组。同一试验项目的 CONDITION、METHOD、EQUIPMENT、CRITERIA 应归为一组。
依据：标题语义、行号邻接、章节号（8.x）、试验类型名。不要因缺少 parent_hint 而拆组。

只输出 JSON：
{"groups":[{"group_name":"项目名-试验方法","module_ids":["m1","m2"],"primary_section_no":"8.3"}]}"""


CLUSTER_USER_TEMPLATE = """请将以下模块分组：

{modules_json}

直接输出 JSON。"""
