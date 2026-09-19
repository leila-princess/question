import os
import sys

# backend 目录须优先于 blueprints/FLGL（后者也有 services.py，会遮蔽本目录 services/ 包）
_BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_BACKEND_DIR)
for _p in (_PROJECT_ROOT, _BACKEND_DIR):
    if _p not in sys.path:
        sys.path.append(_p)
if _BACKEND_DIR in sys.path:
    sys.path.remove(_BACKEND_DIR)
sys.path.insert(0, _BACKEND_DIR)

# 必须在 chromadb 等库导入前关闭遥测（无网时 posthog.com 会刷 Connection/DNS 错误）
from bootstrap_offline import apply_offline_bootstrap

apply_offline_bootstrap()

import logging
import atexit
from flask import Flask
from config import config
from extensions import init_extensions
from utils.operation_log import init_operation_logging, log_action
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger(__name__)

def create_app(config_name='default'):
    app = Flask(__name__)
    app.config.from_object(config[config_name])
    config[config_name].init_app(app)
    init_operation_logging()
    init_extensions(app)
    register_blueprints(app)
    register_error_handlers(app)
    atexit.register(config[config_name].cleanup)
    logger.info(f"Flask应用已创建，使用 {config_name} 配置")
    return app

def register_blueprints(app):
    # from blueprints.chat import chat_bp
    # from blueprints.createfa import CC_bp
    # from blueprints.document_parser import parser_bp
    # from blueprints.experiment_scheme import experiment_bp
    # from blueprints.download import download_bp
    # from blueprints.pdf2kg import pdf2kg_bp
    # from blueprints.knowledge_graph import kg_bp
    from blueprints.ZSGL.ZSTQ import zstq_bp
    from blueprints.GLCX.ZNWD import znwd_bp
    from blueprints.GLCX.ZNWD1 import znwd1_bp
    from blueprints.GLCX.ZNWD2 import znwd2_bp
    from blueprints.GLCX.ZNWD3 import znwd3_bp
    from blueprints.GLCX.ZNWD4 import znwd4_bp
    from blueprints.GLCX.ZNWD5 import znwd5_bp
    from blueprints.GLCX.ZNWD6 import znwd6_bp
    from blueprints.GLCX.JQCX import jqcx_bp
    from blueprints.GLCX.SSYQ import ssyq_bp
    from blueprints.FLGL.routes import flgl_bp
    from blueprints.FLGL.nodes_routes import flgl_nodes_bp
    from blueprints.FLGL.upload_routes import flgl_upload_bp
    from blueprints.FLGL.method_routes import flgl_method_bp
    from blueprints.FLGL.GetTestMethod.word_llm_upload_extract import create_upload_blueprint
    from blueprints.Standard.routes import standard_bp
    from blueprints.FZSJ.JYLLM.routes import jyllm_bp
    from blueprints.FZSJ.JYLLM.history_routes import jyllm_history_bp
    from blueprints.FZSJ.JYLLM.pure_llm.routes import pure_llm_bp
    from blueprints.FZSJ.JYGZ.routes import jygz_bp
    from blueprints.SY.routes import sy_bp
    from blueprints.template_routes import template_bp
    from blueprints.ZSGL.WJGL.routes import wjgl_bp
    from blueprints.ZSGL.WJGL1.routes import wjgl1_bp
    from blueprints.ZSGL.WJGL.step_upload_routes import step_upload_bp
    from blueprints.ZSGL.XLSJK.routes import chroma_bp
    from blueprints.ZSGL.TPZS.TPCX import kg_bp
    from blueprints.KSHZS.routes import kshzs_bp
    from blueprints.XTGL.routes import xtgl_bp
    from blueprints.XTGL.YHGL.routes import yhgl_bp
    from blueprints.XTGL.SJKGL.routes import sjkgl_bp
    from blueprints.XTGL.MXGL.routes import mxgl_bp
    from blueprints.XTGL.WTGL.routes import wtgl_bp
    from blueprints.XTGL.RZGL.routes import rzgl_bp
    from blueprints.XTGL.SJBF.routes import register_sjbf_scheduler, sjbf_bp
    from blueprints.RelFact.routes import relfact_bp
    from blueprints.Login.routes import login_bp

    # app.register_blueprint(chat_bp)
    # app.register_blueprint(parser_bp)
    # app.register_blueprint(experiment_bp)
    # app.register_blueprint(download_bp)
    # app.register_blueprint(CC_bp)
    # app.register_blueprint(pdf2kg_bp)
    app.register_blueprint(kg_bp)
    app.register_blueprint(zstq_bp)
    app.register_blueprint(znwd_bp)
    app.register_blueprint(znwd1_bp)
    app.register_blueprint(znwd2_bp)
    app.register_blueprint(znwd3_bp)
    app.register_blueprint(znwd4_bp)
    app.register_blueprint(znwd5_bp)
    app.register_blueprint(znwd6_bp)
    app.register_blueprint(jqcx_bp)
    app.register_blueprint(ssyq_bp)
    app.register_blueprint(flgl_bp)
    app.register_blueprint(flgl_nodes_bp)
    app.register_blueprint(flgl_upload_bp)
    app.register_blueprint(flgl_method_bp)
    app.register_blueprint(create_upload_blueprint())
    app.register_blueprint(standard_bp)
    app.register_blueprint(jyllm_bp)
    app.register_blueprint(jyllm_history_bp)
    app.register_blueprint(pure_llm_bp)
    app.register_blueprint(jygz_bp)
    app.register_blueprint(sy_bp)
    app.register_blueprint(template_bp)
    app.register_blueprint(wjgl_bp)
    app.register_blueprint(wjgl1_bp)
    app.register_blueprint(relfact_bp)
    app.register_blueprint(step_upload_bp)
    app.register_blueprint(chroma_bp)
    app.register_blueprint(kshzs_bp)
    app.register_blueprint(xtgl_bp)
    app.register_blueprint(yhgl_bp)
    app.register_blueprint(sjkgl_bp)
    app.register_blueprint(mxgl_bp)
    app.register_blueprint(wtgl_bp)
    app.register_blueprint(rzgl_bp)
    app.register_blueprint(sjbf_bp)
    app.register_blueprint(login_bp)
    register_sjbf_scheduler(app)
    logger.info("所有蓝图已注册")

def register_error_handlers(app):
    @app.errorhandler(404)
    def not_found_error(error):
        return {"success": False, "error": "资源未找到", "code": 404}, 404

    @app.errorhandler(500)
    def internal_error(error):
        from flask import jsonify
        logger.error(f"服务器内部错误: {error}")
        try:
            log_action("system", "HTTP 500", level="ERROR", error=str(error))
        except Exception:
            pass
        return jsonify({"success": False, "error": "服务器内部错误", "code": 500}), 500

    @app.errorhandler(Exception)
    def unhandled_exception(error):
        """API 路径未捕获异常 → JSON（避免前端收到 Werkzeug HTML Internal Server Error）。"""
        from flask import jsonify, request
        from werkzeug.exceptions import HTTPException

        if isinstance(error, HTTPException):
            if error.code == 404:
                return not_found_error(error)
            if error.code == 413:
                return request_entity_too_large(error)
            if error.code == 500:
                return internal_error(error)
            if request.path.startswith("/api/"):
                return jsonify({
                    "success": False,
                    "error": getattr(error, "description", None) or str(error),
                    "code": error.code,
                }), error.code
            return error

        if request.path.startswith("/api/"):
            logger.exception("未捕获 API 异常 path=%s", request.path)
            try:
                log_action("system", "API 未捕获异常", level="ERROR", error=str(error))
            except Exception:
                pass
            return jsonify({"success": False, "error": str(error), "code": 500}), 500
        raise error

    @app.errorhandler(413)
    def request_entity_too_large(error):
        return {"success": False, "error": "文件太大，最大支持16MB", "code": 413}, 413

if __name__ == '__main__':
    app = create_app()
    app.run(debug=True, host='0.0.0.0', port=5000, use_reloader=False, threaded=True)
