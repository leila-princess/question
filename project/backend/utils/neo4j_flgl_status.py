"""
图数据库 FLGL 类节点与 Status：启用在库中可能为中文「启用」或英文 active（与前端/接口一致）。

凡「列表、搜索、联查」等只读图查询，应对这些带 Status 的节点增加启用过滤。
分类管理使用 `/api/FLGL/nodes/*` 与 `/api/FLGL/newnodes/*` 的接口：列表、schema、
可关联、关联边展示等**不过滤** Status，以便看到停用节点；本模块用于其余业务与检索接口。
（按 Id 更新/删、自增号生成、全库清空等不由此模块过滤。）
"""
from typing import Dict, List, Tuple, Any

# 与 FLGL NODE_LABELS 值一致
FLGL_GRAPH_LABELS: Tuple[str, ...] = (
    'EquipmentType', 'Equipment', 'Environment', 'TestProject', 'Indicator', 'TestMethod', 'File',
)

# 与「启用」等价的存储值
FLGL_ENABLED_STATUS: Tuple[str, ...] = ('启用', 'active')


def flgl_status_cypher_param() -> Dict[str, List[str]]:
    """与 Cypher 中 $flgl_enabled_status 配合；调用方在 params 中解包本字典。"""
    return {'flgl_enabled_status': list(FLGL_ENABLED_STATUS)}


def flgl_neighbor_filter_params() -> Dict[str, Any]:
    """与「关联图」中邻居节点 m 的过滤一起使用，含 Cypher: ... WHERE (NOT any(...) ) OR m.Status IN $flgl_enabled_status"""
    return {
        'flgl_enabled_status': list(FLGL_ENABLED_STATUS),
        'flgl_typed_label_list': list(FLGL_GRAPH_LABELS),
    }
