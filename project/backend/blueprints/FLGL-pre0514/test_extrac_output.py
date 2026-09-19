from docx import Document
import os
import json
import re
import hashlib
from collections import defaultdict

# 全局序号计数器
equipment_seq_counter = 1
equipment_type_seq_counter = 1
environment_seq_counter = 1
project_seq_counter = 1
indicator_seq_counter = 1


def set_id_counters_for_upload(id_starts=None):
    """
    为上传解析流程设置全局 ID 序号起点。

    id_starts 期望结构:
    {
        "equipment_type": int,
        "equipment": int,
        "environment": int,
        "project": int,
        "indicator": int,
    }
    """
    global equipment_seq_counter, equipment_type_seq_counter
    global environment_seq_counter, project_seq_counter, indicator_seq_counter

    starts = id_starts or {}
    equipment_type_seq_counter = int(starts.get("equipment_type", 1) or 1)
    equipment_seq_counter = int(starts.get("equipment", 1) or 1)
    environment_seq_counter = int(starts.get("environment", 1) or 1)
    project_seq_counter = int(starts.get("project", 1) or 1)
    indicator_seq_counter = int(starts.get("indicator", 1) or 1)

def generate_id(prefix, name, seq_num):
    """生成符合规范的ID：前缀_4位数字"""
    if seq_num:
        return f"{prefix}_{seq_num:04d}"
    else:
        hash_val = int(hashlib.md5(name.encode()).hexdigest()[:4], 16) % 10000
        return f"{prefix}_{hash_val:04d}"

# 更智能的解析函数
def parse_test_items(test_content):
    """
    解析试验项目字符串，处理括号嵌套和顿号分隔
    返回: list of dict，每个dict包含 parent 和 children
    """
    if not test_content or test_content in ['—', '--', '---', '']:
        return []
    
    results = []
    
    # 使用正则找到所有 "xxx（yyy）" 的结构
    # 注意：需要处理项目名本身可能包含顿号的情况
    pattern = r'([^（、]+?)（([^）]+)）'
    
    # 先找出所有带括号的完整结构
    last_end = 0
    matches = []
    
    for match in re.finditer(pattern, test_content):
        start, end = match.span()
        
        # 处理括号前的顿号分隔的独立项目
        before_text = test_content[last_end:start]
        if before_text:
            # 按顿号分割独立项目
            independent_items = [item.strip() for item in before_text.split('、') if item.strip()]
            for item in independent_items:
                results.append({
                    'parent': item,
                    'children': []
                })
        
        # 处理当前括号结构：项目名可能是多个顿号分隔的
        parent_part = match.group(1).strip()  # "项目A、项目B" 或 "高温工作试验"
        children_part = match.group(2).strip()  # "高温飞行稳定性测试、高温启动能力测试"
        
        # 父项目可能包含顿号（多个父项目共用一个括号）
        parent_items = [p.strip() for p in parent_part.split('、') if p.strip()]
        
        # 子项目按顿号分割
        child_items = [c.strip() for c in children_part.split('、') if c.strip()]
        
        for parent in parent_items:
            results.append({
                'parent': parent,
                'children': child_items
            })
        
        last_end = end
    
    # 处理最后剩余的部分
    remaining = test_content[last_end:]
    if remaining:
        # 如果剩余部分以顿号开头，去掉开头的顿号
        remaining = remaining.lstrip('、')
        independent_items = [item.strip() for item in remaining.split('、') if item.strip()]
        for item in independent_items:
            results.append({
                'parent': item,
                'children': []
            })
    
    return results

def infer_indicator_type(indicator_name):
    """推断指标类型"""
    if '距离' in indicator_name or '长度' in indicator_name or '里程' in indicator_name:
        return '距离'
    elif '时间' in indicator_name or '时长' in indicator_name or '周期' in indicator_name:
        return '时间'
    elif '速度' in indicator_name or '速率' in indicator_name:
        return '速度'
    elif '高度' in indicator_name or '海拔' in indicator_name:
        return '高度'
    else:
        return '其他'


def split_indicator_items(indicator_content):
    """
    分步拆分指标内容，并输出中间结果，便于定位未拆分问题。
    默认按以下顺序拆分:
      1) 换行
      2) 分号(中文/英文)
      3) 顿号
      4) 逗号(中文/英文)
    """
    if not indicator_content:
        print("[split_indicator_items] input is empty -> []")
        return []
    text = str(indicator_content).strip()
    if not text:
        print("[split_indicator_items] text after strip is empty -> []")
        return []

    print(f"[split_indicator_items] raw: {repr(indicator_content)}")
    print(f"[split_indicator_items] stripped: {repr(text)}")

    # Step 1: 统一换行符并按换行粗拆
    normalized = text.replace('\r\n', '\n').replace('\r', '\n')
    parts_step1 = [p.strip() for p in normalized.split('\n') if p.strip()]
    print(f"[split_indicator_items] step1 newline split: {parts_step1}")

    # Step 2: 按分号拆
    parts_step2 = []
    for part in parts_step1:
        chunks = [c.strip() for c in re.split(r'[；;]+', part) if c.strip()]
        if chunks:
            parts_step2.extend(chunks)
    print(f"[split_indicator_items] step2 semicolon split: {parts_step2}")

    # Step 3: 按顿号拆（你当前重点）
    parts_step3 = []
    for part in parts_step2:
        chunks = [c.strip() for c in part.split('、') if c.strip()]
        if chunks:
            parts_step3.extend(chunks)
    print(f"[split_indicator_items] step3 dunhao split: {parts_step3}")

    # Step 4: 按逗号拆（兜底）
    parts_step4 = []
    for part in parts_step3:
        chunks = [c.strip() for c in re.split(r'[，,]+', part) if c.strip()]
        if chunks:
            parts_step4.extend(chunks)
    print(f"[split_indicator_items] step4 comma split: {parts_step4}")

    # 去重并保持顺序
    final_items = []
    seen = set()
    for p in parts_step4:
        if p not in seen:
            seen.add(p)
            final_items.append(p)
    print(f"[split_indicator_items] final: {final_items}")
    return final_items

def extract_environment_type(env_name):
    """提取8类环境类型"""
    env_mapping = {
        '高温': '高温环境',
        '低温': '低温环境',
        '湿热': '湿热环境',
        '盐雾': '盐雾环境',
        '振动': '振动环境',
        '冲击': '冲击环境',
        '低气压': '低气压环境',
        '太阳辐射': '太阳辐射环境',
        '大风': '大风环境',
        '强磁场': '强磁场环境',
        '高原': '高原环境',
        '复杂气象': '复杂气象环境',
        '复杂地理': '复杂地理环境',
        '复杂水文': '复杂水文环境',
    }
    for key, value in env_mapping.items():
        if key in env_name:
            return value
    return '其他环境'

def get_table_caption(table):
    """获取表格上方的题注（标题），并提取一级设备类别"""
    parent = table._element.getparent()
    captions = []
    for elem in parent.iterchildren():
        if elem is table._element:
            break
        if elem.tag == '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p':
            texts = []
            for t in elem.iterfind('.//{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t'):
                if t.text:
                    texts.append(t.text)
            if texts:
                caption_text = ''.join(texts).strip()
                if caption_text:
                    captions.append(caption_text)
    
    if captions:
        full_caption = captions[-1]
        match = re.match(r'([^表格\d\s]+)(?:复杂环境适应性试验项目和边界性能考核指标清单)', full_caption)
        if match:
            device_category_level1 = match.group(1).strip()
        else:
            match = re.search(r'(?:空中|地面|水上|水下|陆地)设备', full_caption)
            if match:
                device_category_level1 = match.group(0)
            else:
                device_category_level1 = full_caption
        return device_category_level1, full_caption
    return "未知设备", "表格"

def extract_entities_from_table(table, level1_category):
    """从表格中提取实体节点和关系"""
    global equipment_seq_counter, equipment_type_seq_counter, environment_seq_counter, project_seq_counter, indicator_seq_counter
    
    rows = len(table.rows)
    if rows < 2:
        return {
            'level1_category': level1_category,
            'level2_categories': [],
            'devices': [],
            'environments': [],
            'test_items': [],
            'indicators': [],
            'relationships': []
        }
    
    # 获取所有单元格的原始文本
    all_cells = []
    for row in table.rows:
        row_cells = []
        for cell in row.cells:
            text = ' '.join(cell.text.strip().split())
            row_cells.append(text)
        all_cells.append(row_cells)
    
    # 分析表头结构
    first_row = all_cells[0] if len(all_cells) > 0 else []
    second_row = all_cells[1] if len(all_cells) > 1 else []
    
    # 构建列索引映射
    col_mapping = {
        'serial_num': None,
        'category_level2': None,
        'device_name': None,
        'environments': [],
        'indicators': None,
        'remarks': None
    }
    
    col_idx = 0
    while col_idx < len(second_row):
        cell_value = second_row[col_idx] if col_idx < len(second_row) else ""
        parent = first_row[col_idx] if col_idx < len(first_row) else ""
        
        if cell_value == '序号' or parent == '序号':
            col_mapping['serial_num'] = col_idx
        elif cell_value == '类别' or parent == '类别':
            col_mapping['category_level2'] = col_idx
        elif cell_value == '设备' or parent == '设备':
            col_mapping['device_name'] = col_idx
        elif cell_value in ['天气环境', '复杂地环境', '复杂水环境', '高原环境', '其他环境', 
                           '复杂气象环境']:
            col_mapping['environments'].append({
                'index': col_idx,
                'name': cell_value,
                'parent': parent
            })
        elif cell_value == '指标' or parent == '指标':
            col_mapping['indicators'] = col_idx
        elif cell_value == '备注' or parent == '备注':
            col_mapping['remarks'] = col_idx
        
        col_idx += 1
    
    entities = {
        'level1_category': level1_category,
        'level2_categories': [],
        'devices': [],
        'environments': [],
        'test_items': [],
        'indicators': [],
        'relationships': []
    }
    
    # 用于记录映射关系
    level2_to_devices = defaultdict(list)
    device_ids_map = {}
    env_ids_map = {}
    device_to_level2 = {}
    
    # 从第3行开始遍历数据行
    for row_idx in range(2, len(all_cells)):
        row = all_cells[row_idx]
        
        serial_num = row[col_mapping['serial_num']] if col_mapping['serial_num'] is not None and col_mapping['serial_num'] < len(row) else ''
        category_level2 = row[col_mapping['category_level2']] if col_mapping['category_level2'] is not None and col_mapping['category_level2'] < len(row) else ''
        device_name = row[col_mapping['device_name']] if col_mapping['device_name'] is not None and col_mapping['device_name'] < len(row) else ''
        
        if not serial_num and not category_level2 and not device_name:
            continue
        
        # 添加二级设备类别节点 (EquipmentType)
        if category_level2:
            existing = next((c for c in entities['level2_categories'] if c['Name'] == category_level2), None)
            if not existing:
                level2_id = generate_id('ZBLX', category_level2, equipment_type_seq_counter)
                equipment_type_seq_counter += 1
                
                entities['level2_categories'].append({
                    'Id': level2_id,
                    'Name': category_level2,
                    'Label': '设备类型',
                    'Description': f"二级设备类别：{category_level2}",
                    'Type': level1_category,
                    'Status': '启用',
                    'User_id': 'system',
                    'Create_time': '2024-06-01T00:00:00Z'
                })
        
        # 处理设备节点 (Equipment)
        if device_name:
            existing_device = next((d for d in entities['devices'] if d['Name'] == device_name), None)
            if not existing_device:
                device_id = generate_id('ZB', device_name, equipment_seq_counter)
                equipment_seq_counter += 1
                device_ids_map[device_name] = device_id
                
                if category_level2:
                    device_to_level2[device_id] = category_level2
                
                device_entity = {
                    'Id': device_id,
                    'Name': device_name,
                    'Label': category_level2 if category_level2 else '设备',
                    'Type': category_level2 if category_level2 else level1_category,
                    'Description': f"{level1_category}设备 - {category_level2} - {device_name}",
                    'Status': '启用',
                    'User_id': 'system',
                    'Create_time': '2024-06-01T00:00:00Z'
                }
                entities['devices'].append(device_entity)
                
                if category_level2:
                    level2_obj = next((c for c in entities['level2_categories'] if c['Name'] == category_level2), None)
                    if level2_obj:
                        level2_to_devices[level2_obj['Id']].append(device_id)
            else:
                device_id = existing_device['Id']
                device_ids_map[device_name] = device_id
                if category_level2 and device_id not in device_to_level2:
                    device_to_level2[device_id] = category_level2
        
        current_device_id = device_ids_map.get(device_name)
        if not current_device_id:
            continue
        
        # 提取环境相关的试验项目
        for env_col in col_mapping['environments']:
            env_name = env_col['name']
            env_idx = env_col['index']
            
            if env_name not in env_ids_map:
                env_id = generate_id('HJ', env_name, environment_seq_counter)
                environment_seq_counter += 1
                env_ids_map[env_name] = env_id
                
                env_type = extract_environment_type(env_name)
                
                entities['environments'].append({
                    'Id': env_id,
                    'Name': env_name,
                    'Label': '环境',
                    'Description': f"{env_name}试验环境",
                    'Type': env_type,
                    'Class': '综合环境',
                    'Combination': '',
                    'Limit': '',
                    'Request': '',
                    'Status': '启用',
                    'User_id': 'system',
                    'Create_time': '2024-06-01T00:00:00Z'
                })
            
            if env_idx < len(row):
                test_content = row[env_idx]
                if test_content and test_content not in ['—', '-', '---', '']:
                    # 使用新的解析函数处理所有情况
                    parsed_items = parse_test_items(test_content)
                    
                    if parsed_items:
                        # 有解析结果（包括独立项目和括号结构）
                        for item in parsed_items:
                            parent_test = item['parent']
                            child_projects = item['children']
                            
                            # 创建父级项目（category类型）
                            parent_test_id = generate_id('XM', parent_test, project_seq_counter)
                            project_seq_counter += 1
                            
                            parent_test_entity = {
                                'Id': parent_test_id,
                                'Name': parent_test,
                                'Type': 'category',
                                'Label': parent_test,
                                'Description': parent_test,
                                'Status': '启用',
                                'User_id': 'system',
                                'Create_time': '2024-06-01T00:00:00Z'
                            }
                            entities['test_items'].append(parent_test_entity)
                            
                            # 设备到父级项目的关系
                            entities['relationships'].append({
                                'type': 'CON',
                                'from_id': current_device_id,
                                'from_type': 'Equipment',
                                'to_id': parent_test_id,
                                'to_type': 'TestProject'
                            })
                            
                            # 环境到父级项目的关系
                            entities['relationships'].append({
                                'type': 'CON',
                                'from_id': env_ids_map[env_name],
                                'from_type': 'Environment',
                                'to_id': parent_test_id,
                                'to_type': 'TestProject'
                            })
                            
                            # 处理子项目
                            for sub_project in child_projects:
                                sub_project = sub_project.strip()
                                if not sub_project:
                                    continue
                                
                                # 创建子级项目（test类型）
                                child_test_id = generate_id('XM', sub_project, project_seq_counter)
                                project_seq_counter += 1
                                
                                child_test_entity = {
                                    'Id': child_test_id,
                                    'Name': sub_project,
                                    'Type': 'test',
                                    'Label': parent_test,
                                    'Description': f"{parent_test} - {sub_project}",
                                    'Status': '启用',
                                    'User_id': 'system',
                                    'Create_time': '2024-06-01T00:00:00Z'
                                }
                                entities['test_items'].append(child_test_entity)
                                
                                # 设备到子级项目的关系
                                entities['relationships'].append({
                                    'type': 'CON',
                                    'from_id': current_device_id,
                                    'from_type': 'Equipment',
                                    'to_id': child_test_id,
                                    'to_type': 'TestProject'
                                })
                                
                                # 环境到子级项目的关系
                                entities['relationships'].append({
                                    'type': 'CON',
                                    'from_id': env_ids_map[env_name],
                                    'from_type': 'Environment',
                                    'to_id': child_test_id,
                                    'to_type': 'TestProject'
                                })
                                
                                # 父级项目到子级项目的关系
                                entities['relationships'].append({
                                    'type': 'HAS',
                                    'from_id': parent_test_id,
                                    'from_type': 'TestProject',
                                    'to_id': child_test_id,
                                    'to_type': 'TestProject'
                                })
                    else:
                        # 完全没有任何内容（parse_test_items返回空列表的情况）
                        # 这种情况理论上不会发生，因为上面的if已经判断test_content不为空
                        pass
        
        # 提取指标
        if col_mapping['indicators'] is not None and col_mapping['indicators'] < len(row):
            indicator_content = row[col_mapping['indicators']]
            if indicator_content and indicator_content not in ['—', '-', '---', '']:
                indicator_names = split_indicator_items(indicator_content)
                if not indicator_names:
                    indicator_names = [indicator_content]

                for indicator_name in indicator_names:
                    existing_indicator = next((i for i in entities['indicators'] if i['Name'] == indicator_name), None)
                    if not existing_indicator:
                        indicator_id = generate_id('ID', indicator_name, indicator_seq_counter)
                        indicator_seq_counter += 1

                        indicator_type = infer_indicator_type(indicator_name)

                        indicator_entity = {
                            'Id': indicator_id,
                            'Name': indicator_name,
                            'Label': '指标',
                            'Description': indicator_name,
                            'Type': indicator_type,
                            'Status': '启用',
                            'User_id': 'system',
                            'Create_time': '2024-06-01T00:00:00Z'
                        }
                        entities['indicators'].append(indicator_entity)
                    else:
                        indicator_id = existing_indicator['Id']

                    entities['relationships'].append({
                        'type': 'CON',
                        'from_id': current_device_id,
                        'from_type': 'Equipment',
                        'to_id': indicator_id,
                        'to_type': 'Indicator'
                    })
    
    # 添加所有 HAS 关系
    for level2_id, device_ids in level2_to_devices.items():
        for device_id in device_ids:
            existing_rel = any(
                r for r in entities['relationships'] 
                if r.get('type') == 'HAS' and r.get('from_id') == level2_id and r.get('to_id') == device_id
            )
            if not existing_rel:
                entities['relationships'].append({
                    'type': 'HAS',
                    'from_id': level2_id,
                    'from_type': 'EquipmentType',
                    'to_id': device_id,
                    'to_type': 'Equipment'
                })
    
    return entities

def print_entities(entities):
    """打印提取的实体和关系"""
    print(f"\n📌 一级设备类别: {entities.get('level1_category', '未知')}")
    
    print(f"\n📦 二级设备类别 (EquipmentType) ({len(entities.get('level2_categories', []))}个):")
    for cat in entities.get('level2_categories', []):
        print(f"  - [{cat['Id']}] {cat['Name']} (Type: {cat['Type']})")
    
    print(f"\n🔧 具体设备 (Equipment) ({len(entities.get('devices', []))}个):")
    for device in entities.get('devices', []):
        print(f"  - [{device['Id']}] {device['Name']} (Type: {device['Type']})")
    
    print(f"\n🌍 环境类型 (Environment) ({len(entities.get('environments', []))}个):")
    for env in entities.get('environments', []):
        print(f"  - [{env['Id']}] {env['Name']}")
    
    # 按类型分组显示试验项目
    category_projects = [p for p in entities.get('test_items', []) if p.get('Type') == 'category']
    test_projects = [p for p in entities.get('test_items', []) if p.get('Type') == 'test']
    
    print(f"\n🧪 试验项目 - category 类型 ({len(category_projects)}个):")
    for test in category_projects[:10]:
        print(f"  - [{test['Id']}] {test['Name']}")
    if len(category_projects) > 10:
        print(f"  ... 共{len(category_projects)}个 category 项目")
    
    print(f"\n🧪 试验项目 - test 类型 ({len(test_projects)}个):")
    for test in test_projects[:10]:
        print(f"  - [{test['Id']}] {test['Name']} (父项目: {test.get('Label', '')})")
    if len(test_projects) > 10:
        print(f"  ... 共{len(test_projects)}个 test 项目")
    
    print(f"\n📊 指标 (Indicator) ({len(entities.get('indicators', []))}个):")
    for indicator in entities.get('indicators', [])[:10]:
        print(f"  - [{indicator['Id']}] {indicator['Name'][:40]}... (Type: {indicator['Type']})")
    if len(entities.get('indicators', [])) > 10:
        print(f"  ... 共{len(entities['indicators'])}个指标")
    
    print(f"\n🔗 关系 ({len(entities.get('relationships', []))}个):")
    for rel in entities.get('relationships', [])[:10]:
        print(f"  - {rel['from_id']} --{rel['type']}--> {rel['to_id']}")
    if len(entities.get('relationships', [])) > 10:
        print(f"  ... 共{len(entities['relationships'])}个关系")

def export_to_json(entities, output_file):
    """导出为JSON格式"""
    nodes = {
        'EquipmentType': [],
        'Equipment': [],
        'Environment': [],
        'TestProject': [],
        'Indicator': []
    }
    
    for item in entities.get('level2_categories', []):
        nodes['EquipmentType'].append(item)
    
    for item in entities.get('devices', []):
        nodes['Equipment'].append(item)
    
    for item in entities.get('environments', []):
        nodes['Environment'].append(item)
    
    for item in entities.get('test_items', []):
        nodes['TestProject'].append(item)
    
    for item in entities.get('indicators', []):
        nodes['Indicator'].append(item)
    
    payload = {
        'nodes': nodes,
        'relationships': entities.get('relationships', [])
    }
    
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"\n✅ 已导出到: {output_file}")

def merge_entities(all_entities):
    """合并多个表格的实体"""
    merged = {
        'level1_category': 'Mixed',
        'level2_categories': [],
        'devices': [],
        'environments': [],
        'test_items': [],
        'indicators': [],
        'relationships': []
    }
    
    seen_ids = {
        'EquipmentType': set(),
        'Equipment': set(),
        'Environment': set(),
        'TestProject': set(),
        'Indicator': set(),
        'Relationship': set()
    }
    
    for entities in all_entities:
        for cat in entities.get('level2_categories', []):
            if cat['Id'] not in seen_ids['EquipmentType']:
                seen_ids['EquipmentType'].add(cat['Id'])
                merged['level2_categories'].append(cat)
        
        for device in entities.get('devices', []):
            if device['Id'] not in seen_ids['Equipment']:
                seen_ids['Equipment'].add(device['Id'])
                merged['devices'].append(device)
        
        for env in entities.get('environments', []):
            if env['Id'] not in seen_ids['Environment']:
                seen_ids['Environment'].add(env['Id'])
                merged['environments'].append(env)
        
        for test in entities.get('test_items', []):
            if test['Id'] not in seen_ids['TestProject']:
                seen_ids['TestProject'].add(test['Id'])
                merged['test_items'].append(test)
        
        for indicator in entities.get('indicators', []):
            if indicator['Id'] not in seen_ids['Indicator']:
                seen_ids['Indicator'].add(indicator['Id'])
                merged['indicators'].append(indicator)
        
        for rel in entities.get('relationships', []):
            rel_key = f"{rel['from_id']}_{rel['type']}_{rel['to_id']}"
            if rel_key not in seen_ids['Relationship']:
                seen_ids['Relationship'].add(rel_key)
                merged['relationships'].append(rel)
    
    return merged

def process_word_document(file_path):
    """处理Word文档，提取所有表格的实体"""
    global equipment_seq_counter, equipment_type_seq_counter, environment_seq_counter, project_seq_counter, indicator_seq_counter
    
    # 重置计数器
    equipment_seq_counter = 1
    equipment_type_seq_counter = 1
    environment_seq_counter = 1
    project_seq_counter = 1
    indicator_seq_counter = 1
    
    try:
        doc = Document(file_path)
    except Exception as e:
        print(f"无法打开文档: {e}")
        return
    
    if not doc.tables:
        print("文档中没有找到表格")
        return
    
    all_entities = []
    
    for table_idx, table in enumerate(doc.tables, 1):
        print("\n" + "=" * 80)
        
        level1_category, full_caption = get_table_caption(table)
        print(f"📋 表题注: {full_caption}")
        print(f"🏷️  一级设备类别: {level1_category}")
        
        entities = extract_entities_from_table(table, level1_category)
        all_entities.append(entities)
        
        print_entities(entities)
        
        json_file = f"entities_table_{table_idx}_{level1_category}.json"
        export_to_json(entities, json_file)
    
    # 合并所有实体
    if all_entities:
        combined_entities = merge_entities(all_entities)
        export_to_json(combined_entities, "all_entities_combined.json")
        
        # 统计信息
        category_count = len([p for p in combined_entities['test_items'] if p.get('Type') == 'category'])
        test_count = len([p for p in combined_entities['test_items'] if p.get('Type') == 'test'])
        
        print("\n" + "=" * 80)
        print("✅ 所有表格处理完成！")
        print(f"\n📊 统计信息:")
        print(f"  EquipmentType节点: {len(combined_entities['level2_categories'])}")
        print(f"  Equipment节点: {len(combined_entities['devices'])}")
        print(f"  Environment节点: {len(combined_entities['environments'])}")
        print(f"  TestProject节点 - category类型: {category_count}")
        print(f"  TestProject节点 - test类型: {test_count}")
        print(f"  Indicator节点: {len(combined_entities['indicators'])}")
        print(f"  关系数量: {len(combined_entities['relationships'])}")

if __name__ == "__main__":
    file_path = "试验项目表.docx"
    
    if not os.path.exists(file_path):
        print(f"文件不存在: {file_path}")
        print(f"当前目录: {os.getcwd()}")
    else:
        process_word_document(file_path)