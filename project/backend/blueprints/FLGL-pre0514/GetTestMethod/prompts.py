import json
from typing import Any, Dict, List


DISCOVERY_PROMPT = """
你是一名“标准文件方法发现与结构化抽取专家”。

你的任务是：根据标准文件的章节目录、章节摘要和正文片段，
1）判断该文件属于“总则”还是“分则”；
2）提取全文件术语与定义；
3）识别适合构建为 TestMethod 的方法候选。

【最重要的总原则】
“方法”必须是一个**独立的试验项**，或者在总则场景下，一个**独立的通用实施方法模板**；
而不是某个试验项的组成章节。

【本次输出目标】
1. 判断文件 class：只能是“总则”“分则”“未判定”三者之一
2. 提取 terms：优先从“术语和定义”章节提取
3. 提取 method_candidates
4. 每个 method_candidate 必须尽量绑定 section_ids，后续程序会按 section_ids 继续细化抽取

【总则与分则的区别】
1. 分则：
   - 通常对应一个或多个具体试验项
   - 例如：高温试验、低温试验、振动试验、积冰/冻雨试验
   - 若标题已明确是单一试验名，则优先只输出 1 个方法

2. 总则：
   - 通常不对应某个具体试验项
   - 主要规定通用条件、通用准备、通用顺序、通用中断处理、通用分析、通用报告、通用监测等
   - 如果正文没有多个清晰独立的具体试验项，则默认整份文件只输出 1 个“通用要求/通用实施模板”方法候选
   - 不能把“试验顺序、试验准备、试验中断、结果分析、监控监测”等通用章节拆成多个方法

【方法识别总规则】
1. 如果文档标题、分部分标题或主标题已经明确给出一个单一试验名称
   例如：
   - “第22部分：积冰/冻雨试验”
   - “高温试验”
   - “低温试验”
   - “振动试验”
   且正文后续章节主要是“试验条件、试验要求、试验过程、结果分析、信息要求、术语、剪裁指南”等，
   则默认整份文件只对应 **1 个方法**。

2. 下列内容默认不是独立方法，除非文档明确把它们命名为不同的并列试验项：
   - 试验条件 / 确定试验条件
   - 试验要求
   - 试验程序 / 试验过程 / 试验步骤 / 试验准备
   - 结果分析 / 结果评定 / 判定规则
   - 信息要求
   - 术语和定义
   - 范围 / 引用文件 / 前言
   - 剪裁指南 / 工作考虑
   - 试验顺序
   - 监控和监测
   这些内容通常是同一主方法的组成部分，应归并到主方法，而不是单独输出为方法。

3. 只有在文档中出现多个并列、命名清晰、边界独立、目的不同的试验项时，才允许输出多个方法。
   例如：
   - 高温试验 / 低温试验 / 湿热试验
   - 冲击试验 / 振动试验 / 跌落试验
   - 方法A / 方法B / 方法C
   这类并列主试验项，才是多个 method_candidates。

4. 总则文件可以有多个方法候选，但前提是正文里真的存在多个并列、独立、命名清晰的“通用方法模块”；
   如果只是很多通用章节，则只能输出 1 个通用方法候选。

5. 分则文件要优先判断是否属于“单一试验项标准”。
   若是单一试验项标准，则通常只输出 1 个方法候选。

【分则单方法优先规则】
当文档被判定为“分则”，且标题中包含明确的单一试验名称（如“第XX部分：XX试验”），
除非正文存在两个及以上**同层级、名称不同、目的不同、适用对象不同**的试验项，
否则只能输出 1 个方法候选。

【总则单方法优先规则】
当文档被判定为“总则”，且正文主要内容是通用条件、允许偏差、试验顺序、试验准备、试验中断处理、
结果分析、试验报告、监控和监测等一般性要求，
且没有出现多个清晰、独立、命名明确的具体试验项，
则默认整份文件只输出 1 个通用方法候选。

这个唯一候选应表示类似：
- “军用装备实验室环境试验通用要求”
- “环境试验通用实施要求”
- “通用环境试验方法要求”
这样的整体概念。

【反例提示：以下一般不是独立方法】
即使这些章节内容很多、写得很完整，也通常不应单独输出为 method_candidate：
- “试验程序”
- “试验条件确定”
- “试验要求”
- “结果分析”
- “试件安装与调试”
- “试验前准备”
- “信息要求”
- “试验顺序”
- “监控和监测”
因为它们通常只是主方法的步骤、条件、保障要求、评定内容或通用实施规则。

【什么算一个方法 candidate】
必须尽量满足以下特征：
- 能独立表示一个试验项目/试验方法/试验类别
- 或者在总则场景下，能独立表示“通用试验实施模板”
- 有清晰的方法名称，而不只是泛化章节名
- 可以单独回答“这是什么方法，它要做什么”

【总则候选命名要求】
若文档是总则且只输出 1 个方法候选：
- candidate_title 不要写成某个章节名，如“试验准备”“结果分析”
- 应写成整体名称，例如：
  - “军用装备实验室环境试验通用要求”
  - “环境试验通用实施要求”
  - “通用环境试验要求”

【terms 要求】
- 只保留“术语 + 明确定义”
- 优先中文术语
- 去重
- 不要把普通描述句误当成术语

【method_candidates 要求】
每个元素格式：
{{
  "candidate_id": "M001",
  "candidate_title": "方法标题",
  "candidate_type_hint": "类型提示",
  "summary_hint": "一句话说明该方法做什么",
  "section_ids": ["S003", "S007"],
  "reason": "为什么这些 section 属于该方法"
}}

规则：
- candidate_id 按 M001、M002 编号
- section_ids 必须从提供的章节目录里选择
- 一个方法可以绑定多个 section_ids
- candidate_title 尽量贴近原文主试验名称或总则整体名称
- 不要编造章节 id
- 不要为了“全面”而把附属章节拆成独立方法
- 若判断为单一试验项标准，method_candidates 应只输出 1 个
- 若判断为典型总则文件，method_candidates 通常也只输出 1 个

【输出格式】
{{
  "document_class": "总则",
  "document_summary": "对整份标准文件的简要总结",
  "terms": [
    {{
      "term": "术语名称",
      "definition": "术语定义"
    }}
  ],
  "method_candidates": [
    {{
      "candidate_id": "M001",
      "candidate_title": "方法标题",
      "candidate_type_hint": "类型提示",
      "summary_hint": "一句话说明",
      "section_ids": ["S001"],
      "reason": "说明"
    }}
  ]
}}

【额外约束】
- 输出必须是纯 JSON
- 不要输出解释
- 不要输出 markdown
- 不要输出额外文字
- 如果不确定是否应拆分，优先少拆，不要过拆
- 总则优先输出“通用整体方法”
- 分则优先输出“具体试验方法”

【输入】
文件名：
{file_name}

章节目录与摘要：
{section_catalogue}

正文前文预览：
{document_preview}
""".strip()


DETAIL_PROMPT = """
你是一名“标准文件方法细粒度抽取专家”。

你的任务是：针对一个已经发现的方法候选，从相关章节正文和表格上下文中，
抽取该方法的结构化信息，并把同一主方法的“试验条件 / 试验过程 / 结果评定”整合到一个 method 对象中。

【最重要的抽取原则】
1. 当前输入的 candidate 是一个“主方法”候选，或者在总则场景下的“通用实施模板方法”候选。
2. 不要把“试验程序”“试验条件确定”“试验要求”“结果分析”“监控和监测”等 generic 章节名当成新的方法名称。
3. 如果相关章节属于同一主方法的组成部分，应整合进同一个 method 对象：
   - SYTJ：试验条件、试验要求中与条件/设备/控制/安装相关的内容
   - SYGC：试验准备、试验步骤、试验程序、试验中断与恢复、监控实施等过程内容
   - JGPD：结果分析、结果评定、判定规则、试后检查、试验报告中与评定直接相关的内容
4. 如果候选方法本身就是单一试验项标准的主方法，那么要尽量把相关章节内容补全到这个主方法中。
5. 如果候选方法来自总则，则要把总则中的“通用条件 / 通用过程 / 通用评定”分别汇总到 SYTJ / SYGC / JGPD。

【抽取目标】
你需要输出一个 method 对象，包含：
1. Name：方法名称
2. Type：方法类型提示
3. Description：方法摘要/总结
4. Label：这里不要自由标签，直接输出 recall_plan
5. SYTJ：试验条件
6. SYGC：试验过程
7. JGPD：结果评定
8. class：继承文档级 class，仅回填当前方法看到的判断
9. section_ids：回填当前方法涉及章节 id

【Name 与 Type 规则】
1. 若当前方法来自分则：
   - name 应优先保留主试验名称，例如“积冰/冻雨试验”
   - 不要把 name 写成“试验程序”“试验要求”“试验条件确定”这类泛章节名，除非该 candidate 本身明确就是独立方法

2. 若当前方法来自总则：
   - name 应优先体现“通用要求/通用实施要求/总则方法模板”的整体含义
   - 不要写成某一个章节名
   - 推荐写法例如：
     - “军用装备实验室环境试验通用要求”
     - “环境试验通用实施要求”
     - “通用环境试验要求”

3. type 可以适度概括：
   - 分则：环境试验方法 / 环境适应性试验方法 / 试验方法
   - 总则：总则 / 通用实施要求 / 通用方法模板

【Description 要求】
- Description 是方法摘要，不要把全文大段复制进去
- 若为分则，应概括：
  - 该方法用于做什么
  - 主要适用对象
  - 主要环境或试验应力
  - 核心过程或关键参数
  - 主要评定目标
- 若为总则，应概括：
  - 它为哪些试验提供通用依据
  - 涵盖哪些通用条件、准备、实施、处理中断、分析、报告、监测要求

【recall_plan 要求】
必须分别给 equipment / environment / test_project / indicator 四类关键词及 reason

格式：
{{
  "equipment": {{
    "keywords": ["..."],
    "reason": "..."
  }},
  "environment": {{
    "keywords": ["..."],
    "reason": "..."
  }},
  "test_project": {{
    "keywords": ["..."],
    "reason": "..."
  }},
  "indicator": {{
    "keywords": ["..."],
    "reason": "..."
  }}
}}

规则：
- equipment：重点是被试对象/适用对象/系统对象
- environment：重点是环境条件短语，不要长句
- test_project：重点是试验项目短语；若为总则，可写“环境试验实施”“试验顺序确定”“试验条件确定”“试验中断处理”等通用项目短语
- indicator：重点是评定指标、性能、状态、功能、失效判据等

【SYTJ / SYGC / JGPD 抽取规则 — 核心原则：原文复制，逐字粘贴】
你现在的角色不是'AI助手'，而是一个'文本分类+复制工具'。
对于 SYTJ / SYGC / JGPD 三个块的 text 字段，你必须严格执行以下操作：

第一步：阅读【相关章节正文】，找到所有与'试验条件'相关的段落
第二步：把这些段落**原封不动、逐字复制**到 sytj.text 中
第三步：找到所有与'试验过程'相关的段落，**原封不动、逐字复制**到 sygc.text 中
第四步：找到所有与'结果评定'相关的段落，**原封不动、逐字复制**到 jgpd.text 中

【什么叫做'原封不动、逐字复制'】
- 原文写'温度：+40℃±2℃'，你就写'温度：+40℃±2℃'，不能写成'温度为40摄氏度正负2度'
- 原文写'1）将试件放入试验箱'，你就写'1）将试件放入试验箱'，不能写成'首先将试件放入试验箱'
- 原文的编号、标点、换行、空格、错别字，全部保留，一个字都不能改
- 如果原文有多个段落，用两个换行（\n\n）把它们连接起来
- 绝对禁止：总结、概括、改写、润色、翻译、解释、补充

【判断哪些段落属于哪一类】
1. SYTJ（试验条件）：原文中描述试验环境参数、设备要求、安装条件、大气条件、温度/湿度/压力数值、允许偏差等内容的段落
2. SYGC（试验过程）：原文中描述试验步骤、操作程序、初始检测、试验持续时间、中断处理、恢复程序、最终检测等内容的段落
3. JGPD（结果评定）：原文中描述合格/不合格判定标准、性能指标要求、损坏/失效判据、试后检查要求等内容的段落

【表格和公式要求】
- 如果表格属于试验条件，应放入 sytj.tables_html
- 如果表格属于试验过程，应放入 sygc.tables_html
- 如果表格属于结果评定，应放入 jgpd.tables_html
- 如果公式可以识别，应尽量转为 LaTeX 放入 formulas_latex
- 不要编造表格或公式
- 没有就返回空数组

【缺失内容处理】
- 若某部分确实没有明确内容，text 写'文档未明确说明'
- 但不要因为当前候选章节本身偏标题/范围，就忽略其他相关章节中已经存在的 SYTJ / SYGC / JGPD 信息
- 应优先综合'相关章节正文'中能支持主方法的信息

【method 输出格式】
{{
  "candidate_id": "M001",
  "class": "总则",
  "section_ids": ["S001"],
  "name": "方法名称",
  "type": "方法类型",
  "description": "方法摘要",
  "recall_plan": {{
    "equipment": {{
      "keywords": ["..."],
      "reason": "..."
    }},
    "environment": {{
      "keywords": ["..."],
      "reason": "..."
    }},
    "test_project": {{
      "keywords": ["..."],
      "reason": "..."
    }},
    "indicator": {{
      "keywords": ["..."],
      "reason": "..."
    }}
  }},
  "sytj": {{
    "text": "（把原文中所有与试验条件相关的段落逐字复制到这里，一个字都不能改）",
    "tables_html": ["<table>...</table>"],
    "formulas_latex": ["T = ..."]
  }},
  "sygc": {{
    "text": "（把原文中所有与试验过程相关的段落逐字复制到这里，一个字都不能改）",
    "tables_html": [],
    "formulas_latex": []
  }},
  "jgpd": {{
    "text": "（把原文中所有与结果评定相关的段落逐字复制到这里，一个字都不能改）",
    "tables_html": [],
    "formulas_latex": []
  }}
}}

【额外约束】
- 一个方法只能输出一个对象
- 输出必须是纯 JSON
- 不要输出解释
- 不要输出 markdown
- 不要输出额外文字

【输入】
文件名：
{file_name}

文档级 class：
{document_class}

候选方法：
{method_candidate}

相关章节正文：
{related_sections}

相关表格上下文：
{related_tables}
""".strip()


DETAIL_PARTIAL_MERGE_PROMPT = """
你是一名"标准文件方法细粒度抽取专家"。当前任务是对**同一方法**分多批阅读章节：此前已从第 1 批得到完整方法骨架，现在请**仅根据本批章节与表格**补充 SYTJ（试验条件）、SYGC（试验过程）、JGPD（结果评定）的正文与表格、公式。

【核心原则：原文引用，禁止改写】
- **必须直接从本批相关章节正文中复制原文**，逐字逐句搬运，不得用自己的话改写、总结、概括
- 你只需要判断某段原文属于"试验条件"还是"试验过程"还是"结果评定"，然后把原文粘贴到对应位置
- 保持原文的编号、格式、段落结构不变
- 禁止概括、总结、提炼、润色

【禁止修改】name、type、description、class、section_ids、recall_plan：本任务不要求输出这些字段，模型也不得臆造对它们的修改。

【已有方法骨架】（仅供参考上下文，保持术语一致）
{skeleton_json}

【批次】第 {chunk_index} / {chunk_total} 批

【本批相关章节正文】
{related_sections}

【本批相关表格】
{related_tables}

【输出要求】
- 只输出纯 JSON 对象，且**仅包含**三个键：sytj、sygc、jgpd
- 结构与主流程一致：
  "sytj": {{"text": "...", "tables_html": [], "formulas_latex": []}}
- 若本批对某块无新增信息，对应 text 可为空字符串 ""，tables_html / formulas_latex 为空数组 []
- 不要输出 markdown、不要解释
""".strip()


DETAIL_CALIBRATION_PROMPT = """
你是一名“标准文件方法抽取校准专家”。

当前流程是“两阶段”：
1) 规则引擎已经从文档中初步抽取了该方法的 SYTJ/SYGC/JGPD；
2) 你现在只做“校验与校准”，不是从零总结。

【你的任务】
- 结合候选方法、相关章节与表格，对“规则预抽取结果”进行核对；
- 保留正确内容，纠正错误归类，补充规则漏掉但原文明确存在的关键段落；
- 输出一个完整 method JSON 对象。

【关键约束】
1. 优先保留规则抽取中已命中的原文段落，不要无故删除。
2. 对于 SYTJ / SYGC / JGPD 的 text，优先使用原文表述，不要泛化总结。
3. 如果某部分确实无明确信息，才写“文档未明确说明”。
4. 不要编造章节、参数、表格、公式。
5. 输出必须是纯 JSON，不要 markdown，不要解释文字。

【输出格式】
{{
  "candidate_id": "M001",
  "class": "总则",
  "section_ids": ["S001"],
  "name": "方法名称",
  "type": "方法类型",
  "description": "方法摘要",
  "recall_plan": {{
    "equipment": {{"keywords": [], "reason": ""}},
    "environment": {{"keywords": [], "reason": ""}},
    "test_project": {{"keywords": [], "reason": ""}},
    "indicator": {{"keywords": [], "reason": ""}}
  }},
  "sytj": {{"text": "", "tables_html": [], "formulas_latex": []}},
  "sygc": {{"text": "", "tables_html": [], "formulas_latex": []}},
  "jgpd": {{"text": "", "tables_html": [], "formulas_latex": []}}
}}

【输入】
文件名：
{file_name}

文档级 class：
{document_class}

候选方法：
{method_candidate}

规则预抽取结果：
{rule_based_method}

相关章节正文：
{related_sections}

相关表格上下文：
{related_tables}
""".strip()


def _json_dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def build_discovery_prompt(
    file_name: str,
    section_catalogue: List[Dict[str, Any]],
    document_preview: str,
) -> str:
    return DISCOVERY_PROMPT.format(
        file_name=file_name,
        section_catalogue=_json_dumps(section_catalogue),
        document_preview=document_preview,
    )


def build_detail_prompt(
    file_name: str,
    document_class: str,
    method_candidate: Dict[str, Any],
    related_sections: List[Dict[str, Any]],
    related_tables: List[Dict[str, Any]],
    rule_pre_extract: Dict[str, Any] = None,
) -> str:
    if rule_pre_extract is None:
        rule_pre_extract = {}
    return DETAIL_PROMPT.format(
        file_name=file_name,
        document_class=document_class,
        method_candidate=_json_dumps(method_candidate),
        related_sections=_json_dumps(related_sections),
        related_tables=_json_dumps(related_tables),
        rule_pre_extract=_json_dumps(rule_pre_extract),
    )


def build_detail_partial_prompt(
    skeleton: Dict[str, Any],
    related_sections: List[Dict[str, Any]],
    related_tables: List[Dict[str, Any]],
    chunk_index: int,
    chunk_total: int,
) -> str:
    return DETAIL_PARTIAL_MERGE_PROMPT.format(
        skeleton_json=_json_dumps(skeleton),
        chunk_index=chunk_index,
        chunk_total=chunk_total,
        related_sections=_json_dumps(related_sections),
        related_tables=_json_dumps(related_tables),
    )


def build_detail_calibration_prompt(
    file_name: str,
    document_class: str,
    method_candidate: Dict[str, Any],
    rule_based_method: Dict[str, Any],
    related_sections: List[Dict[str, Any]],
    related_tables: List[Dict[str, Any]],
) -> str:
    return DETAIL_CALIBRATION_PROMPT.format(
        file_name=file_name,
        document_class=document_class,
        method_candidate=_json_dumps(method_candidate),
        rule_based_method=_json_dumps(rule_based_method),
        related_sections=_json_dumps(related_sections),
        related_tables=_json_dumps(related_tables),
    )