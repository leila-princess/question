"""
方案类 Markdown → PDF 统一导出（版式由 utils/md2pdf-formal 下 convert.js + style.css 控制）。

供 JYLLM、JYGZ 等路由复用，避免各处重复 tempfile / generate_pdf 逻辑。

PDF 由 Puppeteer 离线渲染：正文里「同源 /api/...」插图无法加载，故在转 PDF 前将
`/api/fzsj/jyllm/scheme_asset/...`、`/api/fzsj/jyllm/method_asset/...` 换为 data URL（从 MinIO 读取）。
"""
from __future__ import annotations

import base64
import logging
import os
import re
import tempfile
from typing import Optional, Tuple
from urllib.parse import unquote, urlparse, parse_qs

logger = logging.getLogger(__name__)

IMG_MD_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")


def _mime_for_filename(name: str) -> str:
    low = (name or "").lower()
    if low.endswith(".png"):
        return "image/png"
    if low.endswith(".jpg") or low.endswith(".jpeg"):
        return "image/jpeg"
    if low.endswith(".gif"):
        return "image/gif"
    if low.endswith(".webp"):
        return "image/webp"
    if low.endswith(".svg"):
        return "image/svg+xml"
    return "application/octet-stream"


def inline_jyllm_images_for_pdf(md_content: str, *, max_bytes: int = 4 * 1024 * 1024) -> str:
    """把 JYLLM 方案插图 API 路径换为 data URL，便于 md-to-pdf / Puppeteer 内嵌显示。"""
    if not md_content or not str(md_content).strip():
        return md_content
    try:
        from config import Config
    except Exception:
        return md_content
    if not getattr(Config, "MINIO_CLIENT", None):
        return md_content
    bucket = getattr(Config, "MINIO_BUCKET_NAME", None)
    if not bucket:
        return md_content

    def fetch_object(object_name: str) -> Optional[bytes]:
        key = (object_name or "").replace("\\", "/").lstrip("/")
        if ".." in key or key.startswith("/"):
            return None
        try:
            resp = Config.MINIO_CLIENT.get_object(bucket, key)
            data = resp.read()
            resp.close()
            resp.release_conn()
            return data
        except Exception as e:
            logger.debug("inline pdf image skip %s: %s", key, e)
            return None

    def replace_one(match: re.Match) -> str:
        alt, raw_url = match.group(1), match.group(2).strip()
        url = raw_url.split()[0].strip('"').strip("'")
        if url.startswith("data:"):
            return match.group(0)
        if url.startswith(("http://", "https://")):
            parsed = urlparse(url)
            path = parsed.path or ""
            q = parse_qs(parsed.query)
        else:
            fake = urlparse("https://local.invalid" + (url if url.startswith("/") else "/" + url))
            path = fake.path or ""
            q = parse_qs(fake.query)

        blob: Optional[bytes] = None
        mime = "application/octet-stream"

        m_scheme = re.match(r"^/api/fzsj/jyllm/scheme_asset/([^/]+)/([^)\s?#]+)$", path)
        if m_scheme:
            tid, fname = m_scheme.group(1), unquote(m_scheme.group(2))
            if ".." in fname or "/" in fname:
                return match.group(0)
            obj = f"方案/{tid}/assets/{fname}"
            blob = fetch_object(obj)
            mime = _mime_for_filename(fname)

        if blob is None:
            m_method = re.match(r"^/api/fzsj/jyllm/method_asset/([^/?#]+)$", path)
            if m_method:
                obj_enc = (q.get("object") or [None])[0]
                if obj_enc:
                    obj = unquote(obj_enc)
                    if obj.startswith("试验方法/"):
                        blob = fetch_object(obj)
                        mime = _mime_for_filename(obj.split("/")[-1])

        if not blob or len(blob) > max_bytes:
            return match.group(0)
        b64 = base64.b64encode(blob).decode("ascii")
        return f"![{alt}](data:{mime};base64,{b64})"

    return IMG_MD_RE.sub(replace_one, md_content)


def export_markdown_to_pdf_bytes(
    md_content: str,
    *,
    doc_title: str = "试验方案",
    doc_id: str = "",
) -> Tuple[Optional[bytes], Optional[str]]:
    """
    将 Markdown 正文转为 PDF 二进制。

    Returns:
        (pdf_bytes, None) 成功；(None, error_message) 失败。
    """
    if md_content is None or not str(md_content).strip():
        return None, "Markdown 内容为空"

    md_for_pdf = inline_jyllm_images_for_pdf(str(md_content))

    try:
        from utils.md2pdf_wrapper import generate_pdf
    except ImportError as e:
        return None, f"缺少 PDF 依赖: {e}"

    tmp_path = None
    try:
        fd, tmp_path = tempfile.mkstemp(suffix=".pdf")
        os.close(fd)

        ok = generate_pdf(str(md_for_pdf), tmp_path, doc_title or "试验方案", doc_id or "")
        if ok and os.path.isfile(tmp_path):
            with open(tmp_path, "rb") as f:
                return f.read(), None
        return None, "PDF 生成失败（请确认已安装 Node.js，且 utils/md2pdf-formal 可执行）"
    except FileNotFoundError as e:
        logger.error("export_markdown_to_pdf_bytes: %s", e)
        return None, "未找到 Node.js 或 md-to-pdf 运行环境"
    except Exception as e:
        logger.exception("export_markdown_to_pdf_bytes")
        return None, str(e)
    finally:
        if tmp_path and os.path.isfile(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def export_markdown_to_pdf_file(
    md_content: str,
    output_pdf_path: str,
    *,
    doc_title: str = "试验方案",
    doc_id: str = "",
) -> Tuple[bool, Optional[str]]:
    """将 Markdown 写入指定 PDF 路径。"""
    data, err = export_markdown_to_pdf_bytes(md_content, doc_title=doc_title, doc_id=doc_id)
    if err or not data:
        return False, err or "未知错误"
    try:
        with open(output_pdf_path, "wb") as f:
            f.write(data)
        return True, None
    except OSError as e:
        return False, str(e)
