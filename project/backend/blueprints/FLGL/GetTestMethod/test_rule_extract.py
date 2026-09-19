import re

text = """===== Page 4 =====
GB/T38924的本部分规定了民用轻小型无人机（起飞重量在0.25kg～150kg之间）系统（含飞行
器和地面站)高温试验的试验条件、试验设备和仪器、试验过程、试验中断和恢复、试验结果评定和试验
报告。
本部分适用于在使用过程中可能会受到高温环境影响的民用轻小型无人机系统，以此来确定民用
轻小型无人机系统对高温环境的耐受能力。
件。凡是不注日期的引用文件，其最新版本(包括所有的修改单)适用于本文件。
GB/T389241—2020
民用轻小型无人机系统环境试验方法
第1部分：总则
除另有规
验条件进行
70 ℃ 
GB/T38924.1一2020界定的以及下列术语和定义适用于本文件。
3.1
高温贮存温度storagehightemperature
在贮存或暴露于气候极值期间，设备预期通常会暴露于其中的最高的地面温度。
3.2
高温工作温度operatinghightemperature
设备正常暴露于其中并要求工作的最高温度。
定外，高
设备达到温
民用轻小型无人机系统环境试验方法
第3部分：高温试验
4.1.2
试验温度
4.1.3
试验持续时间
4.1.1
概述
2
规范性引用文件
4
试验条件
4.1
高温贮存试验条件
3
术语和定义
1
范国
用轻小型无人机系统环境试验方
第3部分：高温试验
R
389243-202
民用轻小型无人机系统环境试验方法
第3部分：高温试验
GB/T 38924.3—2020
民用轻小型无人机系统环境试验方法
第3部分：高温试验
1范围
GB/T 38924的本部分规定了民用轻小型无人机（起飞重量在0.25kg～150kg之间）系统（含飞行
器和地面站)高温试验的试验条件、试验设备和仪器、试验过程、试验中断和恢复、试验结果评定和试验
报告。
本部分适用于在使用过程中可能会受到高温环境影响的民用轻小型无人机系统，以此来确定民用
轻小型无人机系统对高温环境的耐受能力。
2规范性引用文件
下列文件对于本文件的应用是必不可少的。凡是注日期的引用文件，仅注日期的版本适用于本文
件。凡是不注日期的引用文件,其最新版本(包括所有的修改单)适用于本文件。
GB/T38924.1—2020 民用轻小型无人机系统环境试验方法第1部分：总则
3术语和定义
GB/T 38924.1一2020界定的以及下列术语和定义适用于本文件。
3.1
高温贮存温度storagehigh temperature
在贮存或暴露于气候极值期间，设备预期通常会暴露于其中的最高的地面温度。
3.2
高温工作温度operatinghigh temperature
设备正常暴露于其中并要求工作的最高温度。
4试验条件
4.1高温贮存试验条件
4.1.1概述
除另有规定外，高温贮存试验应按照下述试验条件进行。
4.1.2试验温度
按有关文件确定高温贮存试验温度,若相关文件未规定高温贮存试验温度,则推荐高温贮存温度为
70 ℃。
4.1.3试验持续时间
除另有规定外，高温贮存试验持续时间为受试设备达到温度稳定后再保持3 h。
"""

PAGE_MARKER_RE = re.compile(r"^\s*=+\s*Page\s+(\d+)\s*=+\s*$", re.IGNORECASE)
DECIMAL_HEADING_RE = re.compile(r"^\s*(\d+(?:\.\d+){0,5})[)\.、\s]+(.+?)\s*$")
CHAPTER_HEADING_RE = re.compile(r"^\s*第[一二三四五六七八九十百零0-9]+[章节部分篇]\s*(.+?)?\s*$")
COMMON_HEADING_PREFIXES = (
    "范围", "规范性引用文件", "术语和定义", "术语", "定义",
    "试验条件", "试验程序", "试验方法", "试验步骤", "试验过程", "试验要求",
    "试验中断", "中断", "恢复", "结果评定", "结果分析", "判定规则",
)

def is_heading_candidate(line):
    s = line.strip()
    if not s or len(s) > 80:
        return False
    if DECIMAL_HEADING_RE.match(s):
        return True
    if CHAPTER_HEADING_RE.match(s):
        return True
    if any(s.startswith(prefix) for prefix in COMMON_HEADING_PREFIXES):
        return True
    return False

lines = text.splitlines()
current_title = None
current_lines = []
sections = {}

for line in lines:
    if is_heading_candidate(line):
        if current_title:
            sections[current_title] = "\n".join(current_lines).strip()
        current_title = line.strip()
        current_lines = [line]
    else:
        current_lines.append(line)

if current_title:
    sections[current_title] = "\n".join(current_lines).strip()

for title, content in sections.items():
    print(f"\n=== {title} ===")
    print(content[:200])
    print("...")
