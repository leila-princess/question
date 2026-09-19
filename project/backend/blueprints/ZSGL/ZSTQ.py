# ZSTQ.py — 知识提取蓝图 (PDF OCR + Word + LLM + Neo4j)
# 所有方法隔离，方便增减
import os
import sys
import json
import uuid
import re
import tempfile
import shutil
import threading
from datetime import datetime
from typing import Dict, List, Any, Optional, Tuple

os.environ["PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION"] = "python"

from flask import Blueprint, request, jsonify, send_file
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import Config

# ============================================================================
# 全局状态
# ============================================================================
zstq_progress_store: Dict[str, Dict] = {}
zstq_progress_lock = threading.Lock()
ZSTQ_TEMP_DIR = os.path.join(tempfile.gettempdir(), 'zstq_extractions')
os.makedirs(ZSTQ_TEMP_DIR, exist_ok=True)

neo4j_driver = Config.NEO4J_DRIVER

# 蓝图
zstq_bp = Blueprint('zstq', __name__, url_prefix='/api/zstq')


# ============================================================================
# 工具函数
# ============================================================================
def _update_progress(task_id: str, info: Dict):
    with zstq_progress_lock:
        if task_id not in zstq_progress_store:
            return
        prog = zstq_progress_store[task_id]
        prog['progress'] = info
        if info.get('stage') in ('completed', 'validation') and info.get('percent', 0) >= 100:
            prog['status'] = 'completed'


def _report(task_id: str, stage: str, current: int, total: int, message: str = "",
           text_progress: int = None, table_progress: int = None, figure_progress: int = None):
    percent = int((current / total) * 100) if total > 0 else 0
    info = {
        'stage': stage,
        'stage_name': {
            'text_extraction': '文本提取',
            'chunking': '文本分块',
            'knowledge_extraction': '知识提取',
            'merging': '结果合并',
            'completed': '完成'
        }.get(stage, stage),
        'current': current,
        'total': total,
        'percent': percent,
        'message': message
    }
    if text_progress is not None:
        info['text_progress'] = text_progress
    if table_progress is not None:
        info['table_progress'] = table_progress
    if figure_progress is not None:
        info['figure_progress'] = figure_progress
    _update_progress(task_id, info)


# ============================================================================
# 阶段1: PDF OCR 提取 (通过子进程调用 paddle_env)
# ============================================================================
class PDFOCREngine:
    """纯OCR版PDF解析 — 通过子进程调用 paddle_env"""

    def __init__(self, dpi: int = 300, use_gpu: Optional[bool] = None):
        self.dpi = dpi
        self.use_gpu = use_gpu

    def init_engines(self):
        pass

    def extract(self, pdf_path: str, output_dir: str, task_id: str = "") -> Dict[str, Any]:
        from ocr import run_ocr_pdf
        figures_dir = os.path.join(output_dir, "figures")
        os.makedirs(figures_dir, exist_ok=True)

        result = run_ocr_pdf(
            file_path=pdf_path,
            output_dir=output_dir,
            use_gpu=self.use_gpu,
            dpi=self.dpi,
        )

        if not result.get("success"):
            return {
                "full_text": "",
                "text_by_page": {},
                "tables_html": [],
                "figures": [],
                "total_pages": 0,
                "figures_dir": figures_dir,
                "error": result.get("error"),
            }

        data = result["data"]
        text_by_page = {}
        for p in data.get("pages", []):
            page_num = p.get("page_num", 0)
            text_by_page[f"page_{page_num}"] = {
                "text": p.get("text", ""),
                "tables": p.get("tables", []),
                "figures": p.get("figures", []),
            }

        figures = []
        for fig in data.get("all_figures", []):
            figures.append({
                "page": fig.get("page", 0),
                "path": fig.get("path", ""),
                "filename": fig.get("filename", ""),
                "ocr_text": fig.get("ocr_text", ""),
            })

        tables_html = []
        for tbl in data.get("all_tables", []):
            tables_html.append({
                "page": tbl.get("page", 0),
                "html": tbl.get("html", ""),
            })

        return {
            "full_text": data.get("full_text", ""),
            "text_by_page": text_by_page,
            "tables_html": tables_html,
            "figures": figures,
            "total_pages": data.get("total_pages", 0),
            "figures_dir": data.get("figures_dir", figures_dir),
        }


# ============================================================================
# 阶段1b: Word 提取 (使用 python-docx)
# ============================================================================
class WordExtractEngine:
    """Word文档结构化提取"""

    def extract(self, docx_path: str, output_dir: str) -> Dict[str, Any]:
        from docx import Document
        from docx.oxml.ns import qn

        doc = Document(docx_path)
        figures_dir = os.path.join(output_dir, "figures")
        os.makedirs(figures_dir, exist_ok=True)

        full_text_parts = []
        text_by_page = {}
        all_tables = []
        all_figures = []
        page_counter = 1
        current_page_text = []

        for i, para in enumerate(doc.paragraphs):
            text = para.text.strip()
            style = para.style.name if para.style else ""

            if text:
                current_page_text.append(text)
                full_text_parts.append(text)

            # 每50段作为一页的近似
            if (i + 1) % 50 == 0 and current_page_text:
                page_key = f"page_{page_counter}"
                text_by_page[page_key] = {
                    "text": "\n".join(current_page_text),
                    "tables": [],
                    "figures": [],
                }
                page_counter += 1
                current_page_text = []

        if current_page_text:
            page_key = f"page_{page_counter}"
            text_by_page[page_key] = {
                "text": "\n".join(current_page_text),
                "tables": [],
                "figures": [],
            }

        # 提取表格
        for tbl_idx, table in enumerate(doc.tables):
            html = "<table border='1'>"
            for row in table.rows:
                html += "<tr>"
                for cell in row.cells:
                    html += f"<td>{cell.text}</td>"
                html += "</tr>"
            html += "</table>"
            all_tables.append({"page": 0, "html": html})

        # 提取图片
        for img_idx, rel in enumerate(doc.part.rels.values()):
            if "image" in rel.reltype:
                img_bytes = rel.target_part.blob
                ext = rel.target_part.content_type.split("/")[-1]
                if ext not in ("png", "jpeg", "jpg", "gif", "bmp"):
                    ext = "png"
                fig_name = f"word_img{img_idx + 1}.{ext}"
                fig_path = os.path.join(figures_dir, fig_name)
                with open(fig_path, "wb") as f:
                    f.write(img_bytes)
                all_figures.append({"page": 0, "path": fig_path, "filename": fig_name})

        return {
            "full_text": "\n\n".join(full_text_parts),
            "text_by_page": text_by_page,
            "tables_html": all_tables,
            "figures": all_figures,
            "total_pages": page_counter,
            "figures_dir": figures_dir,
        }


# ============================================================================
# 阶段2: 文本分块
# ============================================================================
class TextChunker:
    """将提取的文本分割为适合LLM处理的块"""

    def chunk(self, text_data: Dict[str, Any], max_chunk_size: int = 3000) -> List[Dict[str, Any]]:
        text_by_page = text_data.get("text_by_page", {})
        chunks = []

        for page_key, page_info in text_by_page.items():
            text = page_info.get("text", "")
            if not text:
                continue

            if len(text) <= max_chunk_size:
                chunks.append({
                    "chunk_id": page_key,
                    "text": text,
                    "tables": page_info.get("tables", []),
                    "figures": page_info.get("figures", []),
                })
            else:
                # 按段落分割
                paragraphs = text.split("\n")
                current_chunk = ""
                chunk_idx = 0
                for para in paragraphs:
                    if len(current_chunk) + len(para) + 1 > max_chunk_size and current_chunk:
                        chunks.append({
                            "chunk_id": f"{page_key}_section_{chunk_idx}",
                            "text": current_chunk,
                            "tables": page_info.get("tables", []) if chunk_idx == 0 else [],
                            "figures": page_info.get("figures", []) if chunk_idx == 0 else [],
                        })
                        chunk_idx += 1
                        current_chunk = para
                    else:
                        current_chunk = (current_chunk + "\n" + para) if current_chunk else para
                if current_chunk:
                    chunks.append({
                        "chunk_id": f"{page_key}_section_{chunk_idx}",
                        "text": current_chunk,
                        "tables": page_info.get("tables", []) if chunk_idx == 0 else [],
                        "figures": page_info.get("figures", []) if chunk_idx == 0 else [],
                    })

        return chunks


# ============================================================================
# 阶段3: LLM 知识图谱提取
# ============================================================================
class KGExtractor:
    """使用LLM提取知识图谱 (实体+关系)"""

    def __init__(self, model: str = None, temperature: float = 0.1, max_tokens: int = 4000):
        self.model = model or Config.OPENAI_MODEL
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.client = None
        self._init_client()

    def _init_client(self):
        try:
            from openai import OpenAI
            self.client = OpenAI(api_key=Config.OPENAI_API_KEY, base_url=Config.OPENAI_BASE_URL)
        except Exception as e:
            print(f"[WARN] OpenAI客户端初始化失败: {e}")

    def _build_prompt(self, text: str) -> str:
        return (
'你是一名知识工程师。将输入的标准文档转化为符合以下 JSON 格式的知识图谱，用于图数据库（如 Neo4j）。输出必须仅为一个JSON 对象，无额外解释。\n' 
    '核心处理逻辑:\n'
        '1.解析与抽取\n'
        '识别实体类型：Standard、Clause、Term、Organization、Person、TestMethod、Condition、Device、Material、Table、Figure。\n'
        '每个最底层标题（章、条、款、项、附录）的正文放入该节点的 definition 属性。若正文中还有子结构（如 a/b/c 或 1/2/3），必须拆分为独立的 Clause 节点，父节点的 definition 只保留自身正文。\n'
        '表格：每个表格为 Table 节点，tableData 属性必须包含 headers（表头数组）、rows（数据二维数组）和 merges（合并单元格信息数组，含 rowStart, colStart, rowSpan, colSpan），以完整还原表格结构。\n'
        '图片：每个图为 Figure 节点，imagePath 属性格式为 [页数]_img[顺序数]（如 15_img2）。\n'
        '附录中的表格/图片（编号如 A.1）不作为 Clause，而是直接关联到附录节点。\n'
        '2.图谱建模\n'
        '输出 nodes 和 relationships 两个数组。\n'
        '父子条款间用 HAS_PART 关系。\n'
        '表格/图片与包含它的标准或条款之间用 CONTAINS_TABLE / CONTAINS_FIGURE 关系；若正文明确引用，增加 REFERENCED_BY 关系（从表格/图片指向引用条款）。\n'
        '输出格式（严格 JSON，无额外文本）\n'
        'json\n'
        '{\n'
            '"nodes": [\n'
            '{\n'
                '"id": "唯一标识符",\n'
                '"labels": ["实体类型"],\n'
                '"name": "名称",\n'
                '"number": "编号（可选）",\n'
                '"definition": "定义（可选）",\n'
                '"value": "数值（可选）",\n'
                '"context": "上下文（可选）",\n'
                '"date": "日期（可选）",\n'
                '"tableData": { "headers": [], "rows": [], "merges": [] },   // 仅 Table\n'
                '"imagePath": "路径"                                         // 仅 Figure\n'
            '}\n'
            '],\n'
            '"relationships": [\n'
            '{\n'
                '"id": "唯一标识符",\n'
                '"type": "关系类型",\n'
                '"startNodeId": "起始节点ID",\n'
                '"endNodeId": "目标节点ID",\n'
                '"properties": {}      // 可选，存放关系的附加属性\n'
            '}\n'
            ']\n'
        '}\n'
            '3.实体类型与属性说明\n'
            'Standard：name（标准名称），standardNumber（标准号），publishingOrganization（发布机构），issueDate（发布日期），implementationDate（实施日期），status（状态）。\n'
            'Clause：name（标题），number（编号，如“4.2.1”），definition（正文）。\n'
            'Term：name（术语），definition（定义）。\n'
            'Organization：name。\n'
            'Person：name。\n'
            'TestMethod：name。\n'
            'Condition：value（数值），context（上下文）。\n'
            'Device：name。\n'
            'Material：name。\n'
            'Table：name（标题），number（编号，如“表 1”），tableData（对象，含 headers, rows, merges）。\n'
            'Figure：name（标题），number（编号，如“图 1”），imagePath（路径）。\n'

            '关系类型（type）\n'
            'HAS_PART：标准→章节，章节→子条款\n'
            'DEFINES：标准/条款→术语\n'
            'PUBLISHED_BY：标准→机构\n'
            'DRAFTED_BY：标准→人员\n'
            'CITES：标准/条款→被引用标准\n'
            'USES_METHOD：标准/条款→试验方法\n'
            'UNDER_CONDITION：试验方法→条件\n'
            'REQUIRES_DEVICE：试验方法→设备\n'
            'REQUIRES_MATERIAL：试验方法→材料\n'
            'CONTAINS_TABLE：标准/条款→表格\n'
            'CONTAINS_FIGURE：标准/条款→图片\n'
            'REFERENCED_BY：表格/图片→引用条款\n'

            'ID 生成规则\n'
            'Standard: std_[标准号]\n'
            'Clause: clause_[标准号]_[条款编号]\n'
            'Term: term_[标准号]_[术语名]\n'
            'Organization: org_[机构名]\n'
            'Person: person_[人名]\n'
            'TestMethod: method_[方法名]\n'
            'Condition: cond_[标准号]_[哈希]\n'
            'Device: device_[设备名]\n'
            'Material: mat_[材料名]\n'
            'Table: table_[标准号]_[表格编号]\n'
            'Figure: figure_[标准号]_[图片编号]\n'

            '特殊说明\n'
            '如果文本中包含【表格信息】HTML，提取编号、标题和完整表格内容（作为 tableData）。\n'
            '如果文本中包含【图片信息】OCR文字，提取编号、标题和 imagePath（按 页数_img顺序数 生成）。\n'
            '附录（如附录A）是一个 Clause 节点，其编号为 "A"，名称为 "附录A (标题)"。附录内的表格/图片直接通过 CONTAINS_TABLE/CONTAINS_FIGURE 关联到该附录节点。\n'

    '现在，请根据下面的文档，输出符合上述规范的 JSON 对象。确保完整解析所有实体、关系、表格结构（包括合并单元格）和图片。只输出 JSON，不要附加任何解释。\n'    
    '文档内容：\n' + text
        )

    def extract_chunk(self, chunk: Dict[str, Any]) -> Dict[str, Any]:
        if not self.client:
            return {"error": "LLM客户端不可用"}

        text = chunk.get("text", "")[:3000]
        prompt = self._build_prompt(text)

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=self.temperature,
                max_tokens=self.max_tokens,
            )
            content = response.choices[0].message.content or ""
            data = self._parse_json(content)
            return {"chunk_id": chunk["chunk_id"], "extraction": data}
        except Exception as e:
            return {"chunk_id": chunk["chunk_id"], "error": str(e)}

    def _parse_json(self, text: str) -> Dict[str, Any]:
        try:
            start = text.find('{')
            end = text.rfind('}')
            if start != -1 and end != -1 and start < end:
                return json.loads(text[start:end + 1])
        except Exception:
            pass
        return {"nodes": [], "relationships": []}

    def merge_extractions(self, extractions: List[Dict[str, Any]]) -> Dict[str, Any]:
        all_nodes = {}
        all_rels = {}

        for ext in extractions:
            if "error" in ext:
                continue
            data = ext.get("extraction", {})
            for node in data.get("nodes", []):
                nid = node.get("id")
                if nid:
                    if nid not in all_nodes:
                        all_nodes[nid] = node
                    else:
                        existing = all_nodes[nid]
                        existing.get("properties", {}).update(node.get("properties", {}))
            for rel in data.get("relationships", []):
                rid = rel.get("id")
                if rid:
                    all_rels[rid] = rel

        return {"nodes": list(all_nodes.values()), "relationships": list(all_rels.values())}


# ============================================================================
# 阶段4: 后处理 (条款层级关系补充 + 图片节点补充)
# ============================================================================
class PostProcessor:
    """后处理: 补充层级关系、图片节点"""

    def process(self, extraction: Dict[str, Any], figures_map: Dict[str, str] = None) -> Dict[str, Any]:
        nodes = extraction.get("nodes", [])
        relationships = extraction.get("relationships", [])

        # 补充条款层级
        number_to_id = {}
        clause_nodes = []
        for node in nodes:
            if "Clause" in node.get("labels", []):
                props = node.get("properties", {})
                match = re.search(r'_([\d\.A-Za-z]+)$', node.get("id", ""))
                number = match.group(1) if match else props.get("number", "")
                if re.match(r'^A\.\d+$', number):
                    continue
                if number:
                    number_to_id[number] = node["id"]
                    clause_nodes.append((number, node["id"]))

        rel_counter = len(relationships)
        for number, node_id in clause_nodes:
            if '.' not in number:
                continue
            parent_number = '.'.join(number.split('.')[:-1])
            if parent_number in number_to_id:
                parent_id = number_to_id[parent_number]
                key = f"{parent_id}-{node_id}"
                if not any(r.get("startNodeId") == parent_id and r.get("endNodeId") == node_id for r in relationships):
                    relationships.append({
                        "id": f"rel_hier_{rel_counter}",
                        "type": "HAS_PART",
                        "startNodeId": parent_id,
                        "endNodeId": node_id,
                        "properties": {"inferred": True}
                    })
                    rel_counter += 1

        # 补充图片节点
        if figures_map:
            for img_id, img_path in figures_map.items():
                fig_node_id = f"figure_{img_id}"
                if not any(n["id"] == fig_node_id for n in nodes):
                    nodes.append({
                        "id": fig_node_id,
                        "labels": ["Figure"],
                        "properties": {
                            "name": f"图 {img_id}",
                            "imagePath": img_id,
                            "filePath": img_path,
                        }
                    })

        # 补充表格节点
        extraction["nodes"] = nodes
        extraction["relationships"] = relationships
        return extraction


# ============================================================================
# 主流程编排器
# ============================================================================
class ZSTQPipeline:
    """完整知识提取流程编排"""

    def __init__(self, task_id: str, file_path: str, original_filename: str, is_pdf: bool):
        self.task_id = task_id
        self.file_path = file_path
        self.original_filename = original_filename
        self.is_pdf = is_pdf
        self.output_dir = os.path.join(tempfile.gettempdir(), f"zstq_{task_id}")
        os.makedirs(self.output_dir, exist_ok=True)

    def run(self) -> Dict[str, Any]:
        try:
            total_steps = 5
            step_names = ['文件上传', '文本提取', '文本分块', '知识提取', '合并保存']

            # 阶段0: 文件上传
            _report(self.task_id, "upload", 100, 100, "文件已接收",
                   text_progress=0, table_progress=0, figure_progress=0)

            # 阶段1: 文本提取
            _report(self.task_id, "text_extraction", 0, 1, "开始文本提取...",
                   text_progress=0, table_progress=0, figure_progress=0)
            if self.is_pdf:
                engine = PDFOCREngine(dpi=300)
                engine.init_engines()
                text_data = engine.extract(self.file_path, self.output_dir, self.task_id)
            else:
                engine = WordExtractEngine()
                text_data = engine.extract(self.file_path, self.output_dir)
            _report(self.task_id, "text_extraction", 1, 1, "文本提取完成",
                   text_progress=100, table_progress=100, figure_progress=100)

            # 阶段2: 文本分块
            _report(self.task_id, "chunking", 0, 1, "文本分块中...",
                   text_progress=100, table_progress=100, figure_progress=100)
            chunker = TextChunker()
            chunks = chunker.chunk(text_data)
            _report(self.task_id, "chunking", 1, 1, f"分块完成，共{len(chunks)}块",
                   text_progress=100, table_progress=100, figure_progress=100)

            # 阶段3: LLM知识提取
            _report(self.task_id, "knowledge_extraction", 0, len(chunks), "开始知识提取...",
                   text_progress=100, table_progress=100, figure_progress=100)
            kg_extractor = KGExtractor()
            extractions = []
            for i, chunk in enumerate(chunks, 1):
                result = kg_extractor.extract_chunk(chunk)
                if "error" not in result:
                    extractions.append(result)
                _report(self.task_id, "knowledge_extraction", i, len(chunks), f"处理第{i}/{len(chunks)}块",
                       text_progress=100, table_progress=100, figure_progress=100)
                if i % 3 == 0 and i < len(chunks):
                    import time
                    time.sleep(2)

            # 阶段4: 合并结果
            _report(self.task_id, "merging", 0, 1, "合并结果...",
                   text_progress=100, table_progress=100, figure_progress=100)
            merged = kg_extractor.merge_extractions(extractions)

            # 后处理
            figures_map = {}
            for fig in text_data.get("figures", []):
                fname = fig.get("filename", "")
                fpath = fig.get("path", "")
                if fname and fpath:
                    base = os.path.splitext(fname)[0]
                    figures_map[base] = fpath

            processor = PostProcessor()
            merged = processor.process(merged, figures_map)

            # 附加元数据
            merged["metadata"] = {
                "pdf_name": self.original_filename,
                "processed_at": datetime.now().isoformat(),
                "total_pages": text_data.get("total_pages", 0),
                "chunks_processed": len(chunks),
                "tables_html": text_data.get("tables_html", []),
                "text_by_page": text_data.get("text_by_page", {}),
                "figures_dir": text_data.get("figures_dir", ""),
            }

            _report(self.task_id, "completed", 1, 1, "提取完成")
            return merged

        except Exception as e:
            import traceback
            print(f"Pipeline error: {traceback.format_exc()}")
            return {"error": str(e)}


# ============================================================================
# Flask 路由
# ============================================================================
@zstq_bp.route('/extract', methods=['POST'])
def extract():
    if 'file' not in request.files:
        return jsonify({'error': '缺少文件字段'}), 400
    file = request.files['file']
    if not file or file.filename == '':
        return jsonify({'error': '未选择文件'}), 400

    filename_lower = file.filename.lower()
    is_pdf = filename_lower.endswith('.pdf')
    is_docx = filename_lower.endswith(('.docx', '.doc'))
    if not is_pdf and not is_docx:
        return jsonify({'error': '只支持PDF和Word文件'}), 400

    original_filename = file.filename
    suffix = '.pdf' if is_pdf else '.docx'
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            file.save(tmp.name)
            file_path = tmp.name
    except Exception as e:
        return jsonify({'error': f'文件保存失败: {str(e)}'}), 500

    task_id = str(uuid.uuid4())
    with zstq_progress_lock:
        zstq_progress_store[task_id] = {
            'status': 'processing',
            'progress': None,
            'result_path': None,
            'error': None,
        }

    def process_task():
        try:
            pipeline = ZSTQPipeline(task_id, file_path, original_filename, is_pdf)
            result = pipeline.run()
            if 'error' in result:
                with zstq_progress_lock:
                    zstq_progress_store[task_id]['status'] = 'failed'
                    zstq_progress_store[task_id]['error'] = result['error']
                return
            result_path = os.path.join(ZSTQ_TEMP_DIR, f'{task_id}.json')
            with open(result_path, 'w', encoding='utf-8') as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
            with zstq_progress_lock:
                zstq_progress_store[task_id]['result_path'] = result_path
                zstq_progress_store[task_id]['status'] = 'completed'
        except Exception as e:
            import traceback
            print(f"Task {task_id} failed: {traceback.format_exc()}")
            with zstq_progress_lock:
                zstq_progress_store[task_id]['status'] = 'failed'
                zstq_progress_store[task_id]['error'] = str(e)
        finally:
            if os.path.exists(file_path):
                os.unlink(file_path)

    thread = threading.Thread(target=process_task)
    thread.daemon = True
    thread.start()
    return jsonify({'task_id': task_id, 'message': '任务已提交'})


@zstq_bp.route('/progress/<task_id>', methods=['GET'])
def get_progress(task_id):
    with zstq_progress_lock:
        prog = zstq_progress_store.get(task_id)
        if not prog:
            return jsonify({'error': '任务不存在'}), 404
        return jsonify({
            'status': prog['status'],
            'progress': prog['progress'],
            'error': prog.get('error')
        })


@zstq_bp.route('/result/<task_id>', methods=['GET'])
def get_result(task_id):
    with zstq_progress_lock:
        prog = zstq_progress_store.get(task_id)
        if not prog:
            return jsonify({'error': '任务不存在'}), 404
        if prog['status'] != 'completed':
            return jsonify({'error': '任务尚未完成'}), 400
        result_path = prog['result_path']
    try:
        with open(result_path, 'r', encoding='utf-8') as f:
            return jsonify(json.load(f))
    except Exception as e:
        return jsonify({'error': f'读取结果失败: {str(e)}'}), 500


@zstq_bp.route('/confirm', methods=['POST'])
def confirm():
    data = request.get_json()
    if not data or 'session_id' not in data:
        return jsonify({'error': '缺少session_id'}), 400
    session_id = data['session_id']
    result_path = os.path.join(ZSTQ_TEMP_DIR, f'{session_id}.json')
    if not os.path.exists(result_path):
        return jsonify({'error': '会话不存在'}), 404
    try:
        with open(result_path, 'r', encoding='utf-8') as f:
            extraction = json.load(f)
        extraction['confirmed'] = True
        extraction['confirmed_at'] = datetime.now().isoformat()
        nodes = extraction.get('nodes', [])
        relationships = extraction.get('relationships', [])
        with neo4j_driver.session() as session:
            for node in nodes:
                labels = ':'.join(node.get('labels', ['Entity']))
                props = node.get('properties', {})
                cypher = f"MERGE (n:{labels} {{id: $id}}) SET n += $props"
                session.run(cypher, id=node['id'], props=props)
            for rel in relationships:
                cypher = (
                    "MATCH (a {{id: $start}}), (b {{id: $end}}) "
                    "MERGE (a)-[r:{type} {{id: $rid}}]->(b) SET r += $props"
                ).format(type=rel.get('type', 'RELATED_TO'))
                session.run(cypher, start=rel['startNodeId'], end=rel['endNodeId'],
                           rid=rel['id'], props=rel.get('properties', {}))
        return jsonify({'status': 'ok', 'message': '导入成功'})
    except Exception as e:
        return jsonify({'error': f'导入失败: {str(e)}'}), 500


@zstq_bp.route('/save', methods=['POST'])
def save():
    data = request.get_json()
    if not data or 'nodes' not in data or 'relationships' not in data:
        return jsonify({'error': '缺少 nodes 或 relationships'}), 400
    nodes = data['nodes']
    relationships = data['relationships']
    node_ids = {n.get('id') for n in nodes}
    for rel in relationships:
        if rel.get('startNodeId') not in node_ids or rel.get('endNodeId') not in node_ids:
            return jsonify({'error': f'关系 {rel.get("id")} 引用了不存在的节点'}), 400
    try:
        with neo4j_driver.session() as session:
            for node in nodes:
                labels = ':'.join(node.get('labels', ['Entity']))
                props = node.get('properties', {})
                session.run(f"MERGE (n:{labels} {{id: $id}}) SET n += $props",
                           id=node['id'], props=props)
            for rel in relationships:
                cypher = (
                    "MATCH (a {{id: $start}}), (b {{id: $end}}) "
                    "MERGE (a)-[r:{type} {{id: $rid}}]->(b) SET r += $props"
                ).format(type=rel.get('type', 'RELATED_TO'))
                session.run(cypher, start=rel['startNodeId'], end=rel['endNodeId'],
                           rid=rel['id'], props=rel.get('properties', {}))
        return jsonify({'status': 'ok', 'message': '数据已导入图数据库'})
    except Exception as e:
        return jsonify({'error': f'导入失败: {str(e)}'}), 500


@zstq_bp.route('/images/<path:filename>', methods=['GET'])
def get_image(filename):
    image_dir = os.path.join(tempfile.gettempdir())
    for root, dirs, files in os.walk(image_dir):
        if filename in files:
            return send_file(os.path.join(root, filename))
    return 'File not found', 404


@zstq_bp.route('/clear', methods=['POST'])
def clear_graph():
    try:
        with neo4j_driver.session() as session:
            result = session.run("MATCH (n) DETACH DELETE n")
            count = result.consume().counters.nodes_deleted
        return jsonify({"success": True, "message": f"已删除 {count} 个节点"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
