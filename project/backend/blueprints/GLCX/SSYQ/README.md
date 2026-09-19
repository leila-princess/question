# SSYQ（语义搜索查询）后端说明文档

## 页面路径
`backend/blueprints/GLCX/SSYQ/routes.py`

## Flask Blueprint
```python
ssyq_bp = Blueprint('ssyq', __name__, url_prefix='/api/GLCX/SSYQ')
```

## API 接口

### 1. POST `/api/GLCX/SSYQ?path=/search` - 语义搜索
**请求体**：
```json
{ "keyword": "无人机" }
```

**返回结构**：
```json
{
  "success": true,
  "data": {
    "searchResults": {
      "equipments": [...],
      "projects": [...],
      "indicators": [...]
    },
    "tableRows": [...],
    "graphData": { "nodes": [...], "edges": [...] }
  }
}
```

**核心函数**：

#### `_search_keyword(keyword)` - 关键词搜索
- 搜索 `Equipment` 节点（Name 包含关键词）
- 搜索 `TestProject` 节点
- 搜索 `Indicator` 节点
- 返回三类节点的属性列表

#### `_get_equipment_data(eq_id)` - 获取设备数据
**Cypher 查询**：
```cypher
MATCH (eq:Equipment {Id: $eqId})-[:CON]->(test:TestProject {Type: 'test'})
OPTIONAL MATCH (cat:TestProject {Type: 'category'})-[:HAS]->(test)
OPTIONAL MATCH (env:Environment)-[:CON]->(test)
RETURN DISTINCT test, cat, env
ORDER BY cat.Name, test.Name
```

**返回结构**：
```python
{
    'equipment': {...},
    'projects': [category节点, ...],  # 带children和_env
    'projectsByEnv': {
        '复杂电磁环境': [category副本(含该环境下的children), ...],
        '复杂地理环境': [...],
        ...
    },
    'indicators': [...]
}
```

**去重逻辑**：
- 按 category 分组收集 children
- 每个 category 在每个环境中只出现一次
- 使用 `children_by_env` 字典按环境分组子项目，避免重复

#### `_get_project_data_with_traceback(proj_id)` - 获取项目数据及追溯
- 查询项目所属环境（直接查 Environment 节点获取 Name）
- 查询项目关联的指标
- 查询关联该项目的设备

#### `_get_indicator_data_with_traceback(ind_id)` - 获取指标数据及追溯
**Cypher 查询**：
```cypher
MATCH (test:TestProject {Type: 'test'})-[:CON]->(i:Indicator {Id: $indId})
OPTIONAL MATCH (cat:TestProject {Type: 'category'})-[:HAS]->(test)
OPTIONAL MATCH (env:Environment)-[:CON]->(test)
RETURN DISTINCT test, cat, env
```
- 与 `_get_equipment_data` 相同的 category/children 去重逻辑
- 查询关联该指标的设备

#### `_build_full_graph(search_results, table_rows)` - 构建完整图谱
**节点类型**：Equipment, TestProject, Environment, Indicator, TestMethod, File

**关系类型**：CON（关联）, HAS（包含）

**环境节点属性**：通过 `get_env_props()` 从数据库查询 Environment 节点的真实属性，不使用项目属性替代。

**方法节点**：查询所有项目/指标关联的方法及文件，缓存方法-文件关系避免重复。

### 2. POST `/api/GLCX/SSYQ?path=/methods` - 查询关联方法
**请求体**：
```json
{
  "projectIds": ["XM_0001"],
  "indicatorIds": ["ID_0001"]
}
```

**返回结构**：
```json
{
  "success": true,
  "data": [
    {
      "id": "FF_0001",
      "name": "方法名称",
      "_relationType": "both",  // both | project-only | indicator-only
      "files": [...]
    }
  ]
}
```

**关系类型判断**：
- `both`: 方法同时关联选中的项目和指标
- `project-only`: 方法只关联选中的项目
- `indicator-only`: 方法只关联选中的指标

## 数据模型（Neo4j）
```
EquipmentCategory → HAS → EquipmentType → HAS → Equipment
Equipment → CON → TestProject {Type: 'test'}
TestProject {Type: 'category'} → HAS → TestProject {Type: 'test'}
Environment → CON → TestProject {Type: 'test'}
TestProject → CON → Indicator
TestProject → CON → TestMethod
Indicator → CON → TestMethod
TestMethod → CON → File
```

## 辅助函数
| 函数 | 说明 |
|------|------|
| `_load_standard_mapping()` | 加载 standard_mapping.json 文件 |
| `_get_source_name(source_id)` | 根据 source_id 获取源文件名称 |
| `_run_cypher(cypher, params)` | 执行 Cypher 查询，返回字典列表 |
| `_serialize_value(v)` | 序列化值（处理 datetime 类型） |
| `_get_node_props(node_data)` | 提取节点属性，过滤 `_` 开头的键 |

## 注意事项
1. **项目查询必须过滤 `Type: 'test'`**，避免查出 category 节点
2. **环境名称直接从 Environment 节点获取**，不使用 ID 映射
3. **projectsByEnv 去重**：每个 category 在每个环境中只出现一次，children 按环境分组
4. **图谱构建时**：环境节点属性必须从数据库查询，不能传入项目属性
