import os
import re
import uuid
import logging
import sys
import docx
from typing import List, Dict, Any, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

logger = logging.getLogger(__name__)


class ChromaInputManager:
    def __init__(self, client, collection):
        self.client = client
        self.collection = collection

    def _chunk_text(self, text, chunk_size=500, overlap=50):
        chunks = []
        sentences = re.split(r'(?<=[。！？.!?\n])', text)
        current = ""
        for s in sentences:
            s = s.strip()
            if not s:
                continue
            if len(current) + len(s) > chunk_size and current:
                chunks.append(current)
                current = s
            else:
                current += s
        if current:
            chunks.append(current)
        if not chunks and text.strip():
            for i in range(0, len(text), chunk_size - overlap):
                chunks.append(text[i:i + chunk_size])
        return chunks

    def _extract_text_from_pdf(self, pdf_path):
        try:
            from ocr import run_ocr_pdf
            result = run_ocr_pdf(pdf_path)
            if result.get("success"):
                return result["data"].get("full_text", "")
            return ""
        except Exception as e:
            logger.error(f"OCR extraction failed: {e}, falling back to PyMuPDF")
            try:
                import fitz
                doc = fitz.open(pdf_path)
                text = ""
                for page in doc:
                    text += page.get_text()
                doc.close()
                return text
            except Exception as e2:
                logger.error(f"PyMuPDF fallback also failed: {e2}")
                return ""

    def _extract_text_from_word(self, docx_path):
        try:
            doc = docx.Document(docx_path)
            return "\n".join([p.text for p in doc.paragraphs if p.text.strip()])
        except Exception as e:
            logger.error(f"Failed to extract Word text: {e}")
            return ""

    def _extract_text_from_file(self, file_path):
        ext = os.path.splitext(file_path)[1].lower()
        if ext == '.pdf':
            return self._extract_text_from_pdf(file_path)
        elif ext in ['.docx', '.doc']:
            return self._extract_text_from_word(file_path)
        elif ext in ['.txt', '.md']:
            with open(file_path, 'r', encoding='utf-8') as f:
                return f.read()
        else:
            logger.warning(f"Unsupported file type: {ext}")
            return ""

    def add_document(self, doc_id, title, text, doc_type="其他",
                     neo4j_testmethod_id=None, source_file=None, images=None,
                     chunk_size=500, chunk_overlap=50):
        try:
            if self.collection is None:
                return {"success": False, "error": "Chroma collection not initialized"}

            chunks = self._chunk_text(text, chunk_size, chunk_overlap)
            if not chunks:
                return {"success": False, "error": "No text content to add"}

            ids = []
            documents = []
            metadatas = []
            for i, chunk in enumerate(chunks):
                chunk_id = f"{doc_id}_{i}"
                ids.append(chunk_id)
                documents.append(chunk)
                meta = {
                    "doc_id": doc_id,
                    "title": title,
                    "chunk_index": i,
                    "type": doc_type,
                    "source_file": source_file or "",
                }
                if neo4j_testmethod_id:
                    meta["neo4j_testmethod_id"] = neo4j_testmethod_id
                if images and i < len(images):
                    meta["images"] = "、".join(images[i]) if isinstance(images[i], list) else str(images[i])
                else:
                    meta["images"] = ""
                metadatas.append(meta)

            self.collection.add(
                ids=ids,
                documents=documents,
                metadatas=metadatas,
            )
            return {
                "success": True,
                "data": {"doc_id": doc_id, "chunks": len(chunks), "ids": ids},
                "message": f"Added {len(chunks)} chunks for document {doc_id}"
            }
        except Exception as e:
            logger.error(f"Failed to add document: {e}")
            return {"success": False, "error": str(e)}

    def add_file(self, file_path, doc_id=None, title=None, doc_type="其他",
                 neo4j_testmethod_id=None, source_file=None, images=None,
                 chunk_size=500, chunk_overlap=50):
        try:
            text = self._extract_text_from_file(file_path)
            if not text:
                return {"success": False, "error": "No text extracted from file"}

            if not doc_id:
                doc_id = f"doc_{uuid.uuid4().hex[:8]}"
            if not title:
                title = os.path.basename(file_path)
            if not source_file:
                source_file = file_path

            return self.add_document(
                doc_id=doc_id, title=title, text=text,
                doc_type=doc_type,
                neo4j_testmethod_id=neo4j_testmethod_id,
                source_file=source_file, images=images,
                chunk_size=chunk_size, chunk_overlap=chunk_overlap
            )
        except Exception as e:
            logger.error(f"Failed to add file: {e}")
            return {"success": False, "error": str(e)}

    def add_folder(self, folder_path, doc_type="其他", chunk_size=500, chunk_overlap=50):
        try:
            results = []
            supported_exts = {'.pdf', '.docx', '.doc', '.txt', '.md'}
            for root, _, files in os.walk(folder_path):
                for fname in files:
                    fpath = os.path.join(root, fname)
                    ext = os.path.splitext(fname)[1].lower()
                    if ext not in supported_exts:
                        continue
                    result = self.add_file(
                        file_path=fpath,
                        doc_type=doc_type,
                        chunk_size=chunk_size,
                        chunk_overlap=chunk_overlap
                    )
                    results.append({"file": fname, "result": result})
            success_count = sum(1 for r in results if r["result"].get("success"))
            return {
                "success": True,
                "data": {"total": len(results), "success": success_count, "details": results},
                "message": f"Processed {len(results)} files, {success_count} succeeded"
            }
        except Exception as e:
            logger.error(f"Failed to add folder: {e}")
            return {"success": False, "error": str(e)}

    def query_similar(self, query_text, n_results=5, filters=None):
        try:
            if self.collection is None:
                return {"success": False, "error": "Chroma collection not initialized"}
            where = filters if filters else None
            results = self.collection.query(
                query_texts=[query_text],
                n_results=n_results,
                where=where,
            )
            docs = results.get("documents", [[]])[0]
            metas = results.get("metadatas", [[]])[0]
            ids = results.get("ids", [[]])[0]
            dists = results.get("distances", [[]])[0] if "distances" in results else []
            items = []
            for i in range(len(docs)):
                item = {"id": ids[i], "document": docs[i], "metadata": metas[i]}
                if dists:
                    item["distance"] = dists[i]
                items.append(item)
            return {"success": True, "data": items, "count": len(items), "message": f"Found {len(items)} similar documents"}
        except Exception as e:
            logger.error(f"Failed to query: {e}")
            return {"success": False, "error": str(e)}

    def get_document_chunks(self, doc_id):
        try:
            if self.collection is None:
                return {"success": False, "error": "Chroma collection not initialized"}
            results = self.collection.get(where={"doc_id": doc_id})
            ids = results.get("ids", [])
            docs = results.get("documents", [])
            metas = results.get("metadatas", [])
            chunks = []
            for i in range(len(ids)):
                chunks.append({
                    "id": ids[i],
                    "document": docs[i],
                    "metadata": metas[i],
                })
            chunks.sort(key=lambda x: x["metadata"].get("chunk_index", 0))
            return {"success": True, "data": chunks, "count": len(chunks), "message": f"Retrieved {len(chunks)} chunks"}
        except Exception as e:
            logger.error(f"Failed to get chunks: {e}")
            return {"success": False, "error": str(e)}
