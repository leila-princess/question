"""
ZNWD5 Zvec 索引管理：graph_entities + documents 两个 collection。
zvec 不可用时自动降级到 FallbackVectorStore。
"""

from __future__ import annotations

import logging
import os
import re
import threading
from typing import Any, Dict, List, Optional

try:
    from .embedding_service import embedding_dim
except ImportError:
    from embedding_service import embedding_dim

try:
    from .fallback_store import FallbackVectorStore, _safe_doc_id
except ImportError:
    from fallback_store import FallbackVectorStore, _safe_doc_id

logger = logging.getLogger(__name__)

_ZVEC_AVAILABLE = False
try:
    import zvec
    from zvec import (
        CollectionOption,
        CollectionSchema,
        DataType,
        Doc,
        FieldSchema,
        Fts,
        FtsIndexParam,
        HnswIndexParam,
        Query,
        RrfReRanker,
        VectorSchema,
        create_and_open,
        init as zvec_init,
        open as zvec_open,
    )

    _ZVEC_AVAILABLE = True
    _ZVEC_INIT_DONE = False
except ImportError:
    zvec = None  # type: ignore
    CollectionOption = None  # type: ignore
    zvec_init = None  # type: ignore
    RrfReRanker = None  # type: ignore
    _ZVEC_INIT_DONE = False

GRAPH_LABELS = [
    "EquipmentType",
    "Equipment",
    "Environment",
    "TestProject",
    "Indicator",
    "TestMethod",
    "File",
]

_manager: Optional["ZvecIndexManager"] = None


def _hybrid_reranker() -> Any:
    """多路 query（向量+FTS）合并时 zvec 0.5 要求指定 reranker。"""
    if not _ZVEC_AVAILABLE or RrfReRanker is None:
        return None
    try:
        return RrfReRanker(rank_constant=60)
    except Exception as exc:
        logger.debug("[ZNWD5][zvec] RrfReRanker init skipped: %s", exc)
        return None


def zvec_available() -> bool:
    return _ZVEC_AVAILABLE


def get_zvec_manager() -> "ZvecIndexManager":
    global _manager
    if _manager is None:
        base = os.environ.get("ZVEC_DATA_PATH") or os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
            "zvec_data",
        )
        _manager = ZvecIndexManager(os.path.abspath(base))
    return _manager


def reset_zvec_manager() -> None:
    """关闭并重置单例（索引重建前调用，避免 read_only 句柄占 LOCK）。"""
    global _manager
    if _manager is not None:
        _manager.close()
        _manager = None


def remove_zvec_lock_files(base_path: Optional[str] = None, *, collections: Optional[tuple] = None) -> None:
    """删除 zvec LOCK 文件。注意：Windows/zvec0.5 在正常 close 后删 LOCK 会导致下次无法 open，仅重建索引前调用。"""
    if base_path is None:
        base_path = os.environ.get("ZVEC_DATA_PATH") or os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
            "zvec_data",
        )
    base_path = os.path.abspath(base_path)
    subs = collections or ("graph_entities", "documents")
    for sub in subs:
        lock = os.path.join(base_path, sub, "LOCK")
        if os.path.isfile(lock):
            try:
                os.remove(lock)
                logger.info("[ZNWD5][zvec] removed LOCK %s", lock)
            except OSError as exc:
                logger.warning("[ZNWD5][zvec] cannot remove LOCK %s: %s", lock, exc)


class ZvecIndexManager:
    """统一管理 graph_entities 与 documents 索引。"""

    def __init__(self, base_path: str):
        self.base_path = base_path
        self.graph_path = os.path.join(base_path, "graph_entities")
        self.doc_path = os.path.join(base_path, "documents")
        self.use_zvec = _ZVEC_AVAILABLE
        self._graph = None
        self._docs = None
        self._graph_fb: Optional[FallbackVectorStore] = None
        self._doc_fb: Optional[FallbackVectorStore] = None
        self._opened = False
        self._read_only = True
        self._lock = threading.RLock()

    def _ensure_zvec_init(self) -> None:
        global _ZVEC_INIT_DONE
        if not self.use_zvec or _ZVEC_INIT_DONE or zvec_init is None:
            return
        try:
            zvec_init()
            _ZVEC_INIT_DONE = True
        except Exception as exc:
            logger.debug("[ZNWD5][zvec] init skipped: %s", exc)

    def open_graph(self, *, read_only: bool = True, create: bool = False) -> None:
        with self._lock:
            if self._graph is not None:
                if read_only or not self._read_only:
                    return
                try:
                    self._graph.close()
                except Exception:
                    pass
                self._graph = None
            os.makedirs(self.base_path, exist_ok=True)
            if self.use_zvec:
                self._ensure_zvec_init()
                last_exc: Optional[Exception] = None
                for attempt in range(2):
                    if attempt and read_only:
                        # 只读模式：仅在首次 open 失败时尝试清 stale LOCK 再重试
                        remove_zvec_lock_files(self.base_path, collections=("graph_entities",))
                    try:
                        self._graph = self._open_graph_zvec(read_only=read_only, create=create)
                        self._opened = True
                        if not read_only:
                            self._read_only = False
                        logger.info("[ZNWD5][zvec] graph_entities opened read_only=%s create=%s", read_only, create)
                        return
                    except (RuntimeError, FileNotFoundError) as exc:
                        last_exc = exc
                        if attempt == 0 and "lock" in str(exc).lower():
                            logger.debug("[ZNWD5][zvec] graph lock retry: %s", exc)
                            continue
                        raise
                if last_exc:
                    raise last_exc
            if self._graph_fb is None:
                self._graph_fb = FallbackVectorStore(self.base_path, "graph_entities")
            self._opened = True

    def open_docs(self, *, read_only: bool = True, create: bool = False) -> None:
        with self._lock:
            if self._docs is not None:
                if read_only or not self._read_only:
                    return
                try:
                    self._docs.close()
                except Exception:
                    pass
                self._docs = None
            os.makedirs(self.base_path, exist_ok=True)
            if self.use_zvec:
                self._ensure_zvec_init()
                last_exc: Optional[Exception] = None
                for attempt in range(2):
                    if attempt and read_only:
                        remove_zvec_lock_files(self.base_path, collections=("documents",))
                    try:
                        self._docs = self._open_docs_zvec(read_only=read_only, create=create)
                        self._opened = True
                        if not read_only:
                            self._read_only = False
                        logger.info("[ZNWD5][zvec] documents opened read_only=%s create=%s", read_only, create)
                        return
                    except (RuntimeError, FileNotFoundError) as exc:
                        last_exc = exc
                        if attempt == 0 and "lock" in str(exc).lower():
                            logger.debug("[ZNWD5][zvec] documents lock retry: %s", exc)
                            continue
                        raise
                if last_exc:
                    raise last_exc
            if self._doc_fb is None:
                self._doc_fb = FallbackVectorStore(self.base_path, "documents")
            self._opened = True

    def open(self, *, read_only: bool = True) -> None:
        """打开 graph + documents（在线问答默认只读）。"""
        with self._lock:
            if self._opened and self._read_only == read_only and self._graph is not None and self._docs is not None:
                return
            if self._opened and read_only and not self._read_only:
                return
            if self._opened and not read_only:
                self.close()

            os.makedirs(self.base_path, exist_ok=True)
            if self.use_zvec:
                self._ensure_zvec_init()
                graph_ok = False
                docs_ok = False
                try:
                    create = not read_only
                    try:
                        self.open_graph(read_only=read_only, create=create)
                        graph_ok = self._graph is not None
                    except (RuntimeError, FileNotFoundError) as exc:
                        msg = str(exc).lower()
                        logger.warning("[ZNWD5][zvec] graph_entities 打开失败: %s", exc)
                        if not read_only or "not found" not in msg:
                            if "lock" not in msg:
                                raise

                    try:
                        self.open_docs(read_only=read_only, create=create)
                        docs_ok = self._docs is not None
                    except (RuntimeError, FileNotFoundError) as exc:
                        logger.warning("[ZNWD5][zvec] documents 打开失败: %s", exc)
                        if not read_only:
                            raise

                    if graph_ok or docs_ok:
                        if not graph_ok:
                            self._graph_fb = FallbackVectorStore(self.base_path, "graph_entities")
                        if not docs_ok:
                            self._doc_fb = FallbackVectorStore(self.base_path, "documents")
                        self._opened = True
                        self._read_only = read_only
                        logger.info(
                            "[ZNWD5][zvec] 已打开索引 backend=zvec graph=%s docs=%s fb_graph=%s fb_docs=%s",
                            "ok" if graph_ok else "fallback",
                            "ok" if docs_ok else "fallback",
                            self._graph_fb.count() if self._graph_fb else 0,
                            self._doc_fb.count() if self._doc_fb else 0,
                        )
                        return

                    logger.warning(
                        "[ZNWD5][zvec] graph/documents 均无法打开，降级 FallbackVectorStore",
                    )
                    self._fallback_to_local_store()
                    return
                except (RuntimeError, FileNotFoundError) as exc:
                    msg = str(exc).lower()
                    if read_only and ("lock" in msg or "missing" in msg or "not found" in msg):
                        logger.warning(
                            "[ZNWD5][zvec] 无法以只读打开 zvec（%s），降级 FallbackVectorStore",
                            exc,
                        )
                        self._fallback_to_local_store()
                        return
                    raise
            self._fallback_to_local_store()

    def _fallback_to_local_store(self) -> None:
        self._graph = None
        self._docs = None
        self._graph_fb = FallbackVectorStore(self.base_path, "graph_entities")
        self._doc_fb = FallbackVectorStore(self.base_path, "documents")
        self._opened = True
        self._read_only = True
        self.use_zvec = False
        logger.warning(
            "[ZNWD5][zvec] 使用 FallbackVectorStore graph=%d docs=%d",
            self._graph_fb.count(),
            self._doc_fb.count(),
        )

    def close(self) -> None:
        with self._lock:
            for attr in ("_graph", "_docs"):
                coll = getattr(self, attr, None)
                if coll is not None:
                    try:
                        coll.close()
                    except Exception:
                        pass
                    setattr(self, attr, None)
            self._graph_fb = None
            self._doc_fb = None
            self._opened = False

    def _ensure_open(self) -> None:
        if not self._opened:
            self.open(read_only=True)

    def _ensure_graph_open(self) -> None:
        if self._graph is None and self._graph_fb is None:
            self.open_graph(read_only=True)

    def _ensure_docs_open(self) -> None:
        if self._docs is None and self._doc_fb is None:
            self.open_docs(read_only=True)

    def _graph_schema(self):
        dim = embedding_dim()
        return CollectionSchema(
            name="graph_entities",
            fields=[
                FieldSchema("neo4j_id", DataType.STRING),
                FieldSchema("node_type", DataType.STRING),
                FieldSchema("name", DataType.STRING, index_param=FtsIndexParam()),
                FieldSchema("status", DataType.STRING),
                FieldSchema("text", DataType.STRING, index_param=FtsIndexParam()),
            ],
            vectors=VectorSchema("embedding", DataType.VECTOR_FP32, dim, index_param=HnswIndexParam()),
        )

    def _docs_schema(self):
        dim = embedding_dim()
        return CollectionSchema(
            name="documents",
            fields=[
                FieldSchema("doc_id", DataType.STRING),
                FieldSchema("title", DataType.STRING, index_param=FtsIndexParam()),
                FieldSchema("section", DataType.STRING),
                FieldSchema("std_code", DataType.STRING),
                FieldSchema("content", DataType.STRING, index_param=FtsIndexParam()),
            ],
            vectors=VectorSchema("embedding", DataType.VECTOR_FP32, dim, index_param=HnswIndexParam()),
        )

    @staticmethod
    def _zvec_open_option(read_only: bool):
        return CollectionOption(read_only=read_only, enable_mmap=True)

    def _open_graph_zvec(self, *, read_only: bool, create: bool):
        schema = self._graph_schema()
        opt = self._zvec_open_option(read_only)
        if os.path.isdir(self.graph_path):
            return zvec_open(path=self.graph_path, option=opt)
        if create:
            return create_and_open(path=self.graph_path, schema=schema, option=self._zvec_open_option(False))
        raise FileNotFoundError(f"zvec graph index missing: {self.graph_path}")

    def _open_docs_zvec(self, *, read_only: bool, create: bool):
        schema = self._docs_schema()
        opt = self._zvec_open_option(read_only)
        if os.path.isdir(self.doc_path):
            return zvec_open(path=self.doc_path, option=opt)
        if create:
            return create_and_open(path=self.doc_path, schema=schema, option=self._zvec_open_option(False))
        raise FileNotFoundError(f"zvec documents index missing: {self.doc_path}")

    @staticmethod
    def make_graph_doc_id(node_type: str, neo4j_id: str) -> str:
        return _safe_doc_id(f"{node_type}_{neo4j_id}")

    def upsert_graph_entities(self, rows: List[Dict[str, Any]]) -> int:
        if not rows:
            return 0
        if self._read_only:
            raise RuntimeError("zvec index opened read-only; call open(read_only=False) before upsert")
        if self.use_zvec and self._graph is not None:
            docs = []
            for row in rows:
                nt = str(row.get("node_type") or "")
                nid = str(row.get("neo4j_id") or row.get("id") or "")
                doc_id = self.make_graph_doc_id(nt, nid)
                docs.append(
                    Doc(
                        id=doc_id,
                        vectors={"embedding": row["vector"]},
                        fields={
                            "neo4j_id": nid,
                            "node_type": nt,
                            "name": str(row.get("name") or ""),
                            "status": str(row.get("status") or ""),
                            "text": str(row.get("text") or ""),
                        },
                    )
                )
            self._graph.insert(docs)
            return len(docs)
        if self._graph_fb is not None:
            items = []
            for row in rows:
                nt = str(row.get("node_type") or "")
                nid = str(row.get("neo4j_id") or row.get("id") or "")
                items.append(
                    {
                        "id": self.make_graph_doc_id(nt, nid),
                        "vector": row.get("vector"),
                        "fields": {
                            "neo4j_id": nid,
                            "node_type": nt,
                            "name": str(row.get("name") or ""),
                            "status": str(row.get("status") or ""),
                            "text": str(row.get("text") or ""),
                        },
                    }
                )
            return self._graph_fb.upsert_batch(items)
        return 0

    def upsert_documents(self, rows: List[Dict[str, Any]]) -> int:
        if not rows:
            return 0
        if self._read_only:
            raise RuntimeError("zvec index opened read-only; call open(read_only=False) before upsert")
        if self.use_zvec and self._docs is not None:
            docs = []
            for row in rows:
                doc_id = _safe_doc_id(str(row.get("chunk_id") or row.get("doc_id") or ""))
                docs.append(
                    Doc(
                        id=doc_id,
                        vectors={"embedding": row["vector"]},
                        fields={
                            "doc_id": str(row.get("doc_id") or ""),
                            "title": str(row.get("title") or ""),
                            "section": str(row.get("section") or ""),
                            "std_code": str(row.get("std_code") or ""),
                            "content": str(row.get("content") or ""),
                        },
                    )
                )
            self._docs.insert(docs)
            return len(docs)
        if self._doc_fb is not None:
            items = []
            for row in rows:
                items.append(
                    {
                        "id": _safe_doc_id(str(row.get("chunk_id") or row.get("doc_id") or "")),
                        "vector": row.get("vector"),
                        "fields": {
                            "doc_id": str(row.get("doc_id") or ""),
                            "title": str(row.get("title") or ""),
                            "section": str(row.get("section") or ""),
                            "std_code": str(row.get("std_code") or ""),
                            "content": str(row.get("content") or ""),
                        },
                    }
                )
            return self._doc_fb.upsert_batch(items)
        return 0

    def _normalize_hits(self, raw: Any) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        if not raw:
            return out
        for item in raw:
            if isinstance(item, dict):
                fields = item.get("fields") if isinstance(item.get("fields"), dict) else {}
                out.append(
                    {
                        "id": item.get("id"),
                        "score": float(item.get("score") or 0.0),
                        "fields": fields,
                    }
                )
        return out

    def search_graph_entities(
        self,
        query_vector: List[float],
        *,
        topk: int = 12,
        node_types: Optional[List[str]] = None,
        status_filter: Optional[str] = None,
        fts_text: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        self._ensure_graph_open()
        filter_expr = self._build_graph_filter(node_types, status_filter)
        if self.use_zvec and self._graph is not None:
            queries = [Query(field_name="embedding", vector=query_vector)]
            if fts_text:
                queries.append(Query(field_name="text", fts=Fts(match_string=fts_text)))
            reranker = _hybrid_reranker() if len(queries) > 1 else None
            raw = self._graph.query(queries=queries, topk=topk, filter=filter_expr, reranker=reranker)
            return self._normalize_hits(raw)
        if self._graph_fb is not None:
            if fts_text:
                return self._graph_fb.query_hybrid(
                    query_vector, fts_text, topk=topk, filter_expr=filter_expr, fts_field="text"
                )
            return self._graph_fb.query_vector(query_vector, topk=topk, filter_expr=filter_expr)
        return []

    def search_graph_fts(
        self,
        text: str,
        *,
        topk: int = 12,
        node_types: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        self._ensure_graph_open()
        filter_expr = self._build_graph_filter(node_types, None)
        if self.use_zvec and self._graph is not None:
            raw = self._graph.query(
                queries=Query(field_name="text", fts=Fts(match_string=text)),
                topk=topk,
                filter=filter_expr,
            )
            return self._normalize_hits(raw)
        if self._graph_fb is not None:
            return self._graph_fb.query_fts(text, field="text", topk=topk, filter_expr=filter_expr)
        return []

    def search_documents_hybrid(
        self,
        query_vector: List[float],
        query_text: str,
        *,
        topk: int = 12,
        std_code: str = "",
        doc_id: str = "",
    ) -> List[Dict[str, Any]]:
        self._ensure_docs_open()
        filter_expr = self._build_doc_filter(std_code, doc_id)
        if self.use_zvec and self._docs is not None:
            raw = self._docs.query(
                queries=[
                    Query(field_name="embedding", vector=query_vector),
                    Query(field_name="content", fts=Fts(match_string=query_text)),
                ],
                topk=topk,
                filter=filter_expr,
                reranker=_hybrid_reranker(),
            )
            return self._normalize_hits(raw)
        if self._doc_fb is not None:
            return self._doc_fb.query_hybrid(
                query_vector, query_text, topk=topk, filter_expr=filter_expr, fts_field="content"
            )
        return []

    @staticmethod
    def _build_graph_filter(node_types: Optional[List[str]], status_filter: Optional[str]) -> Optional[str]:
        parts: List[str] = []
        if node_types:
            quoted = ", ".join(f"'{x}'" for x in node_types if x)
            if quoted:
                # zvec filter 语法：IN 使用圆括号，非方括号
                parts.append(f"node_type IN ({quoted})")
        if status_filter:
            parts.append(f"status == '{status_filter}'")
        return " AND ".join(parts) if parts else None

    @staticmethod
    def _build_doc_filter(std_code: str, doc_id: str) -> Optional[str]:
        parts: List[str] = []
        sc = re.sub(r"\s+", "", (std_code or "").strip()).upper()
        if sc.startswith("GBT") and not sc.startswith("GB/T"):
            sc = "GB/T" + sc[3:]
        sc = sc.replace("／", "/")
        if sc:
            parts.append(f"std_code == '{sc}'")
        did = (doc_id or "").strip()
        if did:
            parts.append(f"doc_id == '{did}'")
        return " AND ".join(parts) if parts else None

    def graph_count(self) -> int:
        self._ensure_graph_open()
        if self.use_zvec and self._graph is not None:
            try:
                return int(self._graph.stats.doc_count)
            except Exception:
                return 0
        return self._graph_fb.count() if self._graph_fb else 0

    def doc_count(self) -> int:
        self._ensure_docs_open()
        if self.use_zvec and self._docs is not None:
            try:
                return int(self._docs.stats.doc_count)
            except Exception:
                return 0
        return self._doc_fb.count() if self._doc_fb else 0
