"""
GetTestMethod 子模块使用独立命名空间 `backend.config`，与主工程 backend 包解耦。
运行时在 method_routes.method_extract 中从主工程 `backend.config.Config` 注入模型 / 数据库等。
环境变量在仓库根 .env 中配置，主 Config 中 FLGL_TESTMETHOD_* 为权威来源。
"""

class Config:
    OPENAI_CLIENT = None
    OPENAI_MODEL = None
    NEO4J_DRIVER = None
    CHROMA_CLIENT = None
    CHROMA_COLLECTION_OBJ = None
    MINIO_CLIENT = None
    BGE_MODEL_PATH = None
    BGE_LOCAL_FILES_ONLY = None
    TESTMETHOD_RETRIEVER = None
    # 由 method_routes 从主 Config 注入（见 FLGL_TESTMETHOD_*）
    LLM_MAX_COMPLETION_TOKENS = None
    LLM_CONTEXT_TOKENS = None
    DISCOVERY_MAX_PROMPT_CHARS = None
    DISCOVERY_CATALOGUE_MAX_CHARS = None
    DISCOVERY_PREVIEW_MAX_CHARS = None
    DETAIL_MAX_PROMPT_CHARS = None
    DETAIL_SECTION_MAX_CHARS = None
    DETAIL_CHUNK_BUDGET_CHARS = None
    DETAIL_TABLE_HTML_MAX_CHARS = None
    DETAIL_MAX_TABLES = None
