# word_agent.py
"""
Word文档到知识图谱转换器
支持 .docx 和 .doc 格式
"""
import os
import json
import re
import tempfile
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime
from langchain_openai import ChatOpenAI
from langchain_core.prompts import PromptTemplate
from langchain.chains.llm import LLMChain
from langchain.text_splitter import RecursiveCharacterTextSplitter

from backend.agent.data4neo4j.config import DASHSCOPE_CONFIG, PROCESSING_CONFIG


class WordTextExtractor:
    """Word文档文本提取器"""
    
    def __init__(self):
        self.text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=3000,
            chunk_overlap=200,
            length_function=len,
            separators=["\n\n", "\n", "。", "；", "，", " ", ""]
        )
    
    def extract_text_from_docx(self, docx_path: str, progress_callback=None) -> Dict[str, Any]:
        """从Word文档提取文本"""
        try:
            from docx import Document
            
            doc = Document(docx_path)
            full_text = []
            tables = []
            images = []
            
            total_paragraphs = len(doc.paragraphs)
            
            for i, para in enumerate(doc.paragraphs):
                if progress_callback and i % 50 == 0:
                    progress_callback({
                        "current_page": i,
                        "total_pages": total_paragraphs,
                        "page_info": f"第{i}段处理完成"
                    })
                
                text = para.text.strip()
                if text:
                    full_text.append(text)
                
                # 提取标题样式
                style_name = para.style.name if para.style else "Normal"
                
            # 提取表格
            for i, table in enumerate(doc.tables):
                table_data = []
                for row in table.rows:
                    row_data = [cell.text.strip() for cell in row.cells]
                    table_data.append(row_data)
                
                if table_data:
                    tables.append({
                        "rows": table_data,
                        "cell_count": sum(len(row) for row in table_data)
                    })
            
            # 合并文本
            combined_text = "\n\n".join(full_text)
            
            # 提取文档中的图片
            # 注意: python-docx只能提取内嵌图片，需要特殊处理
            image_counter = 0
            for rel in doc.part.rels.values():
                if "image" in rel.target_ref:
                    image_counter += 1
            
            print(f"[OK] Word文档提取完成:")
            print(f"   段落数: {total_paragraphs}")
            print(f"   表格数: {len(tables)}")
            print(f"   图片数: {image_counter}")
            
            return {
                "doc_name": os.path.basename(docx_path),
                "total_paragraphs": total_paragraphs,
                "table_count": len(tables),
                "image_count": image_counter,
                "full_text": combined_text,
                "paragraphs": full_text,
                "tables": tables,
                "extracted_at": datetime.now().isoformat(),
                "extraction_method": "python-docx"
            }
            
        except ImportError:
            return {"error": "请安装 python-docx: pip install python-docx"}
        except Exception as e:
            return {"error": str(e)}
    
    def extract_text_from_doc(self, doc_path: str, progress_callback=None) -> Dict[str, Any]:
        """从旧版 .doc 文件提取文本（需要转换为 .docx）"""
        try:
            import subprocess
            
            # 使用 LibreOffice 或 AntiWord 转换
            # 这里先尝试直接读取，如果失败则需要系统工具
            # 简化处理：直接返回错误，提示用户转换为 .docx
            return {
                "error": ".doc 格式需要先转换为 .docx。请使用 Word 或在线工具转换后再上传。"
            }
        except Exception as e:
            return {"error": str(e)}
    
    def extract(self, file_path: str, original_filename: str = None, progress_callback=None) -> Dict[str, Any]:
        """统一入口，根据文件扩展名选择处理方法"""
        ext = os.path.splitext(file_path)[1].lower()
        
        if ext == '.docx':
            result = self.extract_text_from_docx(file_path, progress_callback)
        elif ext == '.doc':
            result = self.extract_text_from_doc(file_path, progress_callback)
        else:
            return {"error": f"不支持的文件格式: {ext}"}
        
        if "error" not in result:
            result["doc_name"] = original_filename or os.path.basename(file_path)
        
        return result


class WatermarkDetector:
    """水印检测和去重"""
    
    # 常见水印模式
    WATERMARK_PATTERNS = [
        r'仅供.*?使用',
        r'.*?机密.*?',
        r'.*?内部资料.*?',
        r'.*?不准复制.*?',
        r'.*?copyright.*?',
        r'.*?©\s*\d+.*?',
        r'.*?【.*?】',
        r'.*?（.*?）',
        r'页码.*?共.*?页',
        r'第\s*\d+\s*页',
        r'共\s*\d+\s*页',
        r'-\s*\d+\s*-',
        r'/\s*\d+\s*/',
        r'\|\s*\d+\s*\|',
    ]
    
    def __init__(self):
        self.patterns = [re.compile(p, re.IGNORECASE) for p in self.WATERMARK_PATTERNS]
        # 记录已处理的水印文本
        self.seen_watermarks = set()
    
    def is_watermark(self, text: str) -> bool:
        """判断文本是否可能是水印"""
        if not text or len(text.strip()) < 5:
            return False
        
        # 检查是否匹配常见水印模式
        for pattern in self.patterns:
            if pattern.search(text):
                return True
        
        # 检查是否是单行且很短（常见水印特征）
        lines = text.strip().split('\n')
        if len(lines) == 1 and len(text) < 50:
            # 检查是否包含版权符号或"仅供"等关键词
            if any(kw in text for kw in ['©', '版权', '仅供', '内部', '机密', '注意']):
                return True
        
        return False
    
    def extract_watermarks(self, text: str) -> List[str]:
        """从文本中提取所有可能是水印的内容"""
        watermarks = []
        
        # 按行分割
        lines = text.split('\n')
        for line in lines:
            line = line.strip()
            if self.is_watermark(line):
                watermarks.append(line)
        
        return list(set(watermarks))  # 去重
    
    def remove_watermarks(self, text: str) -> str:
        """从文本中移除水印"""
        lines = text.split('\n')
        cleaned_lines = []
        
        for line in lines:
            line = line.strip()
            if not self.is_watermark(line):
                cleaned_lines.append(line)
        
        return '\n'.join(cleaned_lines)
    
    def deduplicate_content(self, content_list: List[str]) -> List[str]:
        """对内容列表去重"""
        seen = set()
        result = []
        
        for content in content_list:
            # 使用内容的哈希作为唯一标识
            content_hash = hash(content.strip())
            if content_hash not in seen:
                seen.add(content_hash)
                result.append(content)
        
        return result


class WordKnowledgeGraphExtractor:
    """Word文档知识图谱提取器"""
    
    def __init__(self):
        self.llm = ChatOpenAI(
            model=DASHSCOPE_CONFIG.model_name,
            temperature=DASHSCOPE_CONFIG.temperature,
            max_tokens=DASHSCOPE_CONFIG.max_tokens,
            api_key=DASHSCOPE_CONFIG.api_key,
            base_url=DASHSCOPE_CONFIG.base_url
        )
        
        # 使用与PDF相同的提示词模板
        self.extraction_prompt = PromptTemplate(
            input_variables=["text_chunk", "pdf_name"],
            template="""你是一个标准文档知识图谱构建专家。请从以下标准文档文本中提取实体和关系。

文本来源：{pdf_name}
文本内容：
{text_chunk}

#### **1. 你的角色与任务**
你是一名专业的知识工程师。你的任务是将输入的文本内容，转化为一个结构化的、包含实体（节点）和关系（边）的JSON对象，以便直接导入图数据库（如Neo4j）或用于构建知识图谱。

#### **2. 核心处理逻辑**
1.  **深度解析与抽取：**
    *   对文档进行解构，识别出所有关键实体（如标准、章节、术语、机构、方法等）以及它们之间的关系。
    *   每个最底层的标题（例如：章节、条、款、项、附录等）必须将正文内容存放在该节点的 `definition` 属性中。
    *   **特别地，对于表格：**
        *   将每个表格识别为一个 `Table` 实体。
        *   **必须完整解析表格结构**，包括表头、行列数据。
        *   在 `tableData` 属性中，需使用结构化对象（如包含 `headers`, `rows` 等字段）。

2.  **知识图谱建模：**
    *   将抽取到的信息建模为两个核心集合：
        *   `nodes`: 代表所有实体，每个实体都是一个对象，包含唯一标识符 (`id`)、标签 (`labels`，代表实体类型) 和属性 (`properties`)。
        *   `relationships`: 代表所有关系，每个关系都是一个对象，包含唯一标识符 (`id`)、类型 (`type`)、起始节点ID (`startNodeId`) 和终止节点ID (`endNodeId`)。

#### **3. 输出格式要求**
```json
{{
  "metadata": {{
    "standardNumber": "标准号",
    "standardTitle_zh": "标准中文标题",
    "publishingOrganization": "发布机构"
  }},
  "nodes": [
    {{
      "id": "唯一标识符",
      "labels": ["实体类型"],
      "properties": {{
        "name": "名称",
        "definition": "定义/描述",
        "number": "编号"
      }}
    }}
  ],
  "relationships": [
    {{
      "id": "关系ID",
      "type": "关系类型",
      "startNodeId": "起始节点ID",
      "endNodeId": "终止节点ID"
    }}
  ]
}}
```

#### **4. 实体与关系定义**
*   **实体类型**: Standard, Clause, Term, Organization, Person, TestMethod, Condition, Device, Table, Figure
*   **关系类型**: HAS_PART, DEFINES, PUBLISHED_BY, CITES, USES_METHOD, UNDER_CONDITION, REQUIRES_DEVICE, CONTAINS_TABLE

**请处理输入的Word文档文本，输出符合上述要求的完整JSON。**"""
        )
        
        self.extraction_chain = LLMChain(llm=self.llm, prompt=self.extraction_prompt)
    
    def extract_from_chunk(self, chunk: Dict[str, Any], verbose: bool = True) -> Dict[str, Any]:
        try:
            result = self.extraction_chain.run(
                text_chunk=chunk["text"],
                pdf_name=chunk["doc_name"]
            )
            
            if verbose:
                print(f"\n      📨 LLM 原始响应 (前500字符):")
                print(f"         {result[:500]}...")
            
            result = self._clean_json_output(result)
            parsed = json.loads(result) if isinstance(result, str) else result
            
            node_count = len(parsed.get("nodes", []))
            rel_count = len(parsed.get("relationships", []))
            print(f"         [STAT] 解析结果: {node_count} 节点, {rel_count} 关系")
            
            return {
                "chunk_id": chunk["chunk_id"],
                "extraction": parsed,
                "timestamp": datetime.now().isoformat(),
            }
            
        except Exception as e:
            print(f"[X] 提取失败 {chunk['chunk_id']}: {e}")
            return {
                "chunk_id": chunk["chunk_id"],
                "error": str(e),
                "extraction": {"nodes": [], "relationships": []}
            }
    
    def _clean_json_output(self, output: str) -> str:
        """清理LLM输出的JSON"""
        if "```json" in output:
            output = output.split("```json")[1].split("```")[0]
        elif "```" in output:
            output = output.split("```")[1].split("```")[0]
        
        output = output.strip()
        output = re.sub(r',\s*}', '}', output)
        output = re.sub(r',\s*]', ']', output)
        
        try:
            data = json.loads(output)
            if "nodes" not in data:
                data["nodes"] = []
            if "relationships" not in data:
                data["relationships"] = []
            return json.dumps(data, ensure_ascii=False)
        except json.JSONDecodeError:
            try:
                start = output.find('{')
                end = output.rfind('}')
                if start != -1 and end != -1 and start < end:
                    output = output[start:end+1]
                    data = json.loads(output)
                    if "nodes" not in data:
                        data["nodes"] = []
                    if "relationships" not in data:
                        data["relationships"] = []
                    return json.dumps(data, ensure_ascii=False)
            except:
                pass
            return json.dumps({"nodes": [], "relationships": []}, ensure_ascii=False)
    
    def merge_extractions(self, extractions: List[Dict[str, Any]]) -> Dict[str, Any]:
        """合并多个提取结果"""
        all_nodes = {}
        all_relationships = {}
        
        for ext in extractions:
            if "extraction" not in ext:
                continue
            
            data = ext["extraction"]
            
            # 合并节点
            for node in data.get("nodes", []):
                node_id = node.get("id")
                if node_id and node_id not in all_nodes:
                    all_nodes[node_id] = node
            
            # 合并关系
            for rel in data.get("relationships", []):
                rel_id = rel.get("id")
                if rel_id and rel_id not in all_relationships:
                    all_relationships[rel_id] = rel
        
        return {
            "nodes": list(all_nodes.values()),
            "relationships": list(all_relationships.values())
        }


class WordToKnowledgeGraphAgent:
    """Word文档到知识图谱主智能体"""
    
    def __init__(self):
        self.text_extractor = WordTextExtractor()
        self.kg_extractor = WordKnowledgeGraphExtractor()
        self.watermark_detector = WatermarkDetector()
        
        self.progress = {
            "total_chunks": 0,
            "processed_chunks": 0,
        }
    
    def process_word(self, file_path: str, original_filename: str = None, 
                     enable_confirmation: bool = False, progress_callback=None) -> Dict[str, Any]:
        """处理Word文档"""
        
        def report(stage, current=0, total=0, message=""):
            if progress_callback:
                percent = int((current / total) * 100) if total > 0 else 0
                progress_callback({
                    "stage": stage,
                    "stage_name": stage,
                    "current": current,
                    "total": total,
                    "percent": percent,
                    "message": message
                })
        
        print(f"\n🚀 开始处理Word文档: {file_path}")
        
        # 步骤1：提取文本
        report("文本提取", 0, 1, "开始文本提取...")
        print("📖 步骤1: 提取Word文档文本...")
        
        text_data = self.text_extractor.extract(file_path, original_filename, progress_callback)
        
        if "error" in text_data:
            print(f"[X] 文本提取失败: {text_data['error']}")
            return text_data
        
        # 步骤2：水印检测和去重
        report("水印处理", 1, 1, "处理水印...")
        print("🧹 步骤2: 检测和移除水印...")
        
        original_text = text_data.get("full_text", "")
        cleaned_text = self.watermark_detector.remove_watermarks(original_text)
        watermarks = self.watermark_detector.extract_watermarks(original_text)
        
        if watermarks:
            print(f"   检测到 {len(watermarks)} 处水印内容")
            for wm in watermarks:
                print(f"   - {wm[:50]}...")
        
        text_data["full_text"] = cleaned_text
        text_data["watermarks"] = watermarks
        
        # 步骤3：文本分块
        report("文本分割", 1, 1, "分割文本...")
        print("🔪 步骤3: 分割文本...")
        
        chunks = self._split_text(cleaned_text, text_data)
        total_chunks = len(chunks)
        self.progress["total_chunks"] = total_chunks
        print(f"[OK] 文本分割完成，共 {total_chunks} 个文本块")
        
        # 步骤4：知识图谱提取
        report("知识提取", 0, total_chunks, "开始知识提取...")
        print("\n🧠 步骤4: 提取知识图谱...")
        
        extractions = []
        for i, chunk in enumerate(chunks, 1):
            print(f"  处理文本块 {i}/{total_chunks} ({len(chunk['text'])}字符)...")
            
            extraction = self.kg_extractor.extract_from_chunk(chunk)
            
            if "error" not in extraction:
                extractions.append(extraction)
                print(f"    ✓ 提取成功")
            else:
                print(f"    ✗ 提取失败: {extraction.get('error')}")
            
            self.progress["processed_chunks"] = i
            report("知识提取", i, total_chunks, f"处理第 {i} 个文本块")
        
        # 步骤5：合并结果
        report("结果合并", 1, 1, "合并结果...")
        print("🔄 步骤5: 合并提取结果...")
        
        merged_extraction = self.kg_extractor.merge_extractions(extractions)
        
        # 添加元数据
        merged_extraction["metadata"] = {
            "doc_name": original_filename or os.path.basename(file_path),
            "processed_at": datetime.now().isoformat(),
            "paragraphs_count": text_data.get("total_paragraphs", 0),
            "table_count": text_data.get("table_count", 0),
            "image_count": text_data.get("image_count", 0),
            "watermarks": watermarks,
            "extraction_method": "python-docx + LLM",
            "llm_config": {
                "model": DASHSCOPE_CONFIG.model_name,
                "base_url": DASHSCOPE_CONFIG.base_url
            }
        }
        
        print(f"[OK] Word文档处理完成:")
        print(f"   节点数: {len(merged_extraction.get('nodes', []))}")
        print(f"   关系数: {len(merged_extraction.get('relationships', []))}")
        
        return merged_extraction
    
    def _split_text(self, text: str, text_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """将文本分割为适合处理的块"""
        chunks = []
        
        # 按段落分块
        paragraphs = text_data.get("paragraphs", [])
        current_chunk_text = []
        current_length = 0
        
        for para in paragraphs:
            if current_length + len(para) > 3000 and current_chunk_text:
                chunks.append({
                    "chunk_id": f"chunk_{len(chunks)}",
                    "text": "\n\n".join(current_chunk_text),
                    "doc_name": text_data.get("doc_name", "unknown")
                })
                current_chunk_text = []
                current_length = 0
            
            current_chunk_text.append(para)
            current_length += len(para)
        
        # 添加最后一个块
        if current_chunk_text:
            chunks.append({
                "chunk_id": f"chunk_{len(chunks)}",
                "text": "\n\n".join(current_chunk_text),
                "doc_name": text_data.get("doc_name", "unknown")
            })
        
        return chunks
