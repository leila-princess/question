#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
测试 _remove_condition_table_plain_text 函数（独立版本）
"""

import re

def _normalize_for_text_dedupe(text: str) -> str:
    s = str(text or "")
    s = s.translate(str.maketrans("０１２３４５６７８９％．，；：（）ａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺ", "0123456789%.,;:()abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"))
    s = s.replace("—", "-").replace("－", "-").replace("一", "-")
    return re.sub(r"\s+", "", s).lower()


def _looks_like_condition_table_plain_line(line: str) -> bool:
    """判断一行是否看起来像条件表格的散文本（OCR 提取的表格内容）。"""
    key = _normalize_for_text_dedupe(line)
    if not key:
        return False
    # 表格关键词：至少命中 3 个才认为是表格行（提高阈值，避免误判）
    table_words = ("序号", "试验阶段", "温度容差", "相对湿度", "试验周期")
    hits = sum(1 for w in table_words if w in line)
    if hits >= 3:
        return True
    if "升温阶段" in line and "高温温度" in line:
        return True
    if "降温阶段" in line and "高温温度" in line:
        return True
    if "24h为1个周期" in key or "24ｈ为1个周期" in line:
        return True
    # 检测明显的表格数据行（如"1 | 升温阶段 | 30→高温温度 | ±2 | ≥85"）
    if re.search(r"\d+\s*[|｜]\s*\d+", line) and hits >= 2:
        return True
    return False


def _remove_condition_table_plain_text(text: str) -> str:
    """从试验条件正文中剔除明显的 OCR 表格散文本，但保留正文内容。"""
    lines = [x.rstrip() for x in str(text or "").splitlines()]
    kept: list = []
    skip_until_blank = False
    for line in lines:
        raw = line.strip()
        if not raw:
            skip_until_blank = False
            kept.append(line)
            continue
        if _looks_like_condition_table_plain_line(raw):
            skip_until_blank = True
            continue
        # 只跳过明确的表格数据行（以数字开头且包含分隔符的行）
        if skip_until_blank and re.search(r"^(?:[1-9]|一|二|三|四|五|六|七|八|九|十)\s*[|｜\t]", raw):
            continue
        # 不再使用宽泛的正则匹配，避免误删正文
        kept.append(line)
    text2 = "\n".join(kept)
    text2 = re.sub(r"\n{3,}", "\n\n", text2).strip()
    return text2


# 示例PDF中的试验条件文本
sample_text = """4 试验条件
4.1 试验准备
车辆应完整，乘员和随车装备应关闭，必要时应满足制造商要求。
4.2 测量仪器及精度
测量仪器及精度要求见表1。
表1 测量仪器及精度
参数 单位 精确度
时间 s ±0.1s
长度 m ±0.1%
大气温度 ℃ ±1℃
大气压力 kPa ±1 kPa
速度 km/h ±1%或±0.1 km/h(选取较大值)
质量 kg ±0.5%
4.3 道路条件
4.3.1 一般要求
试验应在直线道路(见4.3.2)或环形道路(见4.3.3)上进行。试验道路应是硬质、平整、清洁、干燥并有良好的附着系数。
4.3.2 直线道路要求
4.3.2.1 长度
道路长度应至少为200m，并用标志标明。
"""

print("=" * 80)
print("测试 _looks_like_condition_table_plain_line 函数")
print("=" * 80)

test_lines = [
    "参数 单位 精确度",
    "时间 s ±0.1s",
    "长度 m ±0.1%",
    "大气温度 ℃ ±1℃",
    "4.3 道路条件",
    "4.3.1 一般要求",
    "试验应在直线道路(见4.3.2)或环形道路(见4.3.3)上进行。",
    "序号 试验阶段 温度 温度容差 相对湿度 试验周期",  # 明显的表格行
    "1 升温阶段 30→高温温度 ±2 ≥85 2±0.25",  # 明显的表格行
]

for line in test_lines:
    result = _looks_like_condition_table_plain_line(line)
    print(f"{'是' if result else '否'}: {line}")

print("\n" + "=" * 80)
print("测试 _remove_condition_table_plain_text 函数")
print("=" * 80)

print("\n【原始文本】:")
print(sample_text)

print("\n【处理后的文本】:")
result = _remove_condition_table_plain_text(sample_text)
print(result)

print("\n" + "=" * 80)
print("分析")
print("=" * 80)
print("""
期望结果：
- 保留 "4.1 试验准备" 及其正文
- 保留 "4.2 测量仪器及精度" 及其正文
- 删除表格散文本（如"参数 单位 精确度"、"时间 s ±0.1s"等）
- 保留 "4.3 道路条件" 及其正文
- 保留 "4.3.1 一般要求" 及其正文
- 保留 "4.3.2 直线道路要求" 及其正文

注意：示例PDF中的表格散文本不包含"序号"、"试验阶段"等词，
所以不会被 _looks_like_condition_table_plain_line 识别为表格行。
因此，这些表格散文本会被保留在正文中。

如果需要删除这些表格散文本，需要改进 _looks_like_condition_table_plain_line 函数，
使其能够识别"参数 单位 精确度"这样的表格标题行。
""")
