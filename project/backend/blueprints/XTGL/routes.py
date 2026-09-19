import json                                  # JSON序列化
import logging                                 # 日志记录
import os                                     # 操作系统接口
from flask import Blueprint, request, jsonify  # Flask核心组件
from utils.operation_log import log_user_action

logger = logging.getLogger(__name__)             # 创建日志记录器
xtgl_bp = Blueprint('xtgl', __name__, url_prefix='/api/xtgl')  # 创建系统管理蓝图，API前缀/api/xtgl


MODEL_CONFIG_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'model_config.json')  # 模型配置文件路径

def _load_model_config():                     # 加载模型配置
    if os.path.exists(MODEL_CONFIG_FILE):    # 文件存在
        with open(MODEL_CONFIG_FILE, 'r', encoding='utf-8') as f:  # 读取文件
            return json.load(f)         # 解析JSON
    return {"llm": {"api_url": "", "model": "", "api_key": ""}, "embedding": {"model": "", "api_url": ""}, "vector_retrieval": {"model": "", "api_url": ""}}  # 返回默认配置

def _save_model_config(config):           # 保存模型配置
    with open(MODEL_CONFIG_FILE, 'w', encoding='utf-8') as f:  # 写入文件
        json.dump(config, f, ensure_ascii=False, indent=2)  # 序列化为JSON


@xtgl_bp.route('/models', methods=['GET'])  # 获取模型配置接口
def get_models():
    return jsonify({"success": True, "data": _load_model_config()})  # 返回配置


@xtgl_bp.route('/models', methods=['POST'])  # 更新模型配置接口
def update_models():
    data = request.get_json()              # 获取请求数据
    config = _load_model_config()      # 加载现有配置
    if 'llm' in data:                 # 更新LLM配置
        config['llm'] = data['llm']
    if 'embedding' in data:              # 更新embedding配置
        config['embedding'] = data['embedding']
    if 'vector_retrieval' in data:         # 更新向量检索配置
        config['vector_retrieval'] = data['vector_retrieval']
    _save_model_config(config)         # 保存配置
    log_user_action("系统管理-模型配置(model_config.json)已更新")
    return jsonify({"success": True, "message": "模型配置已更新"})  # 返回成功

