#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
快速测试关键词匹配改进
"""
import json

# 读取改进后的配置
config_path = r"F:\antd-demo1\backend\blueprints\FLGL\GetTestMethod\section_extract_config.json"
with open(config_path, 'r', encoding='utf-8') as f:
    config = json.load(f)

print("=" * 80)
print("改进后的配置关键词")
print("=" * 80)

print("\n【试验条件】关键词:")
for kw in config['title_keywords']['sytj']:
    print(f"  - {kw}")

print("\n【试验过程】关键词:")
for kw in config['title_keywords']['sygc']:
    print(f"  - {kw}")

print("\n【结果评定】关键词:")
for kw in config['title_keywords']['jgpd']:
    print(f"  - {kw}")

# 测试示例PDF中的章节标题
test_titles = [
    "4 试验条件",
    "4.1 试验准备",
    "4.2 测量仪器及精度",
    "4.3 道路条件",
    "4.4 大气条件",
    "4.5 试验车辆载荷",
    "5 试验方法",
    "5.1 试验车辆准备",
    "5.2 直线道路上的最高车速试验",
    "5.2.1 标准试验方法（双向试验）",
    "5.2.2 其他试验方法",
    "5.3 环形道路上的最高车速",
    "附录A 环形道路最高车速的确定方法",
    "附录B 空气密度计算公式",
    "附录C 最高车速试验数据记录表",
]

print("\n" + "=" * 80)
print("测试标题匹配")
print("=" * 80)

def match_bucket(title, config):
    """模拟标题匹配逻辑"""
    for bucket in ['sytj', 'sygc', 'jgpd']:
        for kw in config['title_keywords'][bucket]:
            if kw in title:
                return bucket
    return "none"

for title in test_titles:
    bucket = match_bucket(title, config)
    bucket_label = {
        'sytj': '【试验条件】',
        'sygc': '【试验过程】',
        'jgpd': '【结果评定】',
        'none': '【未匹配】'
    }.get(bucket, bucket)
    print(f"{title:40s} -> {bucket_label}")

print("\n" + "=" * 80)
print("分析")
print("=" * 80)
print("""
改进点：
1. 增加了"试验方法"作为 sygc 的关键词 - 可以匹配"5 试验方法"
2. 增加了"试验要求"、"大气条件"等 sytj 关键词
3. 增加了"记录表"、"数据表"等 jgpd 关键词 - 可以匹配附录C

对于这个PDF：
- "4 试验条件" 及其子节 -> 【试验条件】
- "5 试验方法" 及其子节 -> 【试验过程】（新增"试验方法"关键词）
- 附录C "试验数据记录表" -> 【结果评定】（新增"记录表"关键词）

注意：这个PDF没有独立的"结果评定"章节，评定内容融合在试验方法中（如计算公式、数据记录表）。
需要在后处理中从试验过程章节提取包含评定关键词的段落。
""")
