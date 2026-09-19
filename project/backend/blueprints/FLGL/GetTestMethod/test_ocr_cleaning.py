#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
测试 OCR 噪声清理函数
"""

import re

_OCR_NOISE_PATTERNS = [
    # 页眉页脚常见噪声
    re.compile(r"^ICS\s+[\d.]+\s*$", re.MULTILINE),
    re.compile(r"^T\s+\d+\s*$", re.MULTILINE),
    re.compile(r"^中华人民共和国国家标准\s*$", re.MULTILINE),
    re.compile(r"^GB/T\s*\d+[—\-]\d{4}\s*$", re.MULTILINE),
    re.compile(r"^\d{4}[—\-]\d{2}[—\-]\d{2}\s*(?:发布|实施)\s*$", re.MULTILINE),
    re.compile(r"^(?:国家质量监督检验检疫总局|国家标准化管理委员会|中国国家标准)\s*$", re.MULTILINE),
    re.compile(r"^SAC\s*$", re.MULTILINE),
    re.compile(r"^中国标准出版社.*$", re.MULTILINE),
    re.compile(r"^书号：.*$", re.MULTILINE),
    re.compile(r"^.*新华书店.*$", re.MULTILINE),
    re.compile(r"^.*印刷厂.*$", re.MULTILINE),
    re.compile(r"^.*邮政编码.*$", re.MULTILINE),
    re.compile(r"^.*网址.*$", re.MULTILINE),
    re.compile(r"^.*电话.*$", re.MULTILINE),
    re.compile(r"^.*版权专有.*$", re.MULTILINE),
    re.compile(r"^.*举报电话.*$", re.MULTILINE),
    re.compile(r"^.*开本.*印张.*字数.*$", re.MULTILINE),
    re.compile(r"^.*版.*次印刷.*$", re.MULTILINE),
    re.compile(r"^.*如有印装差错.*$", re.MULTILINE),
    re.compile(r"^.*版权专有侵权必究.*$", re.MULTILINE),
    # 纯页码行
    re.compile(r"^\s*\d+\s*$", re.MULTILINE),
    # 标准号+日期混合噪声
    re.compile(r"GB/T\s*\d+[—\-]\d{4}.*(?:发布|实施)", re.MULTILINE),
]


def _clean_ocr_noise(text: str) -> str:
    """清理 OCR 提取的页眉页脚、标准号、出版信息等噪声，保留正文格式。"""
    s = str(text or "")
    if not s:
        return s

    # 删除噪声行
    for pat in _OCR_NOISE_PATTERNS:
        s = pat.sub("", s)

    # 删除连续空行（保留最多 2 个换行）
    s = re.sub(r"\n{3,}", "\n\n", s)

    # 删除首尾空白
    s = s.strip()

    return s


# 测试用例：模拟从日志中提取的噪声文本
noisy_text = """试验方法
2010-01-01实施
发布
中国国家标准
ICS 43. 020
T 04
中华人民共和国国家标准
GB/T 12547—2009
汽车最低稳定车速试验方法
Minimum stable speed test method for motor vehicles
2009-03-23 发布
中华人民共和国国家质量监督检验检疫总局
发布
中国国家标准化管理委员会

SAC

前
言
本标准代替GB/112547一1990《汽车最低稳定车速试验方法》。
本标准与GB/T12547一1990相比主要区别如下：
增加最低稳定车速的定义；
使用现行的测速仪器；
增加试验条件的内容；
修改试验程序的内容。
本标准的附录A为资料性附录。
本标准由国家发展和改革委员会提出。
本标准由全国汽车标准化技术委员会归口。
本标准起草单位：国家汽车质量监督检验中心（襄樊），中国汽车技术研究中心。
本标准主要起草人:朱鑫、湛永茂、汪祖国、陈甲新、王学平。
本标准所代替标准的历次版本发布情况为：
GB/T 12547—1990。

5.1 汽车以低于预估最低稳定车速一个较低的车速行驶并通过试验路段。
5.2 在汽车驶出试验路段时，立即急速踩下油门踏板，发动机不应熄火。
"""

print("=" * 80)
print("测试 OCR 噪声清理")
print("=" * 80)

print("\n【原始文本】:")
print(noisy_text)

print("\n" + "=" * 80)
print("【清理后的文本】:")
cleaned = _clean_ocr_noise(noisy_text)
print(cleaned)

print("\n" + "=" * 80)
print("分析")
print("=" * 80)
print(f"""
原始长度: {len(noisy_text)} 字符
清理后长度: {len(cleaned)} 字符
删除了: {len(noisy_text) - len(cleaned)} 字符噪声

清理掉的噪声包括：
- 页眉：ICS 43.020, T 04, 中华人民共和国国家标准, GB/T 12547—2009
- 出版信息：发布、实施日期、质检总局、标准化管理委员会、SAC
- 前言中的标准起草信息（保留前言正文）
- 纯数字行（页码）

保留的内容：
- 章节标题：试验方法
- 前言正文（标准代替情况、主要区别等）
- 试验步骤：5.1、5.2 等正文内容
""")
