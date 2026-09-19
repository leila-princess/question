"""引导式方法提取：通用提示词（结构画像驱动，不写死具体文档格式）。"""

GUIDED_CHUNK_MEMORY_PROMPT = """
你是试验文档理解专家。阅读目录与正文片段，**仅**输出本片理解记忆，不要列出 methods。

【要求】
1. 输出纯 JSON，无 markdown 代码块。
2. chunk_memory：Markdown，按章节组织，必须引用 section_id（来自目录）。
3. 区分「试验方法正文」与「非方法区」：设备清单、title 以 | 开头的表行、纯指标汇总表、引用文件/前言。
4. **完整试验方法**语义上通常至少含三块（标题可改名、可合并小节）：
   - 试验条件 / 测试条件 / 工况 / 样品要求
   - 试验方法 / 试验过程 / 操作步骤 / 实施程序
   - 结果评定 / 合格判据 / 评判准则 / 数据处理与结论
   数据处理与评定是方法正文的一部分，不是排除理由。
5. 观察本文档的层级习惯：有的文档在「项目章」下挂多条可执行方法；有的整章即一条方法；
   子节若标题是「试验条件/步骤/评定」等，通常是**同一条方法**的内部结构；
   子节若是不同试验名称，则可能是**独立方法**。
6. 正文中的（1）（2）列表项、表格操作行，通常是方法内子步骤，不是独立方法。

【目录】
{heading_outline}

【章节索引】
{section_catalogue_json}

【正文片段 {chunk_index}/{chunk_total}】
{chunk_text}

输出：{{ "chunk_memory": "..." }}
""".strip()

GUIDED_MERGE_MEMORY_PROMPT = """
合并各片段理解为完整文档记忆。输出纯 JSON，**不要**输出 methods。

【结构画像（Phase0，供参考）】
{structure_profile_json}

【目录】
{heading_outline}

【各片段摘要】
{chunk_summaries}

【要求】
1. document_memory_markdown：完整 Markdown，引用 section_id。
2. document_structure：在结构画像基础上补充本文特有说明（如何组织试验方法、非方法区域），不重复编造结构。
3. non_method_regions：列出非方法区域（设备表、参数表行、指标汇总表等）。

输出：
{{
  "document_memory_markdown": "...",
  "document_structure": {{
    "structure_notes": "...",
    "non_method_regions": ["..."]
  }}
}}
""".strip()

GUIDED_LIST_METHODS_PROMPT = """
你是试验方法识别专家。根据【结构画像】【文档记忆】与【章节索引】，推测全文所有试验方法候选。

【共性定义 — 适用于任意格式文档】
一条试验方法 = 一个可**独立执行**的试验单元，语义上尽量齐：
试验条件 + 试验过程/方法 + 结果评定（标题可改名、可缺失部分，但须在 reasoning 中说明）。

【请先阅读结构画像再决定粒度】
- organization_mode=dispersed：并列章节若各有完整条件/过程/评定 → 多条方法
- organization_mode=integrated：共用条件/评定 + 多个子项 → 按子项或画像说明拆分
- organization_mode=single_method：默认一条，除非正文有明确多个并列试验项
- method_unit=per_sub_item：方法单元在子节/子项层级
- method_unit=per_chapter：方法单元在章/节层级
- method_unit=single_block：全文实质一条方法

【粒度原则 — 不写死节号】
1. boundary_start / anchor 须落在**有正式节号或章节标题**的节；禁止仅凭（1）（2）列表项或表格行单独成条。
2. 子节标题若是「试验条件/试验步骤/结果评定」等 → 归入同一方法，不单独成条。
3. 子节若是不同试验名称（不论层级深浅）→ 各自成条，即使 project_name 相同。
4. section_ids / section_nos 必须来自【章节索引】，禁止编造。
5. 不要用「章总节」覆盖其下所有子方法的锚点（锚点应落在具体方法节）。

【每条 method 必须输出】
display_name, project_name, method_short_name, method_scope（standalone|sub_item|shared_section|full_project|indicator_method 等自由标注）,
reasoning（为何这是一条完整可执行方法）,
granularity_rationale（相对父章/兄弟节为何是这个粒度）,
semantic_slots（{{"conditions":"本文档中对应标题或片段","process":"...","evaluation":"..."}}）,
boundary_start, boundary_end, included_section_ids, section_mapping, span_hint, memory_excerpt,
anchor_section_ids, anchor_section_nos, confidence

【结构画像】
{structure_profile_json}

【组织形态】
organization_mode: {organization_mode}
organization_reason: {organization_reason}

【文档记忆】
{document_memory}

【文档结构补充】
{document_structure_json}

【目录摘要】
{heading_outline}

【章节索引（含层级元数据）】
{section_catalogue_json}

输出纯 JSON：{{ "methods": [ ... ] }}
""".strip()

GUIDED_CRITIQUE_METHODS_PROMPT = """
你是试验方法识别质检专家。根据【结构画像】与【方法候选列表】，自检是否过粗、过细或漏项。

【结构画像】
{structure_profile_json}

【文档记忆摘要】
{document_memory}

【当前候选】
{methods_json}

【任务】
1. 过粗：整章容器被当成一条方法，但其下有多条并列可执行子方法 → 应拆分
2. 过细：条件/步骤/评定子节被单独成条 → 应合并
3. 漏项：结构画像暗示应有更多方法 → 补充
4. 保留 reasoning / semantic_slots / boundary 字段；修订时更新 granularity_rationale

输出纯 JSON：
{{
  "methods": [ ... ],
  "review_notes": "整体复核说明"
}}
""".strip()

GUIDED_MERGE_METHODS_PROMPT = """
合并去重以下方法候选。输出纯 JSON：{{ "methods": [...] }}

【规则 — 通用】
1. 按 boundary_start.section_nos 或 anchor_section_nos 区分条目；**禁止**仅因 project_name 相同就合并不同子方法。
2. 删除 memory_excerpt 以 | 开头的表格行候选。
3. 删除仅锚在「条件/步骤/评定」子节且应归入父方法的窄条目。
4. 不得仅因正文出现「（1）××」「步骤一」就单独成条。
5. 保留 reasoning 与 semantic_slots 字段。

【结构画像摘要】
{structure_profile_json}

【文档记忆摘要】
{document_memory}

【候选列表】
{methods_json}
""".strip()

GUIDED_REFINE_ANCHOR_PROMPT = """
用户已对某试验方法候选给出反馈。根据文档记忆、结构画像与章节目录，**仅更新边界定位与 reasoning**，不要重写全文。

【结构画像】
{structure_profile_json}

【文档记忆】
{document_memory}

【章节目录】
{section_catalogue_json}

【当前候选】
{current_candidate_json}

【用户反馈】
{user_feedback}

输出纯 JSON（含 boundary_start/boundary_end/method_scope/reasoning/granularity_rationale 若需修改）：
{{
  "boundary_start": {{"section_nos": ["..."], "text_anchor": "..."}},
  "boundary_end": {{"section_nos": ["..."]}},
  "included_section_ids": ["S..."],
  "method_scope": "standalone",
  "anchor_section_ids": ["S..."],
  "anchor_section_nos": ["..."],
  "span_hint": "...",
  "memory_excerpt": "...",
  "display_name": "...",
  "project_name": "...",
  "method_short_name": "",
  "reasoning": "...",
  "granularity_rationale": "...",
  "semantic_slots": {{"conditions":"...","process":"...","evaluation":"..."}},
  "confidence": 0.8,
  "refine_note": "简要说明改动"
}}
""".strip()

# 兼容 shell 长度估算
GUIDED_UNDERSTAND_PROMPT = GUIDED_CHUNK_MEMORY_PROMPT
