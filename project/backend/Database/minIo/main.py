import os
import hashlib
import json
import re
import logging
import tempfile
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from urllib.parse import quote, unquote

logger = logging.getLogger(__name__)


try:
    from minio import Minio
    from minio.error import S3Error
    MINIO_AVAILABLE = True
except ImportError:
    MINIO_AVAILABLE = False
    logger.warning('minio module not installed')


STANDARD_ID_MAP = {
    'GBT38924.1': ('std001', '民用飞机电气系统设计要求'),
    'GBT38924.2': ('std002', '民用飞机产品安全'),
    'GBT38924.3': ('std003', '民用飞机设计结构'),
    'GBT38924.4': ('std004', '民用飞机环境控制系统'),
    'GBT38924.5': ('std005', '民用飞机燃油系统'),
    'GBT38924.6': ('std006', '民用飞机飞行控制系统'),
    'GBT38924.7': ('std007', '民用飞机导航系统'),
    'GBT38924.8': ('std008', '盐雾试验'),
    'GBT38924.9': ('std009', '防水性试验'),
    'GBT38924.10': ('std010', '砂尘试验'),
    'GBT38924.11': ('std011', '霉菌试验'),
}


def to_minio_meta_value(value: str, max_len: int = 500) -> str:
    """MinIO/S3 用户元数据值必须为 latin-1；非 ASCII 使用 URL 编码。"""
    if not value:
        return ''
    s = str(value)
    if len(s) > max_len:
        s = s[:max_len]
    try:
        s.encode('latin-1')
        return s
    except UnicodeEncodeError:
        return quote(s, safe='')


def prepare_minio_metadata(meta: Dict[str, str]) -> Dict[str, str]:
    """写入 MinIO 前统一编码所有元数据值（含 stat 读回的中文），并限制总大小。"""
    allowed_prefix = 'x-amz-meta-'
    allowed_keys = {
        'x-amz-meta-doc-id',
        'x-amz-meta-title',
        'x-amz-meta-category',
        'x-amz-meta-file-type',
        'x-amz-meta-upload-time',
        'x-amz-meta-file-size',
        'x-amz-meta-checksum',
        'x-amz-meta-upload-userid',
        'x-amz-meta-file-label',
        'x-amz-meta-abstract',
        'x-amz-meta-keywords',
        'x-amz-meta-std-code',
        'x-amz-meta-std-name',
        'x-amz-meta-publish-date',
        'x-amz-meta-issuer',
        'x-amz-meta-review-status',
        'x-amz-meta-file-ics',
        'x-amz-meta-file-ccs',
        'x-amz-meta-graph-link-status',
    }
    out: Dict[str, str] = {}
    for k, v in (meta or {}).items():
        if v is None or v == '':
            continue
        key = str(k).lower()
        if not key.startswith(allowed_prefix) or key not in allowed_keys:
            continue
        val = str(v)
        max_len = 500 if 'abstract' in key else 200
        if 'std-name' in key or 'title' in key:
            max_len = 200
        if 'keywords' in key:
            max_len = 100
        if 'std-code' in key:
            max_len = 50
        if 'publish-date' in key:
            max_len = 32
        if 'issuer' in key:
            max_len = 100
        out[key] = to_minio_meta_value(val, max_len=max_len)

    def encoded_size(items: Dict[str, str]) -> int:
        total = 0
        for k, v in items.items():
            try:
                v.encode('latin-1')
                total += len(k) + len(v)
            except UnicodeEncodeError:
                total += len(k) + len(quote(v, safe=''))
        return total

    drop_order = [
        'x-amz-meta-abstract',
        'x-amz-meta-keywords',
        'x-amz-meta-issuer',
        'x-amz-meta-std-name',
        'x-amz-meta-std-code',
    ]
    while encoded_size(out) > 1800:
        removed = False
        for field in drop_order:
            if field in out:
                del out[field]
                removed = True
                break
        if not removed:
            break
    return out


def update_object_metadata(minio_client, bucket: str, object_name: str,
                           updates: Dict[str, str]) -> None:
    """用 copy_object 原地更新用户元数据，避免 fget/fput 引发签名错误。"""
    from minio.commonconfig import CopySource, REPLACE

    stat = minio_client.stat_object(bucket, object_name)
    allowed_keys = {
        'x-amz-meta-doc-id', 'x-amz-meta-title', 'x-amz-meta-category',
        'x-amz-meta-file-type', 'x-amz-meta-upload-time', 'x-amz-meta-file-size',
        'x-amz-meta-checksum', 'x-amz-meta-upload-userid', 'x-amz-meta-file-label',
        'x-amz-meta-abstract', 'x-amz-meta-keywords', 'x-amz-meta-std-code',
        'x-amz-meta-std-name', 'x-amz-meta-publish-date', 'x-amz-meta-issuer',
        'x-amz-meta-review-status', 'x-amz-meta-file-ics', 'x-amz-meta-file-ccs',
        'x-amz-meta-graph-link-status',
    }
    existing: Dict[str, str] = {}
    if stat.metadata:
        for k, v in stat.metadata.items():
            if v is None:
                continue
            key = str(k).lower()
            if key not in allowed_keys:
                continue
            try:
                existing[key] = unquote(str(v))
            except Exception:
                existing[key] = str(v)

    field_to_key = {
        'title': 'x-amz-meta-title',
        'std_code': 'x-amz-meta-std-code',
        'std_name': 'x-amz-meta-std-name',
        'publish_date': 'x-amz-meta-publish-date',
        'issuer': 'x-amz-meta-issuer',
        'abstract': 'x-amz-meta-abstract',
        'keywords': 'x-amz-meta-keywords',
        'review_status': 'x-amz-meta-review-status',
    }
    for field, val in (updates or {}).items():
        mk = field_to_key.get(field)
        if mk is not None and val is not None:
            existing[mk] = str(val)

    minio_client.copy_object(
        bucket,
        object_name,
        CopySource(bucket, object_name),
        metadata=prepare_minio_metadata(existing),
        metadata_directive=REPLACE,
    )


def _safe_meta(value: str, max_len: int = 500) -> str:
    return to_minio_meta_value(value, max_len=max_len)


def _extract_doc_info(filename: str):
    name_without_ext = os.path.splitext(filename)[0]
    
    for key, (doc_id, title) in STANDARD_ID_MAP.items():
        if key in name_without_ext:
            return doc_id, title
    
    patterns = [
        r'^([A-Z]{2,}[_-]?\d+(?:\.\d+)?(?:-\d+)?)[_-](.+)$',
        r'^([A-Z]+[_]?/\d+[_-]?\d*)[_-](.+)$',
        r'^(GB[/\s]?T|GBT|GJB|YY|JB|QC|CJ|DL|TJ)[+\s_]*(\d+(?:\.\d+)?-\d{4})(?:[_\s]+(.+))?$',
    ]
    
    for pattern in patterns:
        match = re.match(pattern, name_without_ext, re.IGNORECASE)
        if match:
            if len(match.groups()) >= 3 and match.group(3) is not None:
                prefix = match.group(1).strip().upper().replace(' ', '')
                if prefix in ('GBT', 'GB/T'):
                    std_code = f"GB/T {match.group(2).strip()}"
                else:
                    std_code = f"{prefix} {match.group(2).strip()}"
                std_name = (match.group(3) or '').strip()
            else:
                std_code = match.group(1).strip()
                std_name = match.group(2).strip()
            if std_name or std_code:
                doc_id = re.sub(r'[^\w\.\-]+', '_', std_code).strip('_') or f'std_{hashlib.md5(filename.encode()).hexdigest()[:6]}'
                return doc_id, std_name or std_code
    
    doc_id = f'std_{hashlib.md5(filename.encode()).hexdigest()[:6]}'
    return doc_id, name_without_ext


def _extract_ics_ccs_from_pdf(pdf_path: str) -> Dict[str, Any]:
    try:
        from ocr import run_ocr_ics_ccs
        result = run_ocr_ics_ccs(pdf_path)
        if result.get("success"):
            return result["data"]
        return {'ics': '', 'ccs': '', 'needs_manual': True}
    except Exception as e:
        logger.warning(f'Failed to extract ICS/CCS from PDF: {e}')
        return {'ics': '', 'ccs': '', 'needs_manual': True}


def upload_original_file(minio_client, bucket: str, file_path: str,
                         doc_id: str, title: str, category: str = '标准',
                         label: str = '', ics: str = '', ccs: str = '',
                         user_id: str = 'admin', abstract: str = '',
                         keywords: str = '', std_code: str = '',
                         std_name: str = '',
                         publish_date: str = '',
                         issuer: str = '',
                         review_status: str = '',
                         upload_time_iso: Optional[str] = None) -> str:
    ext = os.path.splitext(file_path)[1].lstrip('.')
    folder_name = doc_id
    object_name = f'{category}/{folder_name}/origin.{ext}'
    
    file_size = os.path.getsize(file_path)
    md5_hash = hashlib.md5(open(file_path, 'rb').read()).hexdigest()
    
    upload_ts = upload_time_iso if upload_time_iso else datetime.now(timezone.utc).isoformat()
    metadata = {
        'x-amz-meta-doc-id': to_minio_meta_value(doc_id, 64),
        'x-amz-meta-title': to_minio_meta_value(title, 200) if title else '',
        'x-amz-meta-category': to_minio_meta_value(category[:10], 20),
        'x-amz-meta-file-type': ext,
        'x-amz-meta-upload-time': to_minio_meta_value(upload_ts[:64], 64),
        'x-amz-meta-file-size': str(file_size),
        'x-amz-meta-checksum': f'md5:{md5_hash}',
        'x-amz-meta-upload-userid': to_minio_meta_value(user_id, 80),
    }
    
    if label:
        metadata['x-amz-meta-file-label'] = to_minio_meta_value(label, 20)
    if abstract:
        metadata['x-amz-meta-abstract'] = to_minio_meta_value(abstract, 500)
    if keywords:
        metadata['x-amz-meta-keywords'] = to_minio_meta_value(keywords, 100)
    if std_code:
        metadata['x-amz-meta-std-code'] = to_minio_meta_value(std_code, 50)
    if std_name:
        metadata['x-amz-meta-std-name'] = to_minio_meta_value(std_name, 200)
    if publish_date:
        metadata['x-amz-meta-publish-date'] = to_minio_meta_value(publish_date, 32)
    if issuer:
        metadata['x-amz-meta-issuer'] = to_minio_meta_value(issuer, 100)
    if review_status:
        metadata['x-amz-meta-review-status'] = to_minio_meta_value(review_status, 20)
    
    def calc_metadata_size(metadata):
        """计算元数据URL编码后的实际字节大小"""
        size = 0
        for k, v in metadata.items():
            val_str = str(v)
            try:
                val_str.encode('latin-1')
                size += len(k) + len(val_str)
            except UnicodeEncodeError:
                encoded = quote(val_str, safe='')
                size += len(k) + len(encoded)
        return size
    
    total_size = calc_metadata_size(metadata)
    if total_size > 2000:
        removable = [
            'x-amz-meta-abstract',
            'x-amz-meta-keywords',
            'x-amz-meta-std-code',
            'x-amz-meta-std-name',
        ]
        for field in removable:
            if field in metadata:
                del metadata[field]
                total_size = calc_metadata_size(metadata)
                if total_size <= 2000:
                    break
    
    minio_client.fput_object(bucket, object_name, file_path, metadata=metadata)

    try:
        stat = minio_client.stat_object(bucket, object_name)
        logger.info("[MINIO-VERIFY] Object %s verified, size=%s", object_name, stat.size)
    except Exception as ve:
        logger.warning("[MINIO-VERIFY] Failed to verify %s: %s", object_name, ve)
        raise

    return object_name


def save_image_to_minio(minio_client, bucket: str, image_path: str, doc_id: str,
                        title: str, category: str, folder_name: str,
                        page_num: int, image_type: str, filename: str,
                        user_id: str = 'admin', description: str = '',
                        source_file: str = '') -> str:
    safe_filename = f"page{page_num}_{filename}"
    object_path = f'{category}/{folder_name}/images/{safe_filename}'
    file_size = os.path.getsize(image_path)
    
    def to_meta(val):
        if not val:
            return ''
        s = str(val)
        try:
            s.encode('latin-1')
            return s
        except UnicodeEncodeError:
            return quote(s, safe='')
            
    metadata = {
        'x-amz-meta-doc-id': to_meta(doc_id),
        'x-amz-meta-source-file': to_meta(source_file) if source_file else to_meta(f'{category}/{folder_name}/origin.pdf'),
        'x-amz-meta-upload-time': datetime.now(timezone.utc).isoformat(),
        'x-amz-meta-file-size': str(file_size),
        'x-amz-meta-page-number': str(page_num),
        'x-amz-meta-image-type': to_meta(image_type),
        'x-amz-meta-description': to_meta(description) if description else '',
        'x-amz-meta-upload-userid': to_meta(user_id),
    }
    minio_client.fput_object(bucket, object_path, image_path, metadata=metadata)
    return object_path


def save_pil_image(minio_client, bucket: str, pil_image, doc_id: str,
                   title: str, category: str, folder_name: str,
                   page_num: int, image_type: str, filename: str,
                   user_id: str = 'admin', description: str = '',
                   source_file: str = '') -> str:
    temp_dir = tempfile.mkdtemp()
    temp_path = os.path.join(temp_dir, filename)
    try:
        pil_image.save(temp_path)
        return save_image_to_minio(minio_client, bucket, temp_path, doc_id, title, category,
                                   folder_name, page_num, image_type, filename, user_id, description, source_file)
    finally:
        try:
            os.unlink(temp_path)
            os.rmdir(temp_dir)
        except:
            pass


def upload_images_from_extraction(minio_client, bucket: str, extract_result: Dict,
                                   doc_id: str, title: str, category: str, user_id: str,
                                   file_ext: str = 'pdf') -> List[str]:
    if not extract_result:
        return []
    
    image_paths = []
    all_figures = extract_result.get('all_figures', [])
    for idx, fig in enumerate(all_figures, 1):
        fig_path = fig.get('path', '')
        if not fig_path or not os.path.exists(fig_path):
            continue
        try:
            page_num = fig.get('page', idx)
            img_type = fig.get('image_type', 'figure')
            ext = os.path.splitext(fig_path)[1].lstrip('.') or 'png'
            filename = f'图{idx}.{ext}'
            obj_path = save_image_to_minio(minio_client, bucket, fig_path, doc_id, title, category,
                                            doc_id, page_num, img_type, filename, user_id)
            image_paths.append(obj_path)
        except Exception as e:
            logger.warning(f'Failed to upload figure {idx}: {e}')
    return image_paths