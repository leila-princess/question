#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
测试改进后的试验方法提取逻辑
"""
import sys
import os
import json

# 切换到 GetTestMethod 目录
os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from parser import parse_standard_file
from extractor import extract_document_methods

def main():
    pdf_path = r"F:\D.c-system\项目\2601-方案生成项目\标准\12544-2012-gbt-e-300.pdf"
    
    if not os.path.exists(pdf_path):
        print(f"文件不存在: {pdf_path}")
        return
    
    print("=" * 80)
    print(f"测试文件: {pdf_path}")
    print("=" * 80)
    
    # 1. 解析PDF
    print("\n[1/3] 解析PDF文件...")
    parsed_doc = parse_standard_file(file_path=pdf_path)
    
    print(f"  - 文件名: {parsed_doc.get('file_name')}")
    print(f"  - 章节数: {len(parsed_doc.get('sections', []))}")
    print(f"  - OCR模式: {parsed_doc.get('ocr_used')}")
    
    # 打印章节预览
    print("\n  章节预览:")
    for sec in parsed_doc.get('sections', [])[:10]:
        title = sec.get('title', '')
        text_len = len(str(sec.get('text', '')))
        print(f"    - {title} (正文长度: {text_len})")
    
    # 2. 提取方法
    print("\n[2/3] 提取试验方法...")
    extraction_data = extract_document_methods(parsed_doc=parsed_doc)
    
    methods = extraction_data.get('methods', [])
    print(f"  - 发现方法数: {len(methods)}")
    
    # 3. 打印提取结果
    print("\n[3/3] 提取结果详情:")
    print("=" * 80)
    
    for idx, method in enumerate(methods, 1):
        print(f"\n方法 {idx}:")
        print(f"  名称: {method.get('name', 'N/A')}")
        print(f"  类型: {method.get('type', 'N/A')}")
        print(f"  描述: {method.get('description', 'N/A')[:100]}...")
        
        print(f"\n  【试验条件】(sytj):")
        sytj = method.get('sytj', {})
        sytj_text = str(sytj.get('text', ''))
        if sytj_text and sytj_text != '文档未明确说明':
            print(f"    长度: {len(sytj_text)} 字符")
            print(f"    预览: {sytj_text[:200]}...")
        else:
            print(f"    内容: {sytj_text or '(空)'}")
        if sytj.get('tables_html'):
            print(f"    表格数: {len(sytj['tables_html'])}")
        
        print(f"\n  【试验过程】(sygc):")
        sygc = method.get('sygc', {})
        sygc_text = str(sygc.get('text', ''))
        if sygc_text and sygc_text != '文档未明确说明':
            print(f"    长度: {len(sygc_text)} 字符")
            print(f"    预览: {sygc_text[:200]}...")
        else:
            print(f"    内容: {sygc_text or '(空)'}")
        
        print(f"\n  【结果评定】(jgpd):")
        jgpd = method.get('jgpd', {})
        jgpd_text = str(jgpd.get('text', ''))
        if jgpd_text and jgpd_text != '文档未明确说明':
            print(f"    长度: {len(jgpd_text)} 字符")
            print(f"    预览: {jgpd_text[:200]}...")
        else:
            print(f"    内容: {jgpd_text or '(空)'}")
        
        print("\n" + "=" * 80)
    
    # 保存完整结果到文件
    output_path = "test_extract_output.json"
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(extraction_data, f, ensure_ascii=False, indent=2)
    print(f"\n完整结果已保存到: {output_path}")

if __name__ == '__main__':
    main()
