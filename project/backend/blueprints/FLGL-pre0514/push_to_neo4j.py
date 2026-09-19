from neo4j import GraphDatabase
import json
import os
from dotenv import load_dotenv, find_dotenv
from pathlib import Path
from typing import Dict, Any, Optional, List

# 加载 .env 文件
load_dotenv(find_dotenv())

# 替换为你的实际信息
URI = os.getenv("NEO4j_URI")
USER = os.getenv("NEO4j_USER")
PASSWORD = os.getenv("NEO4j_PASSWORD")
DATA_FILE = os.getenv("DATA_FILE", "entities_table_1_空中设备复杂环境适应性试验项目和边界性能.json")


class Neo4jConnector:
    def __init__(self):
        self.driver = GraphDatabase.driver(URI, auth=(USER, PASSWORD))
    
    def close(self):
        self.driver.close()
    
    def test_connection(self):
        with self.driver.session() as session:
            try:
                result = session.run("RETURN 'Neo4j 连接成功！' AS message")
                print(result.single()["message"])
                return True
            except Exception as e:
                print(f"❌ Neo4j 连接失败: {e}")
                return False

    def clear_database(self, confirm=False):
        """清空数据库（谨慎使用）"""
        if not confirm:
            response = input("⚠️  确认清空所有数据？(yes/no): ")
            if response.lower() != 'yes':
                print("操作已取消")
                return        
        with self.driver.session() as session:
            session.run("MATCH (n) DETACH DELETE n")
            print("✅ 数据库已清空")
    
    def create_node(self, label, properties):
        """创建单个节点"""
        with self.driver.session() as session:
            query = """
            MERGE (n:%s {id: $id})
            SET n = $properties
            RETURN n
            """ % label
            result = session.run(query, id=properties.get('id'), properties=properties)
            if result.single():
                print(f"  ✓ 创建/更新: {label} - {properties.get('name', properties.get('id'))}")

    def create_relationship(self, rel_type, from_id, to_id, properties=None):
        """创建关系"""
        with self.driver.session() as session:
            query = """
            MATCH (a {id: $from_id})
            MATCH (b {id: $to_id})
            MERGE (a)-[r:%s]->(b)
            """ % rel_type
            
            if properties:
                query += " SET r = $props"
                session.run(query, from_id=from_id, to_id=to_id, props=properties)
            else:
                session.run(query, from_id=from_id, to_id=to_id)
            
            print(f"  ✓ 创建关系: {rel_type} ({from_id} → {to_id})")

    def import_data(self, data):
        """批量导入JSON数据"""
        print("\n" + "=" * 60)
        print("开始导入数据到 Neo4j...")
        print("=" * 60)
        
        # 统计信息
        stats = {}
        
        # 1. 创建所有节点
        print("\n📦 步骤 1/2: 创建节点...")
        for node_type, nodes in data['nodes'].items():
            print(f"\n  处理 {node_type.upper()} 节点 ({len(nodes)} 个):")
            label = node_type.capitalize()
            stats[node_type] = len(nodes)
            
            for node in nodes:
                self.create_node(label, node)
        
        # 2. 创建所有关系
        print("\n🔗 步骤 2/2: 创建关系...")
        rel_count = len(data['relationships'])
        print(f"\n  处理 {rel_count} 个关系:")
        
        for rel in data['relationships']:
            rel_type = rel['type']
            from_id = rel['from']
            to_id = rel['to']
            properties = rel.get('properties', {})
            self.create_relationship(rel_type, from_id, to_id, properties)
        
        print("\n" + "=" * 60)
        print("✅ 数据导入完成！")
        print(f"   - 节点总数: {sum(stats.values())} 个")
        print(f"   - 关系总数: {rel_count} 个")
        print("=" * 60)
        
        return stats

    def create_indexes(self):
        """创建索引以提高查询性能"""
        print("\n📇 创建索引...")
        with self.driver.session() as session:
            indexes = [
                "CREATE INDEX equipment_id IF NOT EXISTS FOR (e:Equipment) ON (e.id)",
                "CREATE INDEX environment_id IF NOT EXISTS FOR (e:Environment) ON (e.id)",
                "CREATE INDEX project_id IF NOT EXISTS FOR (p:Project) ON (p.id)",
                "CREATE INDEX indicator_id IF NOT EXISTS FOR (i:Indicator) ON (i.id)",
                "CREATE INDEX method_id IF NOT EXISTS FOR (m:Method) ON (m.id)",
                "CREATE INDEX standard_id IF NOT EXISTS FOR (s:Standard) ON (s.id)"
            ]
            
            for index_query in indexes:
                try:
                    session.run(index_query)
                    print(f"  ✓ {index_query.split('FOR')[0].strip()}")
                except Exception as e:
                    if "already exists" in str(e):
                        print(f"  ℹ️  索引已存在: {index_query.split('FOR')[0].strip()}")
                    else:
                        print(f"  ⚠️  创建索引失败: {e}")

    def verify_import(self):
        """验证导入结果"""
        print("\n" + "=" * 60)
        print("📊 数据统计验证:")
        print("=" * 60)
        
        with self.driver.session() as session:
            # 统计各类型节点数量
            node_types = ['Equipment', 'Environment', 'Project', 'Indicator', 'Method', 'Standard']
            for node_type in node_types:
                result = session.run(f"MATCH (n:{node_type}) RETURN count(n) AS count")
                count = result.single()['count']
                print(f"  {node_type}: {count} 个节点")
            
            # 统计关系数量
            result = session.run("MATCH ()-[r]->() RETURN count(r) AS count")
            total_rels = result.single()['count']
            print(f"\n  总关系数: {total_rels}")
            
            # 显示示例数据
            print("\n📝 示例数据 (Equipment → Project):")
            result = session.run("""
                MATCH (e:Equipment)-[:HAS_PROJECT]->(p:Project)
                RETURN e.name AS equipment, p.name AS project
                LIMIT 5
            """)
            for record in result:
                print(f"  {record['equipment']} → {record['project']}")

    def query_project_detail(self, project_name):
        """查询项目详细信息"""
        print(f"\n🔍 查询项目: {project_name}")
        print("-" * 40)
        
        with self.driver.session() as session:
            query = """
            MATCH (p:Project {name: $project_name})
            OPTIONAL MATCH (p)-[:HAS_INDICATOR]->(i:Indicator)
            OPTIONAL MATCH (p)-[:HAS_METHOD]->(m:Method)
            OPTIONAL MATCH (m)-[:REFERENCES]->(s:Standard)
            OPTIONAL MATCH (m)-[u:USES_INDICATOR]->(ind:Indicator)
            RETURN p.name AS project,
                   collect(DISTINCT i.name) AS indicators,
                   collect(DISTINCT m.name) AS methods,
                   collect(DISTINCT s.title) AS standards,
                   collect(DISTINCT {method: m.name, typical_value: u.typical_value}) AS typical_values
            """
            result = session.run(query, project_name=project_name)
            record = result.single()
            
            if record:
                print(f"\n📋 项目名称: {record['project']}")
                print(f"\n📊 相关指标:")
                for ind in record['indicators']:
                    print(f"   - {ind}")
                
                print(f"\n🔧 试验方法:")
                for method in record['methods']:
                    print(f"   - {method}")
                
                print(f"\n📚 参考标准:")
                for std in record['standards']:
                    print(f"   - {std}")
                
                if record['typical_values']:
                    print(f"\n📈 典型值:")
                    for tv in record['typical_values']:
                        if tv['method'] and tv['typical_value']:
                            print(f"   - {tv['method']}: {tv['typical_value']}")
            else:
                print(f"未找到项目: {project_name}")

    
def load_json_data(file_path):
    """从JSON文件加载数据"""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        print(f"✅ 成功加载数据文件: {file_path}")
        print(f"   - 节点类型: {len(data['nodes'])} 种")
        print(f"   - 关系数量: {len(data['relationships'])} 条")
        return data
    except FileNotFoundError:
        print(f"❌ 文件未找到: {file_path}")
        return None
    except json.JSONDecodeError as e:
        print(f"❌ JSON 解析错误: {e}")
        return None


def main():
    """主函数"""
    print("=" * 60)
    print("Neo4j 数据导入工具")
    print("=" * 60)
    
    # 检查数据文件是否存在
    current_dir = Path(__file__).parent
    data_file_path = current_dir / DATA_FILE
    
    if not data_file_path.exists():
        print(f"❌ 数据文件不存在: {data_file_path}")
        print(f"   请确保 {DATA_FILE} 文件与脚本在同一目录下")
        return
    
    # 加载数据
    data = load_json_data(data_file_path)
    if not data:
        return
    
    # 连接数据库
    db = Neo4jConnector()
    
    try:
        # 测试连接
        if not db.test_connection():
            return
        
        # 询问是否清空数据库
        print("\n" + "-" * 60)
        choice = input("是否清空现有数据？(y/n, 默认 n): ").strip().lower()
        if choice == 'y':
            db.clear_database(confirm=True)
        
        # 导入数据
        db.import_data(data)
        
        # 创建索引
        db.create_indexes()
        
        # 验证导入结果
        db.verify_import()
        
        # 查询示例
        print("\n" + "=" * 60)
        db.query_project_detail("低温贮存试验项目")
        
        print("\n" + "=" * 60)
        print("✅ 所有操作完成！")
        print("=" * 60)
        
    except Exception as e:
        print(f"\n❌ 发生错误: {e}")
        import traceback
        traceback.print_exc()
    finally:
        db.close()

if __name__ == "__main__":
    main()