"""
md-to-pdf 封装：通过 Node.js 调用 md-to-pdf 生成高质量 PDF
"""
import os
import subprocess
import logging
from datetime import date

logger = logging.getLogger(__name__)

MD2PDF_DIR = os.path.dirname(os.path.abspath(__file__))
CONVERT_SCRIPT = os.path.join(MD2PDF_DIR, 'md2pdf-formal', 'convert.js')

def standardize_markdown(text: str) -> str:
    """
    使用 mdformat 将 Markdown 转换为标准 GFM 格式。
    如果失败，返回原始文本。
    """
    if not text:
        return text
    # 含 data URL 大图时 mdformat 易慢/异常，跳过
    if "](data:image" in text:
        return text
    try:
        import mdformat
        # 使用 mdformat 的 GFM 扩展，将 Markdown 转换为标准格式
        return mdformat.text(text, extensions={'gfm'})
    except ImportError:
        logger.warning("未安装 mdformat，跳过 Markdown 标准化")
        return text
    except Exception as e:
        logger.warning(f"Markdown 标准化失败，使用原始内容: {e}")
        return text

def generate_pdf(md_content: str, output_path: str, doc_name: str = '试验方案', doc_id: str = '') -> bool:
    """
    将 Markdown 内容写入临时文件，调用 Node.js md-to-pdf 生成 PDF。

    Args:
        md_content: Markdown 源内容
        output_path: 输出 PDF 路径
        doc_name: 文档名称（用于封面和页眉）
        doc_id: 文档标识（用于封面）

    Returns:
        是否成功
    """
    md_path = output_path.replace('.pdf', '.md')

    # 标准化 Markdown 内容 - 在转换为PDF之前，先将原始 Markdown 转换为标准、规范的 Markdown
    standardized_content = standardize_markdown(md_content)

    # 封面 + 签署栏同页（签署在底部居中），正文从新页起；不再插入空白分页 div，避免第二页空白
    gen_date = date.today().isoformat()
    cover_html = f"""<div class="cover-sheet">
  <div class="cover-main">
    <div class="doc-id">文档标识：{doc_id or '——'}</div>
    <div class="title">{doc_name}</div>
    <div class="org-date">生成日期：{gen_date}</div>
  </div>
  <div class="signature-footer" aria-label="签署栏">
    <div class="signature-footer-title">签署栏</div>
    <div class="signature-rows">
      <div class="signature-row"><span class="signature-label">编写：</span><span class="signature-line"></span></div>
      <div class="signature-row"><span class="signature-label">审核：</span><span class="signature-line"></span></div>
      <div class="signature-row"><span class="signature-label">会签：</span><span class="signature-line"></span></div>
      <div class="signature-row"><span class="signature-label">批准：</span><span class="signature-line"></span></div>
    </div>
  </div>
</div>

"""

    full_content = cover_html + standardized_content

    try:
        with open(md_path, 'w', encoding='utf-8') as f:
            f.write(full_content)

        result = subprocess.run(
            ['node', CONVERT_SCRIPT, md_path, output_path, doc_name],
            capture_output=True,
            text=True,
            timeout=120,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        )

        if result.returncode != 0:
            logger.error(f"md-to-pdf 失败: {result.stderr}")
            return False

        logger.info(f"PDF 生成成功: {output_path}")
        return True

    except subprocess.TimeoutExpired:
        logger.error("md-to-pdf 超时 (120s)")
        return False
    except FileNotFoundError:
        logger.error("未找到 Node.js，请确保已安装")
        return False
    except Exception as e:
        logger.error(f"md-to-pdf 异常: {e}")
        return False
    finally:
        # 清理临时 .md 文件
        if os.path.exists(md_path):
            try:
                os.remove(md_path)
            except OSError:
                pass