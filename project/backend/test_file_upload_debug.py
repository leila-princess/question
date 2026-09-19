"""
测试文件上传流程 - 调试向量数据库关联和图片保存问题
"""
import os
import sys
import logging

# 设置路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

def test_extraction_and_chroma():
    """测试文档提取和Chroma存储"""
    from config import Config
    from Process_data.extractor import DocumentExtractor, chunk_document
    from Database.Chroma.main import process_single_file, _extract_doc_info
    
    # 初始化配置
    from flask import Flask
    app = Flask(__name__)
    Config.init_app(app)
    
    # 测试文件
    test_file = r"C:\Users\hyx\antd-demo1\backend\Database\monidata\wurenji\GBT+38924.6-2020.pdf"
    
    if not os.path.exists(test_file):
        logger.error(f"测试文件不存在: {test_file}")
        return
    
    logger.info(f"测试文件: {test_file}")
    
    # 1. 测试文档提取
    logger.info("=" * 60)
    logger.info("步骤1: 文档提取")
    logger.info("=" * 60)
    
    extractor = DocumentExtractor(dpi=300, output_dir=Config.OUTPUT_DIR)
    extract_result = extractor.extract(test_file)
    
    logger.info(f"提取结果:")
    logger.info(f"  - 总页数: {extract_result.get('total_pages', 0)}")
    logger.info(f"  - 全文长度: {len(extract_result.get('full_text', ''))}")
    logger.info(f"  - 表格数: {len(extract_result.get('all_tables', []))}")
    logger.info(f"  - 图片数: {len(extract_result.get('all_figures', []))}")
    logger.info(f"  - 公式数: {len(extract_result.get('all_formulas', []))}")
    
    # 检查图片路径
    all_figures = extract_result.get('all_figures', [])
    logger.info(f"\n图片详情:")
    for i, fig in enumerate(all_figures):
        fig_path = fig.get('path', '')
        exists = os.path.exists(fig_path) if fig_path else False
        logger.info(f"  [{i}] {fig.get('filename', 'unknown')}")
        logger.info(f"       路径: {fig_path}")
        logger.info(f"       存在: {exists}")
    
    # 2. 测试Chroma处理
    logger.info("\n" + "=" * 60)
    logger.info("步骤2: Chroma向量数据库处理")
    logger.info("=" * 60)
    
    if Config.CHROMA_COLLECTION_OBJ is None:
        logger.error("Chroma集合未初始化!")
        return
    
    # 调用 process_single_file
    chroma_result = process_single_file(
        test_file, extractor,
        Config.CHROMA_CLIENT,
        Config.CHROMA_COLLECTION_OBJ,
        Config.MINIO_BUCKET_NAME,
        Config.MINIO_CLIENT,
        ""  # neo4j_method_id
    )
    
    logger.info(f"Chroma处理结果:")
    logger.info(f"  - success: {chroma_result.get('success', False)}")
    logger.info(f"  - doc_id: {chroma_result.get('doc_id', '')}")
    logger.info(f"  - title: {chroma_result.get('title', '')}")
    logger.info(f"  - chunks: {chroma_result.get('chunks', 0)}")
    logger.info(f"  - pages: {chroma_result.get('pages', 0)}")
    logger.info(f"  - images: {chroma_result.get('images', 0)}")
    logger.info(f"  - error: {chroma_result.get('error', 'None')}")
    
    # 3. 验证Chroma中的数据
    logger.info("\n" + "=" * 60)
    logger.info("步骤3: 验证Chroma中的数据")
    logger.info("=" * 60)
    
    doc_id = chroma_result.get('doc_id', '')
    if doc_id:
        # 查询Chroma中的chunks
        results = Config.CHROMA_COLLECTION_OBJ.get(where={"doc_id": doc_id})
        ids = results.get('ids', [])
        logger.info(f"Chroma中 doc_id={doc_id} 的chunks数: {len(ids)}")
        if ids:
            logger.info(f"  chunk IDs: {ids[:5]}{'...' if len(ids) > 5 else ''}")
            
            # 检查第一个chunk的metadata
            metas = results.get('metadatas', [])
            if metas:
                logger.info(f"\n第一个chunk的metadata:")
                for k, v in metas[0].items():
                    logger.info(f"    {k}: {str(v)[:100]}")
    
    return chroma_result


def test_minio_image_upload():
    """测试MinIO图片上传"""
    from config import Config
    from Process_data.extractor import DocumentExtractor
    from Database.minIo.main import upload_images_from_extraction, upload_original_file
    
    # 初始化配置
    from flask import Flask
    app = Flask(__name__)
    Config.init_app(app)
    
    test_file = r"C:\Users\hyx\antd-demo1\backend\Database\monidata\wurenji\GBT+38924.6-2020.pdf"
    
    if not os.path.exists(test_file):
        logger.error(f"测试文件不存在: {test_file}")
        return
    
    logger.info("=" * 60)
    logger.info("测试MinIO图片上传")
    logger.info("=" * 60)
    
    # 提取文档
    extractor = DocumentExtractor(dpi=300, output_dir=Config.OUTPUT_DIR)
    extract_result = extractor.extract(test_file)
    
    doc_id = "std006"  # GBT38924.6 的 doc_id
    title = "振动试验"
    category = "标准"
    
    # 上传原始文件
    logger.info("\n上传原始文件到MinIO...")
    minio_path = upload_original_file(
        Config.MINIO_CLIENT, Config.MINIO_BUCKET_NAME, test_file,
        doc_id, title, category, "", "", "", "admin"
    )
    logger.info(f"原始文件路径: {minio_path}")
    
    # 上传图片
    logger.info("\n上传图片到MinIO...")
    image_paths = upload_images_from_extraction(
        Config.MINIO_CLIENT, Config.MINIO_BUCKET_NAME, extract_result,
        doc_id, title, category, "admin"
    )
    logger.info(f"上传图片数: {len(image_paths)}")
    for i, path in enumerate(image_paths[:5]):
        logger.info(f"  [{i}] {path}")
    
    return image_paths


def test_neo4j_file_node():
    """测试Neo4j文件节点创建"""
    from config import Config
    from datetime import datetime, timezone
    import json
    
    # 初始化配置
    from flask import Flask
    app = Flask(__name__)
    Config.init_app(app)
    
    if Config.NEO4J_DRIVER is None:
        logger.error("Neo4j未初始化!")
        return
    
    logger.info("=" * 60)
    logger.info("测试Neo4j文件节点")
    logger.info("=" * 60)
    
    # 查询现有的File节点
    with Config.NEO4J_DRIVER.session() as session:
        result = session.run("""
            MATCH (f:File)
            WHERE f.Id CONTAINS 'std006' OR f.Name CONTAINS '38924.6'
            RETURN f.Id, f.Name, f.Chroma_id, f.Chroma_chunks, f.minIo_id, f.Image_minIO_id
            ORDER BY f.Create_time DESC
            LIMIT 5
        """)
        
        records = list(result)
        logger.info(f"\n找到 {len(records)} 个相关File节点:")
        for r in records:
            logger.info(f"\n  Id: {r['f.Id']}")
            logger.info(f"  Name: {r['f.Name']}")
            logger.info(f"  Chroma_id: {r['f.Chroma_id']}")
            logger.info(f"  Chroma_chunks: {r['f.Chroma_chunks']}")
            logger.info(f"  minIo_id: {r['f.minIo_id']}")
            logger.info(f"  Image_minIO_id: {r['f.Image_minIO_id']}")


def test_full_upload_flow():
    """测试完整的上传流程"""
    from config import Config
    from Process_data.extractor import DocumentExtractor
    from Database.minIo.main import upload_original_file, upload_images_from_extraction
    from Database.Chroma.main import process_single_file
    from datetime import datetime, timezone
    import json
    
    # 初始化配置
    from flask import Flask
    app = Flask(__name__)
    Config.init_app(app)
    
    test_file = r"C:\Users\hyx\antd-demo1\backend\Database\monidata\wurenji\GBT+38924.6-2020.pdf"
    
    if not os.path.exists(test_file):
        logger.error(f"测试文件不存在: {test_file}")
        return
    
    logger.info("=" * 60)
    logger.info("完整上传流程测试")
    logger.info("=" * 60)
    
    # 参数
    doc_id = "std006"
    title = "振动试验"
    category = "标准"
    label = ""
    user_id = "admin"
    file_node_id = f"WJ_{doc_id}"
    
    # 1. 上传原始文件到MinIO
    logger.info("\n[1] 上传原始文件到MinIO...")
    minio_path = upload_original_file(
        Config.MINIO_CLIENT, Config.MINIO_BUCKET_NAME, test_file,
        doc_id, title, category, label, "", "", user_id
    )
    logger.info(f"    MinIO路径: {minio_path}")
    
    # 2. 提取文档
    logger.info("\n[2] 提取文档...")
    extractor = DocumentExtractor(dpi=300, output_dir=Config.OUTPUT_DIR)
    extract_result = extractor.extract(test_file)
    logger.info(f"    页数: {extract_result.get('total_pages', 0)}")
    logger.info(f"    图片数: {len(extract_result.get('all_figures', []))}")
    
    # 3. 上传图片到MinIO
    logger.info("\n[3] 上传图片到MinIO...")
    image_paths = upload_images_from_extraction(
        Config.MINIO_CLIENT, Config.MINIO_BUCKET_NAME, extract_result,
        doc_id, title, category, user_id
    )
    logger.info(f"    上传图片数: {len(image_paths)}")
    
    # 4. 同步到Chroma
    logger.info("\n[4] 同步到Chroma...")
    chroma_result = process_single_file(
        test_file, extractor,
        Config.CHROMA_CLIENT,
        Config.CHROMA_COLLECTION_OBJ,
        Config.MINIO_BUCKET_NAME,
        Config.MINIO_CLIENT,
        ""
    )
    
    chroma_id = ""
    chroma_chunks = 0
    if chroma_result.get("success"):
        first_id = chroma_result.get("doc_id", "")
        chroma_id = f"{first_id}_0" if first_id else ""
        chroma_chunks = chroma_result.get("chunks", 0)
        logger.info(f"    Chroma同步成功!")
        logger.info(f"    doc_id: {first_id}")
        logger.info(f"    chroma_id: {chroma_id}")
        logger.info(f"    chunks: {chroma_chunks}")
    else:
        logger.error(f"    Chroma同步失败: {chroma_result.get('error', 'Unknown')}")
    
    # 5. 创建Neo4j File节点
    logger.info("\n[5] 创建Neo4j File节点...")
    if Config.NEO4J_DRIVER:
        # 先删除已存在的节点
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
            """, id=file_node_id, type=category, label=label, name=name,
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
                RETURN f.Chroma_id, f.Chroma_chunks, f.minIo_id, f.Image_minIO_id
            """, id=file_node_id)
            record = result.single()
            if record:
                logger.info(f"\n[验证] File节点属性:")
                logger.info(f"    Chroma_id: {record['f.Chroma_id']}")
                logger.info(f"    Chroma_chunks: {record['f.Chroma_chunks']}")
                logger.info(f"    minIo_id: {record['f.minIo_id']}")
                logger.info(f"    Image_minIO_id: {record['f.Image_minIO_id'][:100]}...")
    else:
        logger.error("    Neo4j未初始化!")
    
    logger.info("\n" + "=" * 60)
    logger.info("测试完成!")
    logger.info("=" * 60)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--test", choices=["extract", "minio", "neo4j", "full"], default="full")
    args = parser.parse_args()
    
    if args.test == "extract":
        test_extraction_and_chroma()
    elif args.test == "minio":
        test_minio_image_upload()
    elif args.test == "neo4j":
        test_neo4j_file_node()
    else:
        test_full_upload_flow()
