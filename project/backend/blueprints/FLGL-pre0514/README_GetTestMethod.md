# GetTestMethod 交接 README

## 1. 项目用途

GetTestMethod 用于把一个标准文件（`pdf / docx / txt`）解析为结构化方法节点 JSON，整体流程分为两段：

1. **抽取阶段**：解析文件、切章节、调用 LLM 做文档级发现与方法级细粒度抽取。
2. **召回阶段**：根据方法内容生成检索文本，对图数据库中的 Equipment / Environment / TestProject / Indicator 做 embedding 粗召回，并组装最终 JSON。

当前版本重点支持：
- **总则**：默认抽取为 1 个“通用要求/通用实施模板方法”。
- **分则**：优先抽取为 1 个或多个“具体试验方法”。
- 输出字段已适配当前图谱结构：`class_node / method_node / SYTJ_node / SYGC_node / JGPD_node / graph_matches / topk_config`。

---

## 2. 目录结构与职责

```text
GetTestMethod/
├── __init__.py
├── parser.py
├── extractor.py
├── prompts.py
├── recall.py
├── retriever.py
├── assembler.py
├── storage.py
├── pipeline.py
└── run_get_testmethod.py   # 项目入口脚本（在包外）
```

### 2.1 文件级说明

#### `__init__.py`
作用：
- 保持包入口极简，不在 `import GetTestMethod` 时自动导入 OCR、Torch、sentence-transformers 等重依赖。
- 这是为了解决 Windows 下 PaddleOCR 与 Torch 可能发生的 DLL 冲突问题。

当前内容：
- 仅保留模块说明和 `__all__ = []`

---

#### `parser.py`
作用：
- 读取原始标准文件。
- 对 PDF 优先尝试 PyMuPDF 文本层，再结合 OCR 补足文本与表格。
- 去页眉/水印噪声。
- 切分页、切章节、构造 `section_catalogue`。

输出核心对象：`parsed_doc`

---

#### `extractor.py`
作用：
- 调用 LLM 完成两步抽取：
  1. `discover_document_structure()`：文档级发现（总则/分则、terms、method_candidates）
  2. `extract_method_details()`：方法级细粒度抽取（name/type/description/recall_plan/SYTJ/SYGC/JGPD）
- 对 LLM 输出做 JSON 清洗、去重、字段补齐、兜底修正。

输出核心对象：`extraction_data`

---

#### `prompts.py`
作用：
- 管理 LLM 提示词模板。
- 包含两套 prompt：
  - `DISCOVERY_PROMPT`：文档级发现
  - `DETAIL_PROMPT`：方法级细粒度抽取
- 当前 prompt 已加入：
  - 总则优先抽 1 个通用方法
  - 分则单一试验优先抽 1 个主方法
  - 不把“试验程序/试验条件确定/试验要求/结果分析”默认拆成多个方法

---

#### `recall.py`
作用：
- 负责把抽取后的方法与图数据库节点做 embedding 粗召回。
- 提供 per-category topk 配置：
  - `equipment`
  - `environment`
  - `test_project`
  - `indicator`

输出核心对象：`recall_data`（在 `extraction_data` 基础上增加 `graph_matches` 和 `topk_config`）

---

#### `retriever.py`
作用：
- 封装图数据库 embedding 检索器 `GraphEmbeddingRetriever`。
- 从 Neo4j 读取节点，拼接节点文本，编码成向量并缓存。
- 根据方法内容构造 query text，分别检索 4 类节点。

---

#### `assembler.py`
作用：
- 将抽取/召回结果组装为最终落库 JSON。
- 负责生成：
  - `class_node`
  - `method_node`
  - `SYTJ_node`
  - `SYGC_node`
  - `JGPD_node`
  - `manifest`

---

#### `storage.py`
作用：
- 管理输出目录与 JSON 文件读写。
- 负责把中间结果和最终结果统一保存到文档目录下。

---

#### `pipeline.py`
作用：
- 串联“解析 → 抽取 → 召回 → 组装 → 保存”流程。
- 由于 Windows 环境中 PaddleOCR 与 Torch 可能同进程冲突，当前实现采用**延迟导入**：
  - 抽取阶段只导入 parser/extractor/storage
  - 召回阶段只导入 recall/assembler/storage

---

#### `run_get_testmethod.py`
作用：
- 命令行入口。
- 初始化 OpenAI / Neo4j / embedding runtime。
- 提供四种运行模式：
  - `extract`
  - `interactive`
  - `full`
  - `resume`
- 其中当前 `full` 是**两进程模式**：先 extract，再在新进程里 resume，避免 Paddle 与 Torch 同进程冲突。

---

## 3. 整体数据流

```text
原始文件
  ↓
parse_standard_file()
  ↓
parsed_doc
  ↓
extract_document_methods()
  ↓
extraction_data
  ↓
recall_candidates_for_document()
  ↓
recall_data / enriched_data
  ↓
build_method_payload() + build_document_manifest()
  ↓
manifest.json + method_xxx.json
```

---

## 4. 各文件函数说明

## 4.1 `parser.py`

### 公开主函数

#### `parse_standard_file(file_path: str, ocr_output_dir: Optional[str] = None) -> Dict[str, Any]`
作用：
- 项目主解析入口。
- 支持 `txt / docx / pdf`。
- PDF 会综合文本层与 OCR，自动判断是否强制 OCR。
- 最终返回结构化 `parsed_doc`。

参数：
- `file_path`：输入文件绝对或相对路径。
- `ocr_output_dir`：OCR 临时输出目录，默认 `output_json/ocr_tmp`。

返回：
- `parsed_doc`，核心字段见第 7 节。

#### `split_standard_sections(full_text: str) -> List[Dict[str, Any]]`
作用：
- 根据标题样式把全文切成章节。
- 输出章节列表，每章附带 `section_id / title / level / start_page / end_page / text`。

#### `build_section_catalogue(sections, snippet_chars=180)`
作用：
- 生成给 discovery prompt 使用的章节目录摘要。

#### `get_sections_by_ids(sections, section_ids)`
作用：
- 按 section_id 选章节。

#### `collect_tables_for_sections(sections, page_tables, section_ids)`
作用：
- 按章节范围收集 OCR 提取到的表格 HTML。

### 内部辅助函数

- `_read_txt()`：读取 txt 文件。
- `_read_docx()`：读取 docx 文本和表格。
- `_read_pdf_with_fitz()`：用 PyMuPDF 走 PDF 文本层。
- `_read_pdf_with_ocr()`：调用 `pdf_ocr_extractor.PDFOCRExtractor` 做 OCR。
- `_choose_pdf_result()`：比较 fitz 与 OCR 结果质量，决定最终采用哪份文本。
- `_clean_pages_noise()` / `_collect_repeated_noise_lines()`：清除重复水印、页眉、短噪声行。
- `_pdf_text_quality()` / `_should_force_ocr()`：评估文本层是否质量过低。
- `_is_heading_candidate()` / `_heading_level()`：识别章节标题及层级。

---

## 4.2 `extractor.py`

### 公开主函数

#### `discover_document_structure(parsed_doc: Dict[str, Any], preview_chars: int = 10000) -> Dict[str, Any]`
作用：
- 文档级发现。
- 调用 `build_discovery_prompt()`，输出：
  - `document_class`
  - `document_summary`
  - `terms`
  - `method_candidates`

参数：
- `parsed_doc`：来自 parser 的解析结果。
- `preview_chars`：传给 prompt 的全文预览长度。

#### `extract_method_details(parsed_doc, discovery_data, max_methods=None) -> List[Dict[str, Any]]`
作用：
- 针对每个候选方法调用 `build_detail_prompt()`。
- 输出细粒度方法对象列表。
- 会自动收集相关章节和表格。
- 如果 LLM 漏掉表格，会按章节兜底补到 `SYTJ/SYGC/JGPD` 中。

参数：
- `parsed_doc`：解析结果。
- `discovery_data`：文档级发现结果。
- `max_methods`：最多抽取多少个候选方法，`None` 表示全抽。

#### `extract_document_methods(parsed_doc, max_methods=None) -> Dict[str, Any]`
作用：
- 抽取总入口。
- 先发现，再细抽。
- 输出 `extraction_data`。

### 主要内部函数

- `_call_llm_json(prompt, max_tokens=4000, temperature=0.1)`：调用 OpenAI，并把结果尽量解析成 JSON。
- `_safe_json_loads()`：兼容带代码块/多余逗号的 LLM 输出。
- `_normalize_terms()`：清洗术语数组。
- `_normalize_method_candidate()`：清洗候选方法。
- `_normalize_method_detail()`：清洗方法细节对象。
- `_normalize_recall_plan()`：确保 recall_plan 四槽完整。
- `_normalize_text_block()`：确保 `sytj/sygc/jgpd` 始终含 `text/tables_html/formulas_latex`。

---

## 4.3 `prompts.py`

### 公开函数

#### `build_discovery_prompt(file_name, section_catalogue, document_preview) -> str`
作用：
- 组装 discovery prompt。

参数：
- `file_name`：文件名。
- `section_catalogue`：章节目录摘要。
- `document_preview`：全文截断预览。

#### `build_detail_prompt(file_name, document_class, method_candidate, related_sections, related_tables) -> str`
作用：
- 组装 detail prompt。

参数：
- `file_name`：文件名。
- `document_class`：总则/分则。
- `method_candidate`：当前候选方法。
- `related_sections`：候选方法相关章节正文。
- `related_tables`：相关表格 HTML 上下文。

### Prompt 设计要点

#### `DISCOVERY_PROMPT`
负责：
- 判断 `document_class`
- 提取 `terms`
- 提取 `method_candidates`

当前规则重点：
- 总则默认输出 1 个“通用方法模板”候选。
- 分则若标题就是单一试验名，默认输出 1 个主方法。
- generic 章节（试验程序/试验条件/试验要求/结果分析/监控监测等）默认不单独算方法。

#### `DETAIL_PROMPT`
负责：
- 把一个候选方法细化为最终 method 对象。
- 将相关章节分别汇总到：
  - `SYTJ`
  - `SYGC`
  - `JGPD`

---

## 4.4 `recall.py`

### 常量

#### `DEFAULT_TOPK`
默认每类召回数：
```python
{
    "equipment": 5,
    "environment": 5,
    "test_project": 5,
    "indicator": 5,
}
```

### 公开函数

#### `normalize_topk_config(topk_config=None) -> Dict[str, int]`
作用：
- 把外部 topk 配置标准化。
- 对缺失值补默认值，对非法值做容错。

#### `get_runtime_retriever() -> GraphEmbeddingRetriever`
作用：
- 获取运行期全局 embedding 检索器。
- 若不存在，则使用 `Config.BGE_MODEL_PATH` 初始化。

#### `recall_candidates_for_methods(methods, topk_config=None) -> List[Dict[str, Any]]`
作用：
- 对方法列表逐个做粗召回。
- 如果 Neo4j 不可用，则直接返回空 `graph_matches`。

#### `recall_candidates_for_document(extraction_data, topk_config=None) -> Dict[str, Any]`
作用：
- 文档级召回入口。
- 在每个 method 上补：
  - `graph_matches`
  - `topk_config`

---

## 4.5 `retriever.py`

### 常量

#### `GRAPH_LABELS`
类别与图数据库标签映射：
```python
{
    "equipment": "Equipment",
    "environment": "Environment",
    "test_project": "TestProject",
    "indicator": "Indicator",
}
```

### 公开函数/类

#### `_cosine_topk(query_vec, doc_vecs, top_k=8) -> List[int]`
作用：
- 对归一化向量做余弦 top-k 检索。

### `class GraphEmbeddingRetriever`

#### `__init__(model_name_or_path="BAAI/bge-small-zh-v1.5", device=None, cache_dir=None)`
作用：
- 初始化 `SentenceTransformer` 模型。
- 建立节点缓存、文本缓存、向量缓存。

参数：
- `model_name_or_path`：embedding 模型路径或模型名。
- `device`：运行设备。
- `cache_dir`：模型缓存目录。

#### `node_to_text(node)`
作用：
- 把图节点字段拼成 embedding 文本。
- 拼接字段：`name / label / type / description`。

#### `load_nodes_from_neo4j(neo4j_driver, graph_label)`
作用：
- 从 Neo4j 指定标签读取节点。

#### `build_index(neo4j_driver, force_rebuild=False)`
作用：
- 加载四类节点并编码为向量。
- 默认缓存，不重复构建。

#### `build_query_text(category, method_item)`
作用：
- 把方法内容转成某一类检索文本。
- 会拼接：
  - name / type / description
  - recall_plan 某槽关键词与 reason
  - `sytj/sygc/jgpd` 的部分文本

#### `search(category, method_item, top_k=8)`
作用：
- 检索指定类别的 top-k 节点。

#### `search_all(method_item, topk_config=None)`
作用：
- 一次性检索四类节点。

---

## 4.6 `assembler.py`

### 公开函数

#### `build_sy(terms) -> str`
作用：
- 把术语数组拼成图谱方法节点中的 `SY` 字符串。
- 格式：`术语1：定义1；术语2：定义2`。

#### `build_method_payload(method_item, file_name, source_file_id, user_id, terms, document_class, method_index, method_id_prefix="FF") -> Dict[str, Any]`
作用：
- 把单个方法组装成最终落库 JSON。
- 生成：
  - `source_file`
  - `class_node`
  - `terms`
  - `method_node`
  - `SYTJ_node`
  - `SYGC_node`
  - `JGPD_node`
  - `graph_matches`
  - `topk_config`

参数：
- `method_item`：抽取+召回后的单方法对象。
- `file_name`：来源文件名。
- `source_file_id`：来源文件 ID。
- `user_id`：用户 ID。
- `terms`：术语数组。
- `document_class`：总则/分则。
- `method_index`：方法顺序，从 1 开始。
- `method_id_prefix`：方法前缀，默认 `FF`。

#### `build_document_manifest(extraction_data, payloads) -> Dict[str, Any]`
作用：
- 生成总清单 `manifest.json`。

### 内部函数

#### `_build_subnode(node_kind, method_id, source_name, source_id, user_id, data)`
作用：
- 组装 `SYTJ_node / SYGC_node / JGPD_node` 的公共结构。

---

## 4.7 `storage.py`

### 公开函数

#### `ensure_dir(path)`
作用：创建目录。

#### `load_json(path)`
作用：读取 JSON 文件。

#### `save_json(data, path)`
作用：保存 JSON 文件。

#### `sanitize_name(name)`
作用：
- 清理文件名非法字符。
- 用于输出方法 JSON 文件名。

#### `build_document_output_dir(output_root, file_name)`
作用：
- 以文件名 stem 建立输出目录。
- 例如：`第二十二部分.pdf -> output_json/第二十二部分/`

#### `save_parsed_document(parsed_doc, document_dir)`
作用：保存 `parsed_document.json`

#### `save_extraction_checkpoint(extraction_data, document_dir)`
作用：保存 `extraction_checkpoint.json`

#### `save_recall_checkpoint(recall_data, document_dir)`
作用：保存 `recall_checkpoint.json`

#### `save_manifest(manifest, document_dir)`
作用：保存 `manifest.json`

#### `save_method_payloads(payloads, document_dir)`
作用：
- 把每个方法 payload 保存成一个单独 JSON。
- 文件名格式：`method_{序号}_{方法名}.json`
- 同时在 payload 内补 `_output_file`

---

## 4.8 `pipeline.py`

### 设计原则

为避免 Windows 下 PaddleOCR 与 Torch 同进程冲突，当前 `pipeline.py` 采用**函数内延迟导入**：
- `run_extract_only()` 内部再导入 parser/extractor/storage
- `run_recall_only()` 内部再导入 recall/assembler/storage

### 公开函数

#### `run_extract_only(file_path, source_file_id, user_id, output_root="output_json", max_methods=None) -> Dict[str, Any]`
作用：
- 只做解析 + 抽取。
- 保存：
  - `parsed_document.json`
  - `extraction_checkpoint.json`

返回：
```python
{
    "status": "paused_after_extraction",
    "document_dir": ...,
    "parsed_document_path": ...,
    "extraction_checkpoint_path": ...,
    "extraction_data": ...,
}
```

#### `run_recall_only(extraction_checkpoint_path, source_file_id, user_id, output_root=None, topk_config=None, method_id_prefix="FF", save_method_json=True) -> Dict[str, Any]`
作用：
- 从抽取结果继续做召回和组装。
- 保存：
  - `recall_checkpoint.json`
  - `manifest.json`
  - `method_*.json`

#### `run_pipeline_full(file_path, source_file_id, user_id, output_root="output_json", max_methods=None, topk_config=None, method_id_prefix="FF") -> Dict[str, Any]`
作用：
- 单进程串行执行 extract + recall。
- **备注**：代码里已明确提醒，若环境中 Paddle 和 Torch 同进程冲突，建议不要直接用这个函数，而是先 extract，再新进程 resume。

#### `run_pipeline_interactive(file_path, source_file_id, user_id, output_root="output_json", continue_after_extraction=False, max_methods=None, topk_config=None, method_id_prefix="FF") -> Dict[str, Any]`
作用：
- 先抽取。
- 如果 `continue_after_extraction=False`，则停在抽取阶段。
- 否则继续召回和组装。

#### `run_pipeline_resume(extraction_checkpoint_path, source_file_id, user_id, output_root=None, topk_config=None, method_id_prefix="FF") -> Dict[str, Any]`
作用：
- 从 `extraction_checkpoint.json` 继续召回与组装。

---

## 4.9 `run_get_testmethod.py`

### 入口职责

1. 初始化 OpenAI 客户端。
2. 初始化 Neo4j driver（失败允许继续，只是 graph_matches 为空）。
3. 设置 embedding 模型路径 `Config.BGE_MODEL_PATH`。
4. 提供命令行模式。
5. 当前 `full` 采用**两进程模式**避免 DLL 冲突。

### 公开函数

#### `init_runtime_clients()`
作用：
- 初始化 `Config.OPENAI_CLIENT`
- 初始化 `Config.NEO4J_DRIVER`
- 初始化 `Config.TESTMETHOD_RETRIEVER`
- 校验 `Config.OPENAI_MODEL`

前置依赖：
- `backend.config.Config`
- 至少应配置：
  - `OPENAI_API_KEY`
  - `OPENAI_BASE_URL`（可选）
  - `OPENAI_MODEL`
  - `NEO4J_URI / NEO4J_USER / NEO4J_PASSWORD`（如需召回）

#### `parse_topk(args)`
作用：
- 将命令行 `--topk-*` 参数整理为统一 dict。

#### `print_methods_preview(extraction_data)`
作用：
- 在 interactive 模式下预览当前抽取到的方法数、名称、类型、摘要。

#### `prompt_continue()`
作用：
- 在 interactive 模式下等待命令行输入。
- 回车继续；输入 `n/no/q/quit/stop` 则暂停。

#### `close_runtime_clients()`
作用：
- 关闭 Neo4j 连接。

#### `_import_pipeline_extract_only()` / `_import_pipeline_resume()`
作用：
- 延迟导入 pipeline 函数。

#### `_run_resume_subprocess(args, checkpoint_path)`
作用：
- 用子进程调用：
  ```bash
  python run_get_testmethod.py resume <checkpoint>
  ```
- 这是当前规避 Paddle/Torch DLL 冲突的核心实现。

#### `main()`
作用：
- 解析命令行并分发到四个模式。

---

## 5. 运行方式

## 5.1 命令行模式

### 1）只抽取
```bash
python run_get_testmethod.py extract your.pdf
```

作用：
- 完成解析和 LLM 抽取
- 不做召回
- 适合先检查抽取质量

生成：
- `parsed_document.json`
- `extraction_checkpoint.json`

---

### 2）交互式运行
```bash
python run_get_testmethod.py interactive your.pdf
```

作用：
- 先抽取
- 命令行提示是否继续
- 回车：继续召回并生成最终 JSON
- 输入 `n`：停止，保留 checkpoint

适合：
- 人工确认“抽取的方法数是否合理”

---

### 3）完整运行（推荐 CLI 方式）
```bash
python run_get_testmethod.py full your.pdf
```

作用：
- 先抽取
- 再在**新进程**中调用 `resume`
- 自动规避 PaddleOCR 与 Torch 同进程冲突

适合：
- 日常正式运行

---

### 4）从 checkpoint 继续
```bash
python run_get_testmethod.py resume output_json/xxx/extraction_checkpoint.json
```

作用：
- 跳过抽取
- 直接从抽取结果继续召回和组装

适合：
- 抽取结果已经确认过
- 或抽取/召回分两次执行

---

## 5.2 常用参数

所有模式通用或常见参数：

- `file`：输入文件路径
- `--source-file-id`：来源文件 ID，默认 `WJ_std0001`
- `--user-id`：用户 ID，默认 `admin`
- `--output-root`：输出根目录，默认 `output_json`
- `--max-methods`：最多抽取多少个方法，默认 `None`
- `--method-id-prefix`：方法 ID 前缀，默认 `FF`
- `--topk-equipment`：设备节点召回数
- `--topk-environment`：环境节点召回数
- `--topk-test-project`：试验项目节点召回数
- `--topk-indicator`：指标节点召回数

示例：
```bash
python run_get_testmethod.py full "E:\hongshi\150标准\第二十二部分.pdf" \
  --source-file-id WJ_std0022 \
  --user-id admin \
  --output-root output_json \
  --topk-equipment 3 \
  --topk-environment 3 \
  --topk-test-project 3 \
  --topk-indicator 3 \
  --method-id-prefix FF
```

---

## 6. 如何测试/验收

## 6.1 当前项目没有独立的单元测试框架
当前交接代码中**没有 pytest / unittest 自动化测试**。
当前推荐的测试方式就是跑 CLI，并检查输出文件。

## 6.2 推荐测试流程

### A. 抽取测试
```bash
python run_get_testmethod.py extract "某个标准.pdf"
```
检查：
- `parsed_document.json` 是否正文正常、不是全水印
- `extraction_checkpoint.json` 中：
  - `document_meta.class` 是否正确
  - `terms` 是否合理
  - `method_candidates` 数量是否合理
  - `methods` 的 `name/type/description` 是否合理

### B. 召回测试
```bash
python run_get_testmethod.py resume "output_json/某文档/extraction_checkpoint.json"
```
检查：
- `recall_checkpoint.json` 中是否新增 `graph_matches`
- `method_*.json` 是否生成
- `manifest.json` 中 `method_count` 和输出文件数是否一致

### C. 全流程测试
```bash
python run_get_testmethod.py full "某个标准.pdf"
```
检查：
- 能否完整执行
- 是否自动在新进程里续跑召回
- 是否生成完整输出目录

---

## 7. 输出文件说明

假设输入文件为：
```text
第二十二部分.pdf
```
输出目录一般为：
```text
output_json/第二十二部分/
```

其中通常包含：

```text
output_json/第二十二部分/
├── parsed_document.json
├── extraction_checkpoint.json
├── recall_checkpoint.json
├── manifest.json
├── method_001_xxx.json
├── method_002_xxx.json
└── ...
```

---

## 7.1 `parsed_document.json`
作用：原始解析结果。

主要字段：
- `file_path`：输入文件绝对路径
- `file_name`：文件名
- `file_ext`：扩展名
- `parse_mode`：解析模式，如 `pdf_fitz / pdf_ocr / pdf_merged / txt / docx`
- `ocr_used`：是否用了 OCR
- `full_text`：全文拼接文本
- `pages`：分页结果
- `all_tables`：全表格列表
- `page_table_map`：页码到表格 HTML 的映射
- `sections`：切分后的章节列表
- `section_catalogue`：章节目录摘要
- `stats`：统计信息，如页数/章节数/文本长度/表格数

### `pages` 单页字段
- `page_number`
- `text`
- `tables_html`
- `figures`

### `sections` 单章节字段
- `section_id`
- `title`
- `heading`
- `level`
- `start_page`
- `end_page`
- `text`

---

## 7.2 `extraction_checkpoint.json`
作用：抽取阶段完整结果。

主要字段：
- `source_file`
  - `file_name`
  - `file_path`
  - `file_ext`
  - `parse_mode`
  - `ocr_used`
- `document_meta`
  - `class`：总则 / 分则 / 未判定
  - `summary`：文档摘要
- `terms`：术语数组
- `method_candidates`：发现阶段候选方法
- `methods`：细粒度方法对象数组

### `terms` 单项字段
- `term`
- `definition`

### `method_candidates` 单项字段
- `candidate_id`
- `candidate_title`
- `candidate_type_hint`
- `summary_hint`
- `section_ids`
- `reason`

### `methods` 单项字段
- `candidate_id`
- `class`
- `section_ids`
- `name`
- `type`
- `description`
- `recall_plan`
- `sytj`
- `sygc`
- `jgpd`

### `recall_plan` 字段
四个槽：
- `equipment`
- `environment`
- `test_project`
- `indicator`

每个槽都包含：
- `keywords`：关键词列表
- `reason`：为何抽这些关键词

### `sytj / sygc / jgpd` 字段
每块都包含：
- `text`
- `tables_html`
- `formulas_latex`

---

## 7.3 `recall_checkpoint.json`
作用：在 `extraction_checkpoint.json` 基础上补召回结果。

新增/更新字段：
- 顶层 `topk_config`
- 每个 `method` 增加：
  - `graph_matches`
  - `topk_config`

### `graph_matches` 字段
包含四类：
- `equipment`
- `environment`
- `test_project`
- `indicator`

每类是一个列表，单项字段：
- `id`
- `name`
- `label`
- `type`
- `description`
- `score`

---

## 7.4 `manifest.json`
作用：总清单，方便快速查看本次输出有哪些方法文件。

字段：
- `source_file`
- `document_meta`
- `terms_count`
- `method_count`
- `methods`

### `methods` 单项字段
- `candidate_id`
- `name`
- `type`
- `section_ids`
- `output_file`

---

## 7.5 `method_xxx.json`
作用：最终单方法落库 JSON。

顶层字段：
- `source_file`
- `class_node`
- `terms`
- `method_node`
- `SYTJ_node`
- `SYGC_node`
- `JGPD_node`
- `graph_matches`
- `topk_config`
- `_output_file`（保存时补充）

### `source_file`
- `file_id`
- `file_name`
- `user_id`

### `class_node`
- `Id`
- `Name`
- `Source_name`
- `Source_id`
- `Status`
- `User_id`
- `Create_time`

### `method_node`
- `Id`
- `Candidate_id`
- `Section_ids`
- `Name`
- `Type`
- `Label`：这里存 `recall_plan`
- `Description`
- `SY`：术语拼接串
- `Source_name`
- `Source_id`
- `Status`
- `User_id`
- `Create_time`

### `SYTJ_node / SYGC_node / JGPD_node`
结构相同：
- `Id`
- `Name`
- `Text`
- `Tables_html`
- `Formulas_latex`
- `Source_name`
- `Source_id`
- `Status`
- `User_id`
- `Create_time`

### `topk_config`
- `equipment`
- `environment`
- `test_project`
- `indicator`

---

## 8. 配置与依赖

## 8.1 Python 运行依赖
核心依赖至少包括：
- `openai`
- `neo4j`
- `sentence-transformers`
- `numpy`
- `pymupdf`
- `python-docx`
- `paddleocr`
- `paddlepaddle`
- `opencv-python` 或 `opencv-python-headless`

## 8.2 项目内外部依赖
本包依赖以下外部模块：
- `backend.config.Config`
- `pdf_ocr_extractor.py`

### `backend.config.Config` 至少应提供
- `OPENAI_API_KEY`
- `OPENAI_BASE_URL`（可选）
- `OPENAI_MODEL`
- `NEO4J_URI`（可选）
- `NEO4J_USER`（可选）
- `NEO4J_PASSWORD`（可选）
- `BGE_MODEL_PATH`（可选）

### `pdf_ocr_extractor.py` 作用
- PDF 每页转图片
- 用 PaddleOCR / PPStructure 做 OCR、版面分析、表格提取、图片保存

---

## 9. 常见问题

### 9.1 为什么要把包入口 `__init__.py` 置空？
因为 Windows 下如果在一个 Python 进程里同时早期加载 PaddleOCR 和 Torch，容易触发 DLL 冲突。
当前方案是：
- `__init__.py` 极简
- `pipeline.py` 延迟导入
- `run_get_testmethod.py full` 用两进程模式

### 9.2 为什么 `full` 不是直接调用 `run_pipeline_full()`？
命令行入口里为了稳定性，实际采用：
- 当前进程 `extract`
- 新进程 `resume`
这样能绕过 Paddle 与 Torch 冲突。

### 9.3 如果 Neo4j 连不上怎么办？
可以继续运行，只是：
- `graph_matches` 会为空
- `topk_config` 仍会保留

### 9.4 如果 PDF 文本层很差怎么办？
`parser.py` 会自动评估文本层质量，必要时强制 OCR；
如果 OCR 本身环境有问题，会直接报错，避免继续产出只有水印/页眉的无效文本。

### 9.5 总则和分则怎么抽？
- **总则**：默认 1 个“通用要求/通用实施模板方法”
- **分则**：默认 1 个或多个具体试验方法
- 当前 prompt 已尽量避免把“试验程序/试验条件/试验要求/结果分析”拆成多个独立方法

---

## 10. 推荐交接话术

建议向接手同学强调这几点：

1. **先看 `run_get_testmethod.py`，这是唯一推荐入口。**
2. **不要直接 `import GetTestMethod` 期待它自动跑流程。** 当前包入口故意是空的。
3. **先用 `extract` 看抽取质量，再用 `resume` 跑召回。**
4. **如果环境稳定，日常可直接用 `full`。** 但它本质还是“抽取进程 + 召回进程”两段式。
5. **总则与分则抽法不同。** 总则默认收成 1 个通用方法；分则优先收成具体试验方法。

---

## 11. 最简使用示例

### 示例 1：先抽取后续跑
```bash
python run_get_testmethod.py extract "E:\hongshi\150标准\第二十二部分.pdf"
python run_get_testmethod.py resume "output_json\第二十二部分\extraction_checkpoint.json"
```

### 示例 2：交互式
```bash
python run_get_testmethod.py interactive "E:\hongshi\150标准\第二十二部分.pdf"
```

### 示例 3：一键完整运行
```bash
python run_get_testmethod.py full "E:\hongshi\150标准\第二十二部分.pdf"
```

---

## 12. 交接时建议一起打包的文件

建议至少一并交接：
- `GetTestMethod/` 整个目录
- `run_get_testmethod.py`
- `backend/config.py` 或 `.env` 配置说明
- `pdf_ocr_extractor.py`
- 本地 embedding 模型目录（如果使用本地 BGE）
- 一份成功跑通的 `output_json/示例文档/` 输出样例

---

如果后续需要继续演进，优先建议做两件事：
1. 增加“方法候选后处理合并规则”，进一步压制总则/分则过拆。
2. 增加自动化测试样例，至少覆盖：总则、单一分则、多方法分则、OCR 差质量 PDF。
