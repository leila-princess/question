"""
环境文档解析器

从 Word 文档中解析环境数据，按一级标题拆分环境，
按二级标题提取各板块（分类/分级/组合/约束/要求），
表格转 HTML，返回结构化 JSON。
"""
import re
import io
import os
import tempfile
import logging
import uuid
import mimetypes
import warnings
from urllib.parse import quote
from docx import Document
from docx.oxml.ns import qn

logger = logging.getLogger(__name__)
_FORMULA_OCR_MODEL = None
_FORMULA_OCR_INIT_DONE = False
os.environ.setdefault("NO_ALBUMENTATIONS_UPDATE", "1")

# 中文数字正则，匹配一级标题如 "一、复杂地理环境"
_LEVEL1_RE = re.compile(r'^[一二三四五六七八九十百千]+[、．.]')

# 二级板块关键词 → 字段映射
_SECTION_MAP = {
    '环境分类': 'type',
    '环境分级': 'class',
    '环境组合': 'combination',
    '约束条件': 'limit',
    '试验要求': 'request',
}


def _table_to_html(table):
    """将 docx 表格转为 HTML 字符串"""
    rows_html = []
    for row in table.rows:
        cells_html = []
        for cell in row.cells:
            text = cell.text.strip().replace('\n', '<br/>')
            cells_html.append(f'<td>{text}</td>')
        rows_html.append(f'<tr>{"".join(cells_html)}</tr>')
    return f'<table border="1" cellpadding="4" cellspacing="0">{"".join(rows_html)}</table>'


def _detect_section(text):
    """检测段落是否为二级板块标题，返回字段名或 None"""
    stripped = text.strip()
    for keyword, field in _SECTION_MAP.items():
        if stripped.startswith(keyword) or stripped == keyword:
            return field
    return None


def _detect_level1(text):
    """检测段落是否为一级标题，返回环境名称或 None"""
    stripped = text.strip()
    if _LEVEL1_RE.match(stripped):
        # 去掉前面的中文数字和标点，得到环境名称
        name = _LEVEL1_RE.sub('', stripped).strip()
        return name if name else stripped
    return None
def _get_paragraph_numbering_info(child):
    """读取段落编号信息，返回 has_numpr / num_id / ilvl。"""
    ppr = child.find(qn('w:pPr'))
    if ppr is None:
        return False, None, None

    num_pr = ppr.find(qn('w:numPr'))
    if num_pr is None:
        return False, None, None

    num_id_node = num_pr.find(qn('w:numId'))
    ilvl_node = num_pr.find(qn('w:ilvl'))
    num_id = num_id_node.get(qn('w:val')) if num_id_node is not None else None
    ilvl = ilvl_node.get(qn('w:val')) if ilvl_node is not None else None
    return True, num_id, ilvl


def _paragraph_has_image(child):
    """判断段落中是否包含图片/绘图对象。"""
    return child.find('.//' + qn('w:drawing')) is not None or child.find('.//' + qn('w:pict')) is not None


def _get_formula_ocr_model():
    """懒加载公式 OCR 模型（pix2tex）。"""
    global _FORMULA_OCR_MODEL, _FORMULA_OCR_INIT_DONE
    if _FORMULA_OCR_INIT_DONE:
        return _FORMULA_OCR_MODEL
    _FORMULA_OCR_INIT_DONE = True
    try:
        warnings.filterwarnings(
            "ignore",
            message=r".*Albumentations is available.*",
            category=UserWarning,
        )
        warnings.filterwarnings(
            "ignore",
            message=r".*Pydantic serializer warnings.*",
            category=UserWarning,
        )
        from pix2tex.cli import LatexOCR
        _FORMULA_OCR_MODEL = LatexOCR()
        logger.info("pix2tex 公式识别模型初始化成功")
    except Exception as e:
        _FORMULA_OCR_MODEL = None
        logger.warning(f"pix2tex 不可用，将回退到普通 OCR: {e}")
    return _FORMULA_OCR_MODEL


def _formula_ocr_from_pil_image(img):
    """优先使用公式 OCR 识别图片中的 LaTeX。"""
    try:
        import cv2
        import numpy as np
        from PIL import Image
    except Exception:
        return ""

    model = _get_formula_ocr_model()
    if model is None:
        return ""

    try:
        gray = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2GRAY)
        binary = cv2.adaptiveThreshold(
            gray,
            255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY,
            31,
            11,
        )
        proc_img = Image.fromarray(binary).convert('RGB')
    except Exception:
        proc_img = img

    latex = ""
    try:
        if callable(model):
            latex = model(proc_img) or ""
        elif hasattr(model, "predict"):
            latex = model.predict(proc_img) or ""
    except Exception as e:
        logger.warning(f"pix2tex 公式识别失败: {e}")
        return ""

    latex = str(latex).strip()
    if not latex:
        return ""
    latex = latex.strip("$")
    return latex


def _upload_blob_to_minio(minio_client, bucket_name, object_path, blob, content_type=None):
    """上传二进制数据到 MinIO。"""
    if not minio_client or not bucket_name or not object_path or not blob:
        return None
    try:
        minio_client.put_object(
            bucket_name,
            object_path,
            io.BytesIO(blob),
            length=len(blob),
            content_type=content_type or 'application/octet-stream'
        )
        return object_path
    except Exception as e:
        logger.warning(f"上传图片到 MinIO 失败: {e}")
        return None


def _process_paragraph_images(doc, child, minio_client=None, bucket_name=None, image_prefix='环境'):
    """处理段落图片：上传 MinIO，返回 OCR 文本和图片信息列表。"""
    try:
        from PIL import Image
        # 按需求：暂时注释掉 OCR 依赖导入（当前不使用 OCR）
        # try:
        #     # 优先绝对导入，兼容以 backend 作为顶层包启动
        #     from ocr import run_ocr_image
        # except Exception:
        #     # 回退相对导入，兼容包内相对引用
        #     from ...ocr import run_ocr_image
        # logger.info("图片OCR依赖导入成功")
    except Exception as e:
        logger.warning(f"图片处理依赖不可用: {e}")
        return "", []

    try:
        runs = child.findall('.//' + qn('w:r'))
        ocr_texts = []
        image_infos = []
        image_count = 0
        for run in runs:
            blips = run.findall('.//' + qn('a:blip'))
            for blip in blips:
                embed_id = blip.get(qn('r:embed'))
                if not embed_id:
                    continue
                image_count += 1
                part = doc.part.related_parts.get(embed_id)
                if part is None:
                    logger.warning(f"图片引用未找到 related_part: embed_id={embed_id}")
                    continue
                blob = part.blob
                logger.info(f"开始OCR第 {image_count} 张段落图片, embed_id={embed_id}, blob_size={len(blob)}")
                img = Image.open(io.BytesIO(blob)).convert('RGB')

                ext = os.path.splitext(getattr(part, 'partname', '') or '')[-1].lower()
                if not ext or ext not in ('.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp'):
                    ext = '.png'
                content_type = mimetypes.guess_type(f'file{ext}')[0] or 'image/png'
                object_path = f"{image_prefix}/image_{image_count}_{uuid.uuid4().hex[:8]}{ext}"
                uploaded_path = _upload_blob_to_minio(
                    minio_client=minio_client,
                    bucket_name=bucket_name,
                    object_path=object_path,
                    blob=blob,
                    content_type=content_type,
                )
                if uploaded_path:
                    image_info = {
                        'path': uploaded_path,
                        'url': f"/api/GLCX/JQCX/minio-image?path={quote(uploaded_path, safe='')}",
                    }
                    image_infos.append(image_info)

                # 按需求：暂时注释掉公式识别（pix2tex）
                # latex_text = _formula_ocr_from_pil_image(img)
                # if latex_text:
                #     logger.info(f"第 {image_count} 张图片公式识别成功: {latex_text[:120]}")
                #     ocr_texts.append(f"[公式LaTeX] {latex_text}")

                tmp_img_path = None
                try:
                    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmpf:
                        tmp_img_path = tmpf.name
                    img.save(tmp_img_path, format="PNG")
                    # 按需求：暂时注释掉普通 OCR，仅保留图片上传与路径回传
                    # with warnings.catch_warnings():
                    #     warnings.filterwarnings(
                    #         "ignore",
                    #         message=r".*Albumentations is available.*",
                    #         category=UserWarning,
                    #     )
                    #     warnings.filterwarnings(
                    #         "ignore",
                    #         message=r".*Pydantic serializer warnings.*",
                    #         category=UserWarning,
                    #     )
                    #     ocr_ret = run_ocr_image(tmp_img_path, use_gpu=False)
                    # text = ""
                    # if isinstance(ocr_ret, dict) and ocr_ret.get("success"):
                    #     text = (
                    #         (ocr_ret.get("data") or {}).get("text", "")
                    #         if isinstance(ocr_ret.get("data"), dict)
                    #         else ""
                    #     )
                    # text = (text or "").strip()
                    text = ""
                finally:
                    if tmp_img_path and os.path.exists(tmp_img_path):
                        try:
                            os.remove(tmp_img_path)
                        except Exception:
                            pass

                logger.info(f"第 {image_count} 张图片OCR结果长度: {len(text)}")
                if text:
                    logger.info(f"第 {image_count} 张图片OCR结果预览: {text[:120]}")
                    ocr_texts.append(text)
        logger.info(f"当前段落共发现图片 {image_count} 张，有效OCR结果 {len(ocr_texts)} 条, 上传成功 {len(image_infos)} 张")
        return '\n'.join([t for t in ocr_texts if t]), image_infos
    except Exception as e:
        logger.warning(f"段落图片OCR失败: {e}")
        return "", []


def _flush_list_item(current_env, current_section, current_list_item):
    """将当前编号项块写回当前板块。"""
    if not current_list_item:
        return None
    if current_env and current_section and current_section in current_env:
        if current_env[current_section]:
            current_env[current_section] += '\n' + current_list_item
        else:
            current_env[current_section] = current_list_item
    return None


def parse_environment_doc(file_path, minio_client=None, bucket_name=None):
    """
    解析环境文档，返回环境列表。

    参数:
        file_path: Word 文档路径

    返回:
        list[dict]: 每个 dict 包含 name, description, type, class, combination, limit, request
    """
    doc = Document(file_path)
    doc_id = uuid.uuid4().hex[:12]
    image_prefix = f"环境/{doc_id}"
    if minio_client and bucket_name:
        try:
            if not minio_client.bucket_exists(bucket_name):
                minio_client.make_bucket(bucket_name)
        except Exception as e:
            logger.warning(f"初始化 MinIO bucket 失败: {e}")

    # 用 body 元素遍历，保持段落和表格的顺序
    body = doc.element.body
    elements = []
    numbered_groups = {}
    for child in body:
        tag = child.tag.split('}')[-1] if '}' in child.tag else child.tag
        if tag == 'p':
            raw_text = child.text or ''
            runs_text = []
            for r in child.findall(qn('w:r')):
                t = r.find(qn('w:t'))
                if t is not None and t.text:
                    runs_text.append(t.text)
            text = ''.join(runs_text) if runs_text else raw_text

            has_numpr, num_id, ilvl = _get_paragraph_numbering_info(child)
            is_numbered = has_numpr and num_id not in (None, '0')
            prefix = ''
            if is_numbered:
                current_idx = numbered_groups.get(num_id, 0)
                if 0 <= current_idx < 26:
                    prefix = f"{chr(ord('a') + current_idx)}) "
                numbered_groups[num_id] = current_idx + 1

            if prefix and text:
                text = prefix + text

            if _paragraph_has_image(child):
                logger.info("检测到包含图片的段落，开始尝试OCR")
                image_ocr_text, image_infos = _process_paragraph_images(
                    doc, child, minio_client=minio_client, bucket_name=bucket_name, image_prefix=image_prefix
                )
                inline_image_html = ""
                if image_infos:
                    html_blocks = []
                    for image in image_infos:
                        image_url = image.get('url') or ''
                        image_path = image.get('path') or ''
                        if image_url:
                            html_blocks.append(
                                f'<div><img src="{image_url}" alt="环境图片" '
                                f'style="max-width:100%;max-height:360px;object-fit:contain;" /></div>'
                            )
                        if image_path:
                            html_blocks.append(
                                f'<div style="margin:4px 0 8px 0;color:#8c8c8c;font-size:12px;">路径：{image_path}</div>'
                            )
                    inline_image_html = '\n'.join(html_blocks)
                if image_ocr_text:
                    if text.strip():
                        text = text + '\n' + image_ocr_text
                    else:
                        text = image_ocr_text
                elif not text.strip():
                    logger.info("图片OCR未产出文本，使用图片内联展示")
                    text = inline_image_html or '[图片]'
                elif inline_image_html:
                    text = text + '\n' + inline_image_html
            else:
                image_infos = []

            elements.append(('para', {
                'text': text,
                'images': image_infos,
                'is_numbered': is_numbered,
                'num_id': num_id,
                'ilvl': ilvl,
            }))
        elif tag == 'tbl':
            # 表格：找到对应的 docx Table 对象
            for table in doc.tables:
                if table._tbl is child:
                    elements.append(('table', table))
                    break

    # 按一级标题拆分环境
    environments = []
    current_env = None
    current_section = None  # 当前二级板块字段名
    description_captured = False
    current_list_item = None

    for elem_type, elem in elements:
        if elem_type == 'para':
            raw_text = elem.get('text', '')
            is_numbered = elem.get('is_numbered', False)
            text = raw_text.strip()
            if not text:
                continue

            # 检测一级标题
            env_name = _detect_level1(text)
            if env_name:
                current_list_item = _flush_list_item(current_env, current_section, current_list_item)
                if current_env:
                    environments.append(current_env)
                current_env = {
                    'name': env_name,
                    'description': '',
                    'type': '',
                    'class': '',
                    'combination': '',
                    'limit': '',
                    'request': '',
                    'images': [],
                }
                current_section = None
                description_captured = False
                continue

            if current_env is None:
                continue

            # 检测二级板块标题
            section = _detect_section(text)
            if section:
                current_list_item = _flush_list_item(current_env, current_section, current_list_item)
                current_section = section
                continue

            para_images = elem.get('images', []) or []
            if para_images:
                current_env.setdefault('images', [])
                current_env['images'].extend(para_images)

            # 如果还没进入任何板块，且紧跟一级标题后面，作为 description
            if current_section is None and not description_captured:
                current_env['description'] = text
                description_captured = True
                continue
            if is_numbered:
                current_list_item = _flush_list_item(current_env, current_section, current_list_item)
                current_list_item = text
                continue

            if current_list_item is not None:
                current_list_item += '\n' + text
                continue

            # 追加到当前板块
            if current_section and current_section in current_env:
                if current_env[current_section]:
                    current_env[current_section] += '\n' + text
                else:
                    current_env[current_section] = text

        elif elem_type == 'table':
            if current_env is None:
                continue
            table_html = _table_to_html(elem)
            if current_list_item is not None:
                current_list_item += '\n' + table_html
                continue
            if current_section and current_section in current_env:
                if current_env[current_section]:
                    current_env[current_section] += '\n' + table_html
                else:
                    current_env[current_section] = table_html

    # 别忘了最后一个环境
    current_list_item = _flush_list_item(current_env, current_section, current_list_item)
    if current_env:
        environments.append(current_env)

    logger.info(f"解析完成，共提取 {len(environments)} 个环境")
    return environments
