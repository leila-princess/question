"""
快速测试文件上传流程 - 验证所有修复
"""
import os
import sys
import json
import logging

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

def test_full_flow():
    from config import Config
    from Process_data.extractor import DocumentExtractor
    from Database.minIo.main import upload_original_file, upload_images_from_extraction
    from Database.Chroma.main import process_single_file, _extract_doc_info
    from datetime import datetime, timezone
    from urllib.parse import quote, unquote
    
    # 初始化配置
    from flask import Flask
    app = Flask(__name__)
    Config.init_app(app)
    
    test_file = r"C:\Users\hyx\antd-demo1\backend\Database\monidata\wurenji\GBT+38924.6-2020.pdf"
    if not os.path.exists(test_file):
        logger.error(f"测试文件不存在: {test_file}")
        return
    
    filename = os.path.basename(test_file)
    doc_id, title = _extract_doc_info(filename)
    category = "标准"
    user_id = "admin"
    file_node_id = f"WJ_{doc_id}"
    ext = ".pdf"
    
    logger.info(f"=" * 60)
    logger.info(f"测试文件: {filename}")
    logger.info(f"doc_id: {doc_id}, title: {title}")
    logger.info(f"file_node_id: {file_node_id}")
    logger.info(f"=" * 60)
    
    # 1. 上传原始文件到MinIO
    logger.info(f"\n[1] 上传原始文件到MinIO...")
    try:
        minio_path = upload_original_file(
            Config.MINIO_CLIENT, Config.MINIO_BUCKET_NAME, test_file,
            doc_id, title, category, "", "", "", user_id
        )
        logger.info(f"    成功: {minio_path}")
    except Exception as e:
        logger.error(f"    失败: {e}")
        return
    
    # 2. 提取文档
    logger.info(f"\n[2] 提取文档...")
    try:
        extractor = DocumentExtractor(dpi=300, output_dir=Config.OUTPUT_DIR)
        extract_result = extractor.extract(test_file)
        logger.info(f"    页数: {extract_result.get('total_pages', 0)}")
        logger.info(f"    全文长度: {len(extract_result.get('full_text', ''))}")
        logger.info(f"    图片数: {len(extract_result.get('all_figures', []))}")
        logger.info(f"    表格数: {len(extract_result.get('all_tables', []))}")
        logger.info(f"    公式数: {len(extract_result.get('all_formulas', []))}")
    except Exception as e:
        logger.error(f"    提取失败: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return
    
    # 3. 上传图片到MinIO
    logger.info(f"\n[3] 上传图片到MinIO...")
    try:
        image_paths = upload_images_from_extraction(
            Config.MINIO_CLIENT, Config.MINIO_BUCKET_NAME, extract_result,
            doc_id, title, category, user_id,
            file_ext=ext.lstrip(".")
        )
        logger.info(f"    上传图片数: {len(image_paths)}")
        for i, path in enumerate(image_paths[:5]):
            logger.info(f"    [{i}] {path}")
    except Exception as e:
        logger.error(f"    图片上传失败: {e}")
        import traceback
        logger.error(traceback.format_exc())
    
    # 4. 同步到Chroma
    logger.info(f"\n[4] 同步到Chroma...")
    chroma_id = ""
    chroma_chunks = 0
    try:
        chroma_result = process_single_file(
            test_file, extractor,
            Config.CHROMA_CLIENT,
            Config.CHROMA_COLLECTION_OBJ,
            Config.MINIO_BUCKET_NAME,
            Config.MINIO_CLIENT, "",
            pre_extracted_result=extract_result,
            pre_doc_id=doc_id,
            pre_title=title,
            category=category
        )
        
        if chroma_result.get("success"):
            chroma_id = chroma_result.get("doc_id", "")
            chroma_chunks = chroma_result.get("chunks", 0)
            logger.info(f"    成功!")
            logger.info(f"    doc_id: {chroma_id}")
            logger.info(f"    chunks: {chroma_chunks}")
            
            # 验证Chroma中的数据
            results = Config.CHROMA_COLLECTION_OBJ.get(where={"doc_id": chroma_id})
            ids = results.get('ids', [])
            logger.info(f"    Chroma中实际chunks数: {len(ids)}")
            if ids:
                logger.info(f"    chunk IDs: {ids[:3]}...")
        else:
            logger.error(f"    失败: {chroma_result.get('error', 'Unknown')}")
    except Exception as e:
        logger.error(f"    Chroma同步失败: {e}")
        import traceback
        logger.error(traceback.format_exc())
    
    # 5. 创建Neo4j File节点
    logger.info(f"\n[5] 创建Neo4j File节点...")
    if Config.NEO4J_DRIVER:
        try:
            # 删除已存在的节点
            with Config.NEO4J_DRIVER.session() as session:
                session.run("MATCH (f:File {Id: $id}) DETACH DELETE f", id=file_node_id)
            
            # 创建新节点
            with Config.NEO4J_DRIVER.session() as session:
                session.run("""
                    CREATE (n:File {
                        Id: $id, Type: $type, Label: $label, Name: $name,
                        Chroma_id: $chroma_id, Chroma_chunks: $chroma_chunks, minIo_id: $minio_id,
                        Image_true: $image_true, Image_minIO_id: $image_ids,
                        Status: '启用', User_id: $user_id,
                        Create_time: $create_time
                    })
                """, id=file_node_id, type=category, label="", name=title,
                    chroma_id=chroma_id, chroma_chunks=chroma_chunks, minio_id=minio_path,
                    image_true=len(image_paths) > 0,
                    image_ids=json.dumps(image_paths),
                    user_id=user_id,
                    create_time=datetime.now(timezone.utc).isoformat())
            
            logger.info(f"    File节点创建成功: {file_node_id}")
            
            # 验证节点
            with Config.NEO4J_DRIVER.session() as session:
                result = session.run("""
                    MATCH (f:File {Id: $id})
                    RETURN f.Chroma_id, f.Chroma_chunks, f.minIo_id, f.Image_minIO_id, f.Image_true
                """, id=file_node_id)
                record = result.single()
                if record:
                    logger.info(f"\n[验证] File节点属性:")
                    logger.info(f"    Chroma_id: '{record['f.Chroma_id']}'")
                    logger.info(f"    Chroma_chunks: {record['f.Chroma_chunks']}")
                    logger.info(f"    minIo_id: {record['f.minIo_id']}")
                    logger.info(f"    Image_true: {record['f.Image_true']}")
                    img_ids = record['f.Image_minIO_id']
                    if img_ids:
                        logger.info(f"    Image_minIO_id: {img_ids[:100]}...")
                    else:
                        logger.info(f"    Image_minIO_id: (空)")
        except Exception as e:
            logger.error(f"    Neo4j操作失败: {e}")
            import traceback
            logger.error(traceback.format_exc())
    else:
        logger.error("    Neo4j未初始化!")
    
    # 6. 最终验证
    logger.info(f"\n{'=' * 60}")
    logger.info(f"最终验证结果:")
    logger.info(f"{'=' * 60}")
    
    issues = []
    if not chroma_id:
        issues.append("Chroma_id 为空!")
    if chroma_chunks == 0:
        issues.append("Chroma_chunks 为 0!")
    if not image_paths:
        issues.append("没有上传图片!")
    
    if issues:
        logger.warning(f"发现问题:")
        for issue in issues:
            logger.warning(f"  - {issue}")
    else:
        logger.info(f"所有检查通过!")
        logger.info(f"  - Chroma_id: {chroma_id}")
        logger.info(f"  - Chroma_chunks: {chroma_chunks}")
        logger.info(f"  - 图片数: {len(image_paths)}")
        logger.info(f"  - MinIO路径: {minio_path}")

if __name__ == "__main__":
    test_full_flow()
