"""
单元测试：第2章标题树结构校验与 Writer 标签解析。
运行：在 backend 目录下执行
  python -m pytest blueprints/FZSJ/JYLLM/test_ch2_structure_validate.py -q
"""
import os
import sys

# 保证从仓库 backend 根目录可导入 blueprints.*
_BACKEND_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)

from blueprints.FZSJ.JYLLM.multi_agent_engine import (  # noqa: E402
    _validate_ch2_structure,
    parse_writer_tag_blocks,
)
from blueprints.FZSJ.JYLLM.project_tree_utils import rebuild_indicator_forest, rebuild_project_forest  # noqa: E402
from blueprints.FZSJ.JYLLM.skeleton_builder import build_method_tree_and_skeleton  # noqa: E402


def test_validate_ch2_parent_child_match():
    md = """## 2
### 2.1
#### 2.1.1 低温试验室
##### 2.1.1.1 环境适应性
###### 2.1.1.1.1 低温启动
"""
    projs = rebuild_project_forest([
        {"id": "c1", "name": "环境适应性", "type": "category", "children": [
            {"id": "t1", "name": "低温启动", "type": "test"},
        ]},
    ])
    ok, errs = _validate_ch2_structure(md, projs)
    assert ok is True
    assert errs == []


def test_validate_ch2_count_mismatch():
    md = """#### 2.1.1 环境X
##### 2.1.1.1 仅一项
"""
    ok, errs = _validate_ch2_structure(md, [
        {"id": "c1", "name": "A", "type": "category", "children": [{"id": "t1", "name": "B", "type": "test"}]},
        {"id": "t2", "name": "C", "type": "test"},
    ])
    assert ok is False
    assert any("数量" in e for e in errs)


def test_validate_ch2_title_mismatch():
    md = """#### 2.1.1 环境X
##### 2.1.1.1 错误名
"""
    ok, errs = _validate_ch2_structure(md, [{"id": "t1", "name": "正确名", "type": "test"}])
    assert ok is False
    assert any("正确名" in e for e in errs)


def test_validate_ch2_forbidden_env():
    md = """#### 2.1.1 地理环境
##### 2.1.1.1 项目一
"""
    ok, errs = _validate_ch2_structure(md, [{"name": "项目一", "id": "p1"}])
    assert ok is False
    assert any("地理环境" in e for e in errs)


def test_validate_ch2_rejects_method_heading():
    md = """#### 2.1.1 高温
##### 2.1.1.1 项目一
###### 2.1.1.1.1 试验方法
"""
    ok, errs = _validate_ch2_structure(md, [{"name": "项目一", "id": "p1"}])
    assert ok is False
    assert any("试验方法" in e for e in errs)


def test_validate_ch2_empty_projects_skips():
    ok, errs = _validate_ch2_structure("任意正文", [])
    assert ok is True
    assert errs == []


def test_rebuild_project_forest_child_only_with_category_id():
    projs = rebuild_project_forest([
        {"id": "t1", "name": "低温启动", "type": "test", "categoryId": "c1"},
        {"id": "c1", "name": "环境适应性", "type": "category"},
    ])
    assert len(projs) == 1
    assert projs[0]["id"] == "c1"
    assert projs[0]["children"][0]["id"] == "t1"


def test_rebuild_project_forest_child_only_parent_stub():
    projs = rebuild_project_forest([
        {"id": "t1", "name": "低温启动", "type": "test", "categoryId": "c1"},
    ])
    assert len(projs) == 1
    assert projs[0]["id"] == "c1"
    assert projs[0]["children"][0]["name"] == "低温启动"


def test_rebuild_indicator_forest_child_only_with_category_id():
    inds = rebuild_indicator_forest([
        {"id": "t1", "name": "子指标", "type": "test", "categoryId": "c1"},
        {"id": "c1", "name": "父指标", "type": "category"},
    ])
    assert len(inds) == 1
    assert inds[0]["id"] == "c1"
    assert inds[0]["children"][0]["id"] == "t1"


def test_emit_per_env_split_parent_and_child():
    """预分组 environments[i].projects：每环境节下 ##### 父 + ###### 子。"""
    payload = build_method_tree_and_skeleton(
        equipment=[],
        environments=[
            {
                "name": "高温试验室",
                "projects": [
                    {
                        "id": "c1",
                        "name": "环境适应性",
                        "type": "category",
                        "children": [{"id": "t1", "name": "高温工作", "type": "test"}],
                    }
                ],
            },
            {
                "name": "低温试验室",
                "projects": [
                    {
                        "id": "c1",
                        "name": "环境适应性",
                        "type": "category",
                        "children": [{"id": "t2", "name": "低温启动", "type": "test"}],
                    }
                ],
            },
        ],
        projects=[],
        indicators=[],
        methods=[],
    )
    disp = payload.get("skeleton_markdown_display") or ""
    assert "#### 2.1.1 高温试验室" in disp
    assert "##### 2.1.1.1 环境适应性" in disp
    assert "###### 2.1.1.1.1 高温工作" in disp
    assert "#### 2.1.2 低温试验室" in disp
    assert "##### 2.1.2.1 环境适应性" in disp
    assert "###### 2.1.2.1.1 低温启动" in disp


def test_legacy_flat_projects_child_emitted_under_parent():
    """旧 payload 回退：父 env 与子 env 不一致时，递归仍输出子项目标题。"""
    payload = build_method_tree_and_skeleton(
        equipment=[],
        environments=[{"name": "高温试验室"}],
        projects=[
            {
                "id": "c1",
                "name": "环境适应性",
                "type": "category",
                "environmentName": "高温试验室",
                "children": [
                    {
                        "id": "t1",
                        "name": "高温工作",
                        "type": "test",
                        "environmentName": "低温试验室",
                    }
                ],
            }
        ],
        indicators=[],
        methods=[],
    )
    disp = payload.get("skeleton_markdown_display") or ""
    assert "##### 2.1.1.1 环境适应性" in disp
    assert "###### 2.1.1.1.1 高温工作" in disp


def test_emit_indicator_parent_and_child():
    """2.2：#### 父指标 + ##### 子指标。"""
    payload = build_method_tree_and_skeleton(
        equipment=[],
        environments=[],
        projects=[],
        indicators=[
            {
                "id": "c1",
                "name": "父指标",
                "type": "category",
                "children": [{"id": "t1", "name": "指标A1", "type": "test"}],
            }
        ],
        methods=[],
    )
    disp = payload.get("skeleton_markdown_display") or ""
    assert "#### 2.2.1 父指标" in disp
    assert "##### 2.2.1.1 指标A1" in disp


def test_emit_orphan_category_indicator():
    """孤立 category 指标显示为 #### 父级，不嵌套为子级。"""
    payload = build_method_tree_and_skeleton(
        equipment=[],
        environments=[],
        projects=[],
        indicators=[{"id": "c1", "name": "孤立父指标", "type": "category"}],
        methods=[],
    )
    disp = payload.get("skeleton_markdown_display") or ""
    assert "#### 2.2.1 孤立父指标" in disp
    assert "##### 2.2.1.1" not in disp


def test_emit_indicator_child_only_includes_parent():
    """仅选子指标时自动补父级 ####。"""
    payload = build_method_tree_and_skeleton(
        equipment=[],
        environments=[],
        projects=[],
        indicators=[
            {"id": "t1", "name": "指标A1", "type": "test", "categoryId": "c1"},
            {"id": "c1", "name": "父指标", "type": "category"},
        ],
        methods=[],
    )
    disp = payload.get("skeleton_markdown_display") or ""
    assert "#### 2.2.1 父指标" in disp
    assert "##### 2.2.1.1 指标A1" in disp


def test_build_outline_tree_has_2_1_and_2_2_sections():
    """目录树须含 2.1 试验项目 / 2.2 边界性能指标 两节，与骨架 Markdown 一致。"""
    from blueprints.FZSJ.JYLLM.skeleton_builder import build_outline_tree

    payload = build_method_tree_and_skeleton(
        equipment=[],
        environments=[
            {
                "name": "高温试验室",
                "projects": [{"id": "p1", "name": "项目A", "type": "test"}],
            }
        ],
        projects=[],
        indicators=[{"id": "i1", "name": "指标A", "type": "test"}],
        methods=[],
    )
    tree = build_outline_tree(
        payload["method_tree"],
        indicators=payload.get("indicators") or [],
    )
    assert len(tree) == 2
    ch2 = tree[1]
    assert ch2["id"] == "ch2"
    section_ids = [c["id"] for c in ch2.get("children") or []]
    assert section_ids == ["section_2_1", "section_2_2"]
    sec21 = ch2["children"][0]
    assert sec21["title"] == "2.1 试验项目"
    assert sec21["children"][0]["type"] == "environment"
    sec22 = ch2["children"][1]
    assert sec22["title"] == "2.2 边界性能指标"
    assert sec22["children"][0]["type"] == "indicator"


def test_parse_writer_unclosed_content():
    raw = "[THINK]x[/THINK][CONTENT]hello world"
    _, c = parse_writer_tag_blocks(raw, allow_unclosed=False)
    assert c == ""
    _, c2 = parse_writer_tag_blocks(raw, allow_unclosed=True)
    assert c2 == "hello world"
