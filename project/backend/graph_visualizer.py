# graph_visualizer.py
from graphviz import Digraph
import base64
import tempfile
import os

class GraphVisualizer:
    def __init__(self):
        # 确保输出目录存在
        self.output_dir = "graph_output"
        os.makedirs(self.output_dir, exist_ok=True)
    
    def visualize_graph(self, records, depth=1):
        """将查询结果转换为Graphviz图谱"""
        dot = Digraph(comment='Knowledge Graph', format='png')
        dot.attr(rankdir='LR')  # 从左到右布局
        
        # 收集所有节点和关系
        nodes = set()
        edges = set()
        
        for record in records:
            # 提取节点信息
            node = record.get('n') or {}
            node_id = node.get('id') or node.get('name') or "unknown"
            node_label = node.get('name') or node.get('title') or "Unknown Node"
            node_type = node.get('labels', ['Unknown'])[0]
            
            # 添加节点
            nodes.add((node_id, node_label, node_type))
            
            # 提取关系信息
            if 'r' in record and record['r']:
                rel = record['r']
                rel_type = rel.get('type') or "RELATES_TO"
                rel_props = {k: v for k, v in rel.items() if k not in ['id', 'type']}
                
                # 添加关系
                target = record.get('m') or {}
                target_id = target.get('id') or target.get('name') or "unknown"
                target_label = target.get('name') or target.get('title') or "Unknown Node"
                target_type = target.get('labels', ['Unknown'])[0]
                
                nodes.add((target_id, target_label, target_type))
                edges.add((node_id, target_id, rel_type, rel_props))
        
        # 添加节点到Graphviz
        for node_id, label, node_type in nodes:
            color = self._get_node_color(node_type)
            dot.node(str(node_id), f"{label}\n({node_type})", 
                     shape='ellipse', style='filled', fillcolor=color, 
                     fontsize='10', fontname='Arial')
        
        # 添加关系到Graphviz
        for src, tgt, rel_type, props in edges:
            label = rel_type
            if props:
                label += f"\n{', '.join(f'{k}:{v}' for k, v in props.items())[:20]}"
            dot.edge(str(src), str(tgt), label=label, fontsize='8', fontname='Arial')
        
        # 渲染为图片
        file_path = os.path.join(self.output_dir, 'knowledge_graph')
        dot.render(file_path, format='png', cleanup=True)
        
        # 读取图片并转换为Base64
        with open(f"{file_path}.png", 'rb') as f:
            img_data = f.read()
        
        return base64.b64encode(img_data).decode('utf-8')
    
    def _get_node_color(self, node_type):
        """根据节点类型返回不同的颜色"""
        color_map = {
            "Standard": "#a6cee3",
            "Part": "#1f78b4",
            "TestType": "#b2df8a",
            "EquipmentCategory": "#33a02c",
            "Term": "#fb9a99",
            "TestCondition": "#e31a1c"
        }
        return color_map.get(node_type, "#d9d9d9")

# 全局实例
graph_visualizer = GraphVisualizer()