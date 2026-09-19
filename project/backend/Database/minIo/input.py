import os
import json
import hashlib
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

VALID_CATEGORIES = {"标准", "方案", "模板", "规范", "其他"}


class MinIOInputManager:
    def __init__(self, client, bucket_name):
        self.client = client
        self.bucket_name = bucket_name

    def _ensure_bucket(self):
        if not self.client.bucket_exists(self.bucket_name):
            self.client.make_bucket(self.bucket_name)
            logger.info(f"Bucket {self.bucket_name} created")

    def _compute_md5(self, file_path):
        md5 = hashlib.md5()
        with open(file_path, 'rb') as f:
            for chunk in iter(lambda: f.read(8192), b''):
                md5.update(chunk)
        return md5.hexdigest()

    def _build_metadata(self, doc_id, title, category, file_type,
                        chroma_id=None, neo4j_id=None, source_path=None,
                        file_size=None, checksum=None, extra=None):
        meta = {
            "x-amz-meta-doc-id": doc_id or "",
            "x-amz-meta-title": title or "",
            "x-amz-meta-category": category or "",
            "x-amz-meta-file-type": file_type or "",
            "x-amz-meta-chroma-id": chroma_id or "",
            "x-amz-meta-neo4j-id": neo4j_id or "",
            "x-amz-meta-source-path": source_path or "",
            "x-amz-meta-upload-time": datetime.now(timezone.utc).isoformat(),
            "x-amz-meta-file-size": str(file_size or 0),
            "x-amz-meta-checksum": f"md5:{checksum}" if checksum else "",
        }
        if extra:
            for k, v in extra.items():
                key = k if k.startswith("x-amz-meta-") else f"x-amz-meta-{k}"
                meta[key] = str(v)
        return meta

    def upload_file(self, file_path, doc_id=None, title=None, category="其他",
                    folder_name=None, chroma_id=None, neo4j_id=None,
                    extra_metadata=None):
        try:
            self._ensure_bucket()
            if category not in VALID_CATEGORIES:
                return {"success": False, "error": f"Invalid category: {category}. Must be one of {VALID_CATEGORIES}"}

            file_name = os.path.basename(file_path)
            file_ext = os.path.splitext(file_name)[1].lstrip('.')
            file_size = os.path.getsize(file_path)
            checksum = self._compute_md5(file_path)

            if not folder_name:
                folder_name = os.path.splitext(file_name)[0].replace(" ", "_").replace("+", "_")

            object_name = f"{category}/{folder_name}/origin.{file_ext}"
            source_path = object_name

            if not doc_id:
                doc_id = f"{file_ext}_{hashlib.md5(file_path.encode()).hexdigest()[:8]}"
            if not title:
                title = file_name

            metadata = self._build_metadata(
                doc_id=doc_id, title=title, category=category,
                file_type=file_ext, chroma_id=chroma_id, neo4j_id=neo4j_id,
                source_path=source_path, file_size=file_size, checksum=checksum,
                extra=extra_metadata
            )

            self.client.fput_object(
                self.bucket_name, object_name, file_path,
                metadata=metadata
            )
            return {
                "success": True,
                "data": {
                    "doc_id": doc_id,
                    "object_name": object_name,
                    "category": category,
                    "file_size": file_size,
                    "checksum": checksum,
                },
                "message": f"File uploaded to {object_name}"
            }
        except Exception as e:
            logger.error(f"Failed to upload file: {e}")
            return {"success": False, "error": str(e)}

    def upload_image(self, image_path, doc_id, source_file, page_number=None,
                     image_type="other", description=None, category="其他",
                     folder_name=None, chroma_id=None, neo4j_id=None):
        try:
            self._ensure_bucket()
            file_name = os.path.basename(image_path)
            file_size = os.path.getsize(image_path)

            if not folder_name:
                folder_name = os.path.splitext(source_file)[0]

            object_name = f"{category}/{folder_name}/images/{file_name}"

            metadata = {
                "x-amz-meta-doc-id": doc_id or "",
                "x-amz-meta-source-file": source_file or "",
                "x-amz-meta-chroma-id": chroma_id or "",
                "x-amz-meta-neo4j-id": neo4j_id or "",
                "x-amz-meta-page-number": str(page_number) if page_number else "",
                "x-amz-meta-image-type": image_type or "other",
                "x-amz-meta-description": description or "",
                "x-amz-meta-upload-time": datetime.now(timezone.utc).isoformat(),
                "x-amz-meta-file-size": str(file_size),
            }

            self.client.fput_object(
                self.bucket_name, object_name, image_path,
                metadata=metadata
            )
            return {
                "success": True,
                "data": {"object_name": object_name, "doc_id": doc_id},
                "message": f"Image uploaded to {object_name}"
            }
        except Exception as e:
            logger.error(f"Failed to upload image: {e}")
            return {"success": False, "error": str(e)}

    def upload_folder(self, folder_path, category="其他", doc_id_prefix=None,
                      chroma_id=None, neo4j_id=None):
        try:
            self._ensure_bucket()
            if category not in VALID_CATEGORIES:
                return {"success": False, "error": f"Invalid category: {category}"}

            results = []
            folder_name = os.path.basename(folder_path)
            image_extensions = {'.png', '.jpg', '.jpeg', '.gif', '.bmp', '.tiff', '.webp'}
            doc_extensions = {'.pdf', '.docx', '.doc', '.txt', '.md'}

            for root, dirs, files in os.walk(folder_path):
                for fname in files:
                    fpath = os.path.join(root, fname)
                    ext = os.path.splitext(fname)[1].lower()

                    if ext in doc_extensions:
                        rel_dir = os.path.relpath(root, folder_path)
                        sub_folder = f"{folder_name}/{rel_dir}".replace("\\", "/") if rel_dir != "." else folder_name
                        result = self.upload_file(
                            file_path=fpath,
                            category=category,
                            folder_name=sub_folder,
                            chroma_id=chroma_id,
                            neo4j_id=neo4j_id,
                        )
                        results.append({"file": fname, "result": result})

                    elif ext in image_extensions:
                        rel_dir = os.path.relpath(root, folder_path)
                        sub_folder = f"{folder_name}/{rel_dir}".replace("\\", "/") if rel_dir != "." else folder_name
                        result = self.upload_image(
                            image_path=fpath,
                            doc_id=doc_id_prefix or folder_name,
                            source_file=f"{category}/{sub_folder}/origin.pdf",
                            category=category,
                            folder_name=sub_folder,
                        )
                        results.append({"file": fname, "result": result})

            success_count = sum(1 for r in results if r["result"].get("success"))
            return {
                "success": True,
                "data": {"total": len(results), "success": success_count, "details": results},
                "message": f"Processed {len(results)} files, {success_count} succeeded"
            }
        except Exception as e:
            logger.error(f"Failed to upload folder: {e}")
            return {"success": False, "error": str(e)}

    def get_file_info(self, object_name):
        try:
            stat = self.client.stat_object(self.bucket_name, object_name)
            meta = {}
            if stat.metadata:
                for k, v in stat.metadata.items():
                    meta[k] = v
            return {
                "success": True,
                "data": {
                    "object_name": object_name,
                    "size": stat.size,
                    "etag": stat.etag,
                    "content_type": stat.content_type,
                    "last_modified": str(stat.last_modified),
                    "metadata": meta,
                },
                "message": "File info retrieved"
            }
        except Exception as e:
            logger.error(f"Failed to get file info: {e}")
            return {"success": False, "error": str(e)}

    def list_files(self, prefix="", recursive=True):
        try:
            objects = self.client.list_objects(self.bucket_name, prefix=prefix, recursive=recursive)
            files = []
            for obj in objects:
                files.append({
                    "object_name": obj.object_name,
                    "size": obj.size,
                    "last_modified": str(obj.last_modified),
                    "etag": obj.etag,
                })
            return {"success": True, "data": files, "count": len(files), "message": f"Listed {len(files)} files"}
        except Exception as e:
            logger.error(f"Failed to list files: {e}")
            return {"success": False, "error": str(e)}

    def list_by_category(self, category):
        return self.list_files(prefix=f"{category}/")

    def download_file(self, object_name, dest_path):
        try:
            self.client.fget_object(self.bucket_name, object_name, dest_path)
            return {"success": True, "data": {"dest_path": dest_path}, "message": f"Downloaded to {dest_path}"}
        except Exception as e:
            logger.error(f"Failed to download: {e}")
            return {"success": False, "error": str(e)}

    def get_presigned_url(self, object_name, expires=3600):
        try:
            url = self.client.presigned_get_object(
                self.bucket_name, object_name, expires=expires
            )
            return {"success": True, "data": {"url": url}, "message": "Presigned URL generated"}
        except Exception as e:
            logger.error(f"Failed to generate presigned URL: {e}")
            return {"success": False, "error": str(e)}
