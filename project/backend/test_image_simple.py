"""简单测试图片提取和保存"""
import os
import sys
import logging

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

def test_image_extraction():
    """测试图片提取"""
    from config import Config
    from Process_data.extractor import DocumentExtractor
    
    # 初始化配置
    from flask import Flask
    app = Flask(__name__)
    Config.init_app(app)
    
    test_file = r"C:\Users\hyx\antd-demo1\backend\Database\monidata\wurenji\GBT+38924.6-2020.pdf"
    
    if not os.path.exists(test_file):
        logger.error(f"测试文件不存在: {test_file}")
        return
    
    logger.info(f"测试文件: {test_file}")
    logger.info(f"OUTPUT_DIR: {Config.OUTPUT_DIR}")
    
    # 提取文档
    extractor = DocumentExtractor(dpi=300, output_dir=Config.OUTPUT_DIR)
    logger.info(f"OCR引擎: {type(extractor.ocr_engine)}")
    logger.info(f"布局引擎: {type(extractor.layout_engine)}")
    
    extract_result = extractor.extract(test_file)
    
    logger.info(f"\n提取结果:")
    logger.info(f"  总页数: {extract_result.get('total_pages', 0)}")
    logger.info(f"  全文长度: {len(extract_result.get('full_text', ''))}")
    logger.info(f"  表格数: {len(extract_result.get('all_tables', []))}")
    logger.info(f"  图片数: {len(extract_result.get('all_figures', []))}")
    logger.info(f"  公式数: {len(extract_result.get('all_formulas', []))}")
    
    # 检查图片
    all_figures = extract_result.get('all_figures', [])
    logger.info(f"\n图片详情 ({len(all_figures)} 张):")
    
    for i, fig in enumerate(all_figures[:10]):  # 只显示前10张
        fig_path = fig.get('path', '')
        filename = fig.get('filename', 'unknown')
        exists = os.path.exists(fig_path) if fig_path else False
        size = os.path.getsize(fig_path) if exists else 0
        logger.info(f"  [{i}] {filename}")
        logger.info(f"       路径: {fig_path}")
        logger.info(f"       存在: {exists}, 大小: {size} bytes")
    
    # 检查输出目录
    base_name = "GBT+38924.6-2020".replace(".pdf", "").replace("+", "_")
    figures_dir = os.path.join(Config.OUTPUT_DIR, f"ocr_{base_name}", "figures")
    logger.info(f"\n图片目录: {figures_dir}")
    logger.info(f"目录存在: {os.path.exists(figures_dir)}")
    
    if os.path.exists(figures_dir):
        files = os.listdir(figures_dir)
        logger.info(f"目录中文件数: {len(files)}")
        for f in files[:5]:
            fpath = os.path.join(figures_dir, f)
            logger.info(f"  {f} ({os.path.getsize(fpath)} bytes)")
    
    return extract_result

if __name__ == "__main__":
    test_image_extraction()
