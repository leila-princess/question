import os
import sys
import json
import logging
from datetime import datetime, timezone
from flask import Blueprint, request, jsonify

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from config import Config
from Process_data.extractor import DocumentExtractor
from Database.Chroma.input import ChromaInputManager

logger = logging.getLogger(__name__)

chroma_bp = Blueprint('chroma', __name__, url_prefix='/api/chroma')


@chroma_bp.route('/list', methods=['GET'])
def list_chunks():
    if not Config.CHROMA_COLLECTION_OBJ:
        return jsonify({"success": False, "error": "Chroma not initialized"}), 500

    doc_type = request.args.get('type', '')
    user_id = request.args.get('user_id', '')
    doc_id = request.args.get('doc_id', '')

    try:
        where = {}
        if doc_type:
            where["type"] = doc_type
        if user_id:
            where["User_id"] = user_id
        if doc_id:
            where["doc_id"] = doc_id

        results = Config.CHROMA_COLLECTION_OBJ.get(where=where if where else None)
        ids = results.get("ids", [])
        docs = results.get("documents", [])
        metas = results.get("metadatas", [])

        items = []
        for i in range(len(ids)):
            meta = metas[i] if i < len(metas) else {}
            items.append({
                "id": ids[i],
                "document": docs[i] if i < len(docs) else "",
                "metadata": meta,
            })

        return jsonify({"success": True, "data": items, "count": len(items)})
    except Exception as e:
        logger.error(f"Failed to list chunks: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@chroma_bp.route('/upload', methods=['POST'])
def upload_to_chroma():
    if not Config.CHROMA_COLLECTION_OBJ:
        return jsonify({"success": False, "error": "Chroma not initialized"}), 500

    files = request.files.getlist('files')
    doc_type = request.form.get('doc_type', '标准')
    user_id = request.form.get('user_id', 'admin')
    associations = json.loads(request.form.get('associations', '[]'))

    if not files:
        return jsonify({"success": False, "error": "No files provided"}), 400

    results = []
    extractor = DocumentExtractor(dpi=300, output_dir=Config.OUTPUT_DIR)
    chroma_manager = ChromaInputManager(Config.CHROMA_CLIENT, Config.CHROMA_COLLECTION_OBJ)

    for uploaded_file in files:
        filename = uploaded_file.filename
        if not filename:
            continue

        temp_dir = os.path.join(Config.OUTPUT_DIR, 'uploads')
        os.makedirs(temp_dir, exist_ok=True)
        temp_path = os.path.join(temp_dir, filename)
        uploaded_file.save(temp_path)

        try:
            import hashlib
            doc_id = f"doc_{hashlib.md5(filename.encode()).hexdigest()[:8]}"
            title = os.path.splitext(filename)[0]

            extract_result = extractor.extract(temp_path)
            full_text = extract_result.get("full_text", "")

            from Process_data.extractor import chunk_document
            chunks = chunk_document(full_text, extract_result.get("pages", []))

            chroma_ids = []
            for i, chunk in enumerate(chunks):
                chunk_id = f"{doc_id}_{i}"
                meta = {
                    "doc_id": doc_id,
                    "title": title,
                    "chunk_index": i,
                    "start_pos": chunk.get("start_pos", 0),
                    "end_pos": chunk.get("end_pos", 0),
                    "type": doc_type,
                    "neo4j_testmethod_id": "",
                    "source_file": "",
                    "images": "",
                    "tables": json.dumps(chunk.get("tables", []), ensure_ascii=False),
                    "formulas": json.dumps(chunk.get("formulas", []), ensure_ascii=False),
                    "User_id": user_id,
                    "Create_time": datetime.now(timezone.utc).isoformat(),
                }
                Config.CHROMA_COLLECTION_OBJ.add(
                    ids=[chunk_id],
                    documents=[chunk["text"]],
                    metadatas=[meta],
                )
                chroma_ids.append(chunk_id)

            results.append({
                "success": True,
                "doc_id": doc_id,
                "title": title,
                "chunks": len(chroma_ids),
            })

        except Exception as e:
            logger.error(f"Failed to upload {filename}: {e}")
            results.append({"success": False, "file": filename, "error": str(e)})
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)

    return jsonify({"success": True, "data": results})


@chroma_bp.route('/delete', methods=['POST'])
def delete_from_chroma():
    if not Config.CHROMA_COLLECTION_OBJ:
        return jsonify({"success": False, "error": "Chroma not initialized"}), 500

    data = request.get_json()
    doc_id = data.get('doc_id', '')

    if not doc_id:
        return jsonify({"success": False, "error": "doc_id required"}), 400

    try:
        results = Config.CHROMA_COLLECTION_OBJ.get(where={"doc_id": doc_id})
        ids = results.get("ids", [])
        if ids:
            Config.CHROMA_COLLECTION_OBJ.delete(ids=ids)
        return jsonify({"success": True, "data": {"deleted": len(ids)}, "message": f"Deleted {len(ids)} chunks"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
