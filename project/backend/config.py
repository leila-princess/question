import os
import sys
import logging
from datetime import timedelta
from dotenv import load_dotenv

os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
# Chroma 默认会向 posthog.com 上报遥测；无网环境会刷 DNS 失败日志
os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")
os.environ.setdefault("CHROMA_TELEMETRY", "FALSE")

# 项目根目录（config.py 位于 backend/）
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)
from bootstrap_offline import apply_offline_bootstrap

apply_offline_bootstrap()
# 先加载仓库根目录 .env，再加载当前工作目录 .env（补缺，且不强行覆盖已存在的键）
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
load_dotenv()
# JYLLM/ZNWD：默认关闭超长 prompt 自动压缩，避免本地 vLLM 摘要流为空导致正文不生成
os.environ.setdefault("LLM_SKIP_CONTEXT_COMPRESS", "1")
# WJGL PDF 解析：MinerU 默认装在 agent conda 环境
os.environ.setdefault("MINERU_ENV", "agent")
os.environ.setdefault("WJGL_PDF_PARSE", "mineru")
# parse_standard_file / 预览接口与上传任务共用同一后端开关
os.environ.setdefault("FLGL_PDF_BACKEND", os.environ.get("WJGL_PDF_PARSE", "mineru"))
_mineru_json = os.path.join(PROJECT_ROOT, "mineru.json")
if os.path.isfile(_mineru_json):
    os.environ.setdefault("MINERU_TOOLS_CONFIG_JSON", _mineru_json)

logger = logging.getLogger(__name__)

try:
    from pdf_extract.mineru_adapter import resolve_mineru_api_url

    _mineru_api = resolve_mineru_api_url(force_probe=False)
    if _mineru_api:
        logger.info("MinerU API configured: %s", _mineru_api)
except Exception as _me:
    logger.debug("MinerU API probe skipped at import: %s", _me)


class Config:
    """基础配置 — 所有系统配置集中管理"""

    SECRET_KEY = os.environ.get('SECRET_KEY') or 'your-secret-key-here'

    # ===== Neo4j =====
    NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://192.168.15.123:7687")
    NEO4J_USER = os.environ.get("NEO4J_USER", "neo4j")
    NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD", "CHANGE_ME")

    # ===== LLM (OpenAI 兼容协议) =====
    # 云端：填真实 OPENAI_API_KEY；本地 vLLM/Ollama 等无鉴权：见 resolve_openai_client_kwargs
    OPENAI_API_KEY = (os.environ.get("OPENAI_API_KEY") or "").strip()
    OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "qwen-plus")
    # 本地推理无 key 时设为 1，或直接在 .env 写 OPENAI_API_KEY=EMPTY
    OPENAI_ALLOW_EMPTY_KEY = os.environ.get("OPENAI_ALLOW_EMPTY_KEY", "").strip().lower() in (
        "1", "true", "yes", "on",
    )

    # ===== MySQL =====
    DB_HOST = os.environ.get("DB_HOST", "localhost")
    DB_PORT = int(os.environ.get("DB_PORT", "3306"))
    DB_USER = os.environ.get("DB_USER", "root")
    DB_PASSWORD = os.environ.get("DB_PASSWORD", "")
    DB_NAME = os.environ.get("DB_NAME", "znwd_history")

    # ===== Redis =====
    REDIS_HOST = os.environ.get("REDIS_HOST", "localhost")
    REDIS_PORT = int(os.environ.get("REDIS_PORT", "6379"))
    REDIS_DB = int(os.environ.get("REDIS_DB", "0"))
    REDIS_PASSWORD = os.environ.get("REDIS_PASSWORD", "")
    REDIS_MEMORY_TTL = int(os.environ.get("REDIS_MEMORY_TTL", "1800"))

    # ===== v4 Token Limits =====
    MAX_CONTEXT_TOKENS = int(os.environ.get("MAX_CONTEXT_TOKENS", "4000"))

    # ===== Chroma =====
    CHROMA_PATH = os.environ.get("CHROMA_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "chroma_data"))
    CHROMA_COLLECTION = os.environ.get("CHROMA_COLLECTION", "drone_standards")

    # ===== 本地 Embedding 模型路径 =====
    LOCAL_EMBEDDING_MODEL_PATH = os.environ.get(
        "LOCAL_EMBEDDING_MODEL_PATH",
        r"C:\Users\hyx\antd-demo1\ANZHUANGBAO\models--sentence-transformers--all-MiniLM-L6-v2\snapshots\c9745ed1d9f207416be6d2e6f8de32d1f16199bf"
    )
    LOCAL_MINILM_MODEL_PATH = os.environ.get(
        "LOCAL_MINILM_MODEL_PATH",
        r"C:\Users\hyx\antd-demo1\ANZHUANGBAO\models--sentence-transformers--paraphrase-multilingual-MiniLM-L12-v2\snapshots\e8f8c211226b894fcb81acc59f3b34ba3efd5f42"
    )
    LOCAL_CROSSENCODER_MODEL_PATH = os.environ.get(
        "LOCAL_CROSSENCODER_MODEL_PATH",
        os.path.join(PROJECT_ROOT, "ANZHUANGBAO", "ms-marco-MiniLM-L6-v2")
    )

    # ===== MinIO =====
    MINIO_ENDPOINT = os.environ.get("MINIO_ENDPOINT", "localhost:9000")
    MINIO_ACCESS_KEY = os.environ.get("MINIO_ACCESS_KEY", "hyx123")
    MINIO_SECRET_KEY = os.environ.get("MINIO_SECRET_KEY", "CHANGE_ME")
    MINIO_SECURE = os.environ.get("MINIO_SECURE", "false").lower() == "true"
    MINIO_BUCKET_NAME = os.environ.get("MINIO_BUCKET_NAME", "faxm")

    # ===== 数据全量备份（SJBF：/data/backup/YYYY-MM-DD/，可用环境变量 BACKUP_ROOT 覆盖）=====
    BACKUP_ROOT = os.environ.get("BACKUP_ROOT", "/data/backup")
    BACKUP_RETENTION_DAYS = int(os.environ.get("BACKUP_RETENTION_DAYS", "7"))

    # ===== Office 转 PDF（文件管理 Word/Excel/PPT 预览，非 react-doc-viewer 依赖）=====
    LIBREOFFICE_PATH = (os.environ.get("LIBREOFFICE_PATH") or "").strip()
    MAGICK_PATH = (os.environ.get("MAGICK_PATH") or os.environ.get("MAGICK_HOME") or "").strip()

    # ===== 文件路径 =====
    OUTPUT_DIR = os.environ.get("OUTPUT_DIR", os.path.join(os.path.dirname(os.path.dirname(__file__)), "output"))
    SCHEME_DIR = os.environ.get("SCHEME_DIR", os.path.join(os.path.dirname(os.path.dirname(__file__)), "schemes"))
    ANZHUANGBAO_DIR = os.environ.get("ANZHUANGBAO_DIR", r"C:\Users\hyx\antd-demo1\ANZHUANGBAO")

    # ===== FLGL 方法提取 / 图谱召回（GetTestMethod：解析、LLM 提取、BGE 推荐）=====
    # 本地 SentenceTransformer 模型目录（如 ANZHUANGBAO/bge-small-zh-v1.5，含 config.json / pytorch 等）
    FLGL_TESTMETHOD_BGE_MODEL_PATH = os.environ.get(
        "FLGL_TESTMETHOD_BGE_MODEL_PATH",
        os.path.join(PROJECT_ROOT, "ANZHUANGBAO", "bge-small-zh-v1.5"),
    )
    # 仅使用本地文件，不触网拉模型（与 HF 离线环境一致）
    FLGL_TESTMETHOD_BGE_LOCAL_FILES_ONLY = os.environ.get("FLGL_TESTMETHOD_BGE_LOCAL_FILES_ONLY", "1").lower() in (
        "1", "true", "yes",
    )
    # 方法抽取/召回用 LLM：空则与全局 OPENAI_MODEL 相同
    FLGL_TESTMETHOD_OPENAI_MODEL = (os.environ.get("FLGL_TESTMETHOD_OPENAI_MODEL") or "").strip() or None
    # 各类节点召回 TopK（0 表示不跑该类；推荐行仍可在后端截断）
    FLGL_TESTMETHOD_RECALL_TOPK_EQUIPMENT = int(os.environ.get("FLGL_TESTMETHOD_RECALL_TOPK_EQUIPMENT", "0"))
    FLGL_TESTMETHOD_RECALL_TOPK_ENVIRONMENT = int(os.environ.get("FLGL_TESTMETHOD_RECALL_TOPK_ENVIRONMENT", "0"))
    FLGL_TESTMETHOD_RECALL_TOPK_TEST_PROJECT = int(os.environ.get("FLGL_TESTMETHOD_RECALL_TOPK_TEST_PROJECT", "3"))
    FLGL_TESTMETHOD_RECALL_TOPK_INDICATOR = int(os.environ.get("FLGL_TESTMETHOD_RECALL_TOPK_INDICATOR", "3"))
    # pipeline 中临时方法 id 前缀（与 Neo4j FF_ 系一致）
    FLGL_TESTMETHOD_ID_PREFIX = (os.environ.get("FLGL_TESTMETHOD_ID_PREFIX") or "FF").strip() or "FF"
    # 若设置则方法提取/召回使用独立 Neo4j；否则与主 NEO4J_URI 相同
    FLGL_TESTMETHOD_NEO4J_URI = (os.environ.get("FLGL_TESTMETHOD_NEO4J_URI") or "").strip()
    FLGL_TESTMETHOD_NEO4J_USER = (os.environ.get("FLGL_TESTMETHOD_NEO4J_USER") or "").strip()
    FLGL_TESTMETHOD_NEO4J_PASSWORD = (os.environ.get("FLGL_TESTMETHOD_NEO4J_PASSWORD") or "").strip()
    # GetTestMethod LLM：控制单次请求上下文，避免超过模型上限；过长时 extractor 按章节分包请求再合并
    FLGL_TESTMETHOD_LLM_MAX_COMPLETION_TOKENS = int(
        os.environ.get("FLGL_TESTMETHOD_LLM_MAX_COMPLETION_TOKENS", "3072")
    )
    # 方法抽取所用模型的「总上下文」上限（用于压缩 max_tokens，避免 input+output 超限）
    FLGL_TESTMETHOD_LLM_CONTEXT_TOKENS = int(
        os.environ.get("FLGL_TESTMETHOD_LLM_CONTEXT_TOKENS", "8192")
    )
    FLGL_TESTMETHOD_DISCOVERY_MAX_PROMPT_CHARS = int(
        os.environ.get("FLGL_TESTMETHOD_DISCOVERY_MAX_PROMPT_CHARS", "11000")
    )
    FLGL_TESTMETHOD_DISCOVERY_CATALOGUE_MAX_CHARS = int(
        os.environ.get("FLGL_TESTMETHOD_DISCOVERY_CATALOGUE_MAX_CHARS", "5000")
    )
    FLGL_TESTMETHOD_DISCOVERY_PREVIEW_MAX_CHARS = int(
        os.environ.get("FLGL_TESTMETHOD_DISCOVERY_PREVIEW_MAX_CHARS", "6000")
    )
    # 细粒度：按「整段 prompt 字符数」分包，避免仅按章节 JSON 估算导致表格撑爆上下文
    FLGL_TESTMETHOD_DETAIL_MAX_PROMPT_CHARS = int(
        os.environ.get("FLGL_TESTMETHOD_DETAIL_MAX_PROMPT_CHARS", "10000")
    )
    FLGL_TESTMETHOD_DETAIL_SECTION_MAX_CHARS = int(
        os.environ.get("FLGL_TESTMETHOD_DETAIL_SECTION_MAX_CHARS", "2000")
    )
    FLGL_TESTMETHOD_DETAIL_CHUNK_BUDGET_CHARS = int(
        os.environ.get("FLGL_TESTMETHOD_DETAIL_CHUNK_BUDGET_CHARS", "8000")
    )
    FLGL_TESTMETHOD_DETAIL_TABLE_HTML_MAX_CHARS = int(
        os.environ.get("FLGL_TESTMETHOD_DETAIL_TABLE_HTML_MAX_CHARS", "1200")
    )
    FLGL_TESTMETHOD_DETAIL_MAX_TABLES = int(os.environ.get("FLGL_TESTMETHOD_DETAIL_MAX_TABLES", "40"))

    # ===== 会话 =====
    SESSION_TYPE = 'filesystem'
    SESSION_PERMANENT = False
    SESSION_USE_SIGNER = True
    SESSION_COOKIE_SECURE = False
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'

    # ===== 上传 =====
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024
    UPLOAD_EXTENSIONS = ['.docx']

    # ===== 运行时对象（由 init_app 初始化） =====
    NEO4J_DRIVER = None
    FLGL_TESTMETHOD_NEO4J_DRIVER = None
    # FLGL 方法召回用：启动时从 ANZHUANGBAO/bge-small-zh-v1.5 预加载，缺模型或失败则 process 不启动
    FLGL_TESTMETHOD_BGE_MODEL = None
    OPENAI_CLIENT = None
    CHROMA_COLLECTION_OBJ = None
    CHROMA_CLIENT = None
    MINIO_CLIENT = None
    MYSQL_POOL = None
    REDIS_CLIENT = None

    @staticmethod
    def is_local_openai_base_url(url: str = "") -> bool:
        """内网/本机 OpenAI 兼容端点通常无需 API Key。"""
        import re
        from urllib.parse import urlparse

        raw = (url or Config.OPENAI_BASE_URL or "").strip()
        if not raw:
            return False
        try:
            host = (urlparse(raw).hostname or "").lower()
        except Exception:
            host = raw.lower()
        if host in ("127.0.0.1", "localhost", "0.0.0.0", "::1"):
            return True
        if re.match(r"^(192\.168\.|10\.|172\.(1[6-9]|2\d|3[01])\.)", host):
            return True
        return False

    @staticmethod
    def effective_openai_api_key() -> str:
        """
        实际用于 OpenAI SDK 的 api_key。
        本地 vLLM 等无鉴权：.env 设 OPENAI_ALLOW_EMPTY_KEY=1，或 OPENAI_API_KEY=EMPTY；
        若 BASE_URL 指向本机/内网且 Key 为空，也自动使用 EMPTY。
        """
        key = (Config.OPENAI_API_KEY or "").strip()
        if key and key.upper() != "EMPTY":
            return key
        if key.upper() == "EMPTY" or Config.OPENAI_ALLOW_EMPTY_KEY:
            return "EMPTY"
        if Config.is_local_openai_base_url():
            return "EMPTY"
        return ""

    @staticmethod
    def create_openai_client():
        """创建 OpenAI 兼容客户端；无可用 key 时返回 None。"""
        import httpx
        from openai import OpenAI

        api_key = Config.effective_openai_api_key()
        if not api_key:
            return None
        base_url = (Config.OPENAI_BASE_URL or "").strip() or None
        trust_env = os.environ.get("OPENAI_HTTP_TRUST_ENV", "").strip().lower() in (
            "1", "true", "yes", "on",
        )
        # Windows 系统代理常导致内网 vLLM（192.168.x.x）ConnectError；本地端点默认不走代理
        if Config.is_local_openai_base_url(base_url or ""):
            trust_env = False
        timeout_sec = float(os.environ.get("OPENAI_HTTP_TIMEOUT", "180") or "180")
        http_client = httpx.Client(trust_env=trust_env, timeout=timeout_sec)
        kwargs = {
            "api_key": api_key,
            "base_url": base_url,
            "http_client": http_client,
        }
        if api_key == "EMPTY":
            # 与 ZNWD 一致：避免 SDK 发送无效 Bearer 导致本地网关 401
            kwargs["default_headers"] = {"Authorization": ""}
        return OpenAI(**kwargs)

    @staticmethod
    def init_app(app):
        from neo4j import GraphDatabase

        os.makedirs(Config.OUTPUT_DIR, exist_ok=True)
        os.makedirs(Config.SCHEME_DIR, exist_ok=True)

        # 优先预加载 bge-small-zh-v1.5，失败则直接抛错，避免首请求才暴露模型问题
        bge_path = os.path.abspath(Config.FLGL_TESTMETHOD_BGE_MODEL_PATH)
        if not os.path.isdir(bge_path):
            raise FileNotFoundError(
                f"FLGL BGE 模型目录不存在: {bge_path!r}。请将 bge-small-zh-v1.5 放在 "
                f"项目下 ANZHUANGBAO/bge-small-zh-v1.5 或设置环境变量 FLGL_TESTMETHOD_BGE_MODEL_PATH"
            )
        from blueprints.GLCX.ZNWD5.embedding_service import embedding_backend, get_embedding_model

        st = get_embedding_model()
        Config.FLGL_TESTMETHOD_BGE_MODEL = st
        app.config["FLGL_TESTMETHOD_BGE_MODEL"] = st
        logger.info("已预加载 BGE 模型: %s backend=%s", bge_path, embedding_backend())

        # Neo4j driver
        try:
            driver = GraphDatabase.driver(
                Config.NEO4J_URI,
                auth=(Config.NEO4J_USER, Config.NEO4J_PASSWORD)
            )
            with driver.session() as session:
                session.run("RETURN 1")
            Config.NEO4J_DRIVER = driver
            app.config['NEO4J_DRIVER'] = driver
            logger.info("Neo4j 连接成功")
        except Exception as e:
            logger.error(f"Neo4j 连接失败: {e}")
            Config.NEO4J_DRIVER = None

        # FLGL 方法提取专用 Neo4j（未配置时与上面对象共用，不在此重复创建）
        try:
            if Config.FLGL_TESTMETHOD_NEO4J_URI:
                flgl_user = Config.FLGL_TESTMETHOD_NEO4J_USER or Config.NEO4J_USER
                flgl_pwd = Config.FLGL_TESTMETHOD_NEO4J_PASSWORD or Config.NEO4J_PASSWORD
                flgl_driver = GraphDatabase.driver(
                    Config.FLGL_TESTMETHOD_NEO4J_URI,
                    auth=(flgl_user, flgl_pwd),
                )
                with flgl_driver.session() as s:
                    s.run("RETURN 1")
                Config.FLGL_TESTMETHOD_NEO4J_DRIVER = flgl_driver
                app.config['FLGL_TESTMETHOD_NEO4J_DRIVER'] = flgl_driver
                logger.info(f"FLGL 方法提取 Neo4j 连接成功: {Config.FLGL_TESTMETHOD_NEO4J_URI}")
            else:
                Config.FLGL_TESTMETHOD_NEO4J_DRIVER = None
                app.config['FLGL_TESTMETHOD_NEO4J_DRIVER'] = None
        except Exception as e:
            logger.warning(f"FLGL 专用 Neo4j 未启用或连接失败，方法提取将回退到主 NEO4J: {e}")
            Config.FLGL_TESTMETHOD_NEO4J_DRIVER = None
            app.config['FLGL_TESTMETHOD_NEO4J_DRIVER'] = None

        # OpenAI client（文件管理自动解析、智能问答等依赖此项）
        try:
            client = Config.create_openai_client()
            if client is None:
                logger.warning(
                    "未配置 LLM：OPENAI_CLIENT=None。"
                    "云端请设置 OPENAI_API_KEY；本地 vLLM 无 key 请设置 "
                    "OPENAI_ALLOW_EMPTY_KEY=1 且 OPENAI_BASE_URL 指向本地服务，或 OPENAI_API_KEY=EMPTY。"
                )
            else:
                Config.OPENAI_CLIENT = client
                app.config["OPENAI_CLIENT"] = client
                eff_key = Config.effective_openai_api_key()
                logger.info(
                    "OpenAI 客户端初始化成功 (base_url=%s, model=%s, local_no_auth=%s)",
                    Config.OPENAI_BASE_URL,
                    Config.OPENAI_MODEL,
                    eff_key == "EMPTY",
                )
            if Config.MYSQL_POOL:
                try:
                    from blueprints.XTGL.MXGL.routes import get_llm_models

                    for item in get_llm_models(active_only=True)[:3]:
                        logger.info(
                            "MXGL LLM 配置: key=%s model=%s api_url=%s source=%s",
                            item.get("model_key"),
                            item.get("model_name"),
                            item.get("api_url"),
                            item.get("source"),
                        )
                except Exception as mx_e:
                    logger.warning("读取 MXGL LLM 列表失败: %s", mx_e)
        except Exception as e:
            logger.error("OpenAI 初始化失败: %s", e)
            Config.OPENAI_CLIENT = None
            app.config["OPENAI_CLIENT"] = None

        # Chroma collection
        try:
            import chromadb
            from chromadb.config import Settings as ChromaSettings
            from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

            chroma_path = os.path.abspath(Config.CHROMA_PATH)
            print("chroma数据库路径", chroma_path)
            chroma_client = chromadb.PersistentClient(
                path=chroma_path,
                settings=ChromaSettings(anonymized_telemetry=False),
            )
            Config.CHROMA_CLIENT = chroma_client
            app.config['CHROMA_CLIENT'] = chroma_client

            # 使用本地模型路径初始化 embedding function
            embed_model_path = os.path.abspath(Config.LOCAL_EMBEDDING_MODEL_PATH)
            if os.path.exists(embed_model_path):
                logger.info(f"使用本地 Embedding 模型: {embed_model_path}")
                ef = SentenceTransformerEmbeddingFunction(model_name=embed_model_path,local_files_only=True,)
            else:
                logger.warning(f"本地 Embedding 模型路径不存在: {embed_model_path}，使用默认")
                ef = None

            collection_name = Config.CHROMA_COLLECTION
            available = [c.name for c in chroma_client.list_collections()]
            
            if collection_name not in available:
                logger.info(f"集合 {collection_name} 不存在，创建新集合")
                if ef:
                    Config.CHROMA_COLLECTION_OBJ = chroma_client.create_collection(
                        name=collection_name, 
                        embedding_function=ef
                    )
                else:
                    Config.CHROMA_COLLECTION_OBJ = chroma_client.create_collection(
                        name=collection_name
                    )
            else:
                if ef:
                    Config.CHROMA_COLLECTION_OBJ = chroma_client.get_collection(name=collection_name, embedding_function=ef)
                else:
                    Config.CHROMA_COLLECTION_OBJ = chroma_client.get_collection(name=collection_name)
            
            app.config['CHROMA_COLLECTION_OBJ'] = Config.CHROMA_COLLECTION_OBJ
            logger.info(f"Chroma 集合 [{collection_name}] 加载成功，共 {Config.CHROMA_COLLECTION_OBJ.count()} 条")
        except Exception as e:
            logger.warning(f"Chroma 初始化失败: {e}")
            Config.CHROMA_COLLECTION_OBJ = None
            Config.CHROMA_CLIENT = None

        # MinIO client
        try:
            from minio import Minio
            minio_client = Minio(
                Config.MINIO_ENDPOINT,
                access_key=Config.MINIO_ACCESS_KEY,
                secret_key=Config.MINIO_SECRET_KEY,
                secure=Config.MINIO_SECURE,
            )
            Config.MINIO_CLIENT = minio_client
            app.config['MINIO_CLIENT'] = minio_client
            if not minio_client.bucket_exists(Config.MINIO_BUCKET_NAME):
                minio_client.make_bucket(Config.MINIO_BUCKET_NAME)
                logger.info(f"MinIO Bucket [{Config.MINIO_BUCKET_NAME}] 已创建")
            else:
                logger.info(f"MinIO Bucket [{Config.MINIO_BUCKET_NAME}] 已存在")
        except Exception as e:
            logger.warning(f"MinIO 初始化失败: {e}")
            Config.MINIO_CLIENT = None

        try:
            from dbutils.pooled_db import PooledDB
            import pymysql
            Config.MYSQL_POOL = PooledDB(
                creator=pymysql,
                maxconnections=10,
                mincached=2,
                maxcached=5,
                blocking=True,
                host=Config.DB_HOST,
                port=Config.DB_PORT,
                user=Config.DB_USER,
                password=Config.DB_PASSWORD,
                database=Config.DB_NAME,
                charset='utf8mb4'
            )
            with Config.MYSQL_POOL.connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("SELECT 1")
            logger.info("MySQL 连接池初始化成功")
        except Exception as e:
            logger.warning(f"MySQL 初始化失败: {e}")
            Config.MYSQL_POOL = None

        try:
            import redis
            redis_kwargs = {}
            if Config.REDIS_PASSWORD:
                redis_kwargs['password'] = Config.REDIS_PASSWORD
            redis_client = redis.Redis(
                host=Config.REDIS_HOST,
                port=Config.REDIS_PORT,
                db=Config.REDIS_DB,
                decode_responses=True,
                **redis_kwargs
            )
            redis_client.ping()
            try:
                redis_client.config_set('stop-writes-on-bgsave-error', 'no')
            except Exception as e:
                logger.warning(f"Redis stop-writes-on-bgsave-error 设置失败: {e}, 将尝试 ignore-save-errors")
                try:
                    redis_client.config_set('save', '')
                except Exception:
                    pass
            Config.REDIS_CLIENT = redis_client
            app.config['REDIS_CLIENT'] = redis_client
            logger.info(f"Redis 连接成功 (db={Config.REDIS_DB})")
        except ImportError:
            logger.warning("redis 包未安装，记忆功能将不可用。pip install redis")
            Config.REDIS_CLIENT = None
        except Exception as e:
            logger.warning(f"Redis 初始化失败: {e}")
            Config.REDIS_CLIENT = None

    @staticmethod
    def cleanup():
        fd = getattr(Config, "FLGL_TESTMETHOD_NEO4J_DRIVER", None)
        if fd and fd is not getattr(Config, "NEO4J_DRIVER", None):
            try:
                fd.close()
                logger.info("FLGL 方法提取 Neo4j 驱动已关闭")
            except Exception as e:
                logger.warning(f"FLGL 方法提取 Neo4j 关闭: {e}")
        if Config.NEO4J_DRIVER:
            Config.NEO4J_DRIVER.close()
            logger.info("Neo4j 驱动已关闭")


class DevelopmentConfig(Config):
    DEBUG = True


class ProductionConfig(Config):
    DEBUG = False
    SESSION_COOKIE_SECURE = True


class TestingConfig(Config):
    TESTING = True
    NEO4J_URI = os.environ.get("TEST_NEO4J_URI", "bolt://localhost:7687")


config = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'testing': TestingConfig,
    'default': DevelopmentConfig
}
