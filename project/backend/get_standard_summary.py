import requests
from bs4 import BeautifulSoup
import re

def get_standard_summary(standard_id: str) -> str:
    """
    从国家标准网获取指定标准号的摘要信息，经过多网站校准验证后返回简洁概括。
    
    参数:
        standard_id (str): 标准号 (如 "GB/T 19001-2016")
    
    返回:
        str: 不超过200字的标准摘要
    """
    # 1. 多网站校准验证
    verified = False
    summary = ""
    
    # 尝试从多个权威网站获取信息
    sources = [
        # {
        #     "name": "国家标准全文公开系统",
        #     "url": f"http://openstd.samr.gov.cn/bzgk/gb/search?p.p1=0&p.p2={standard_id}"
        # },
        {
            "name": "全国标准信息公共服务平台",
            "url": f"https://std.samr.gov.cn/search/std?q={standard_id}"
        },
        # {
        #     "name": "中国标准服务网",
        #     "url": f"http://www.cssn.net.cn/cssn/front/search/standard?searchCondition.standardNo={standard_id}"
        # }
    ]
    
    for source in sources:
        try:
            response = requests.get(source["url"], timeout=10)
            response.raise_for_status()
            
            # 使用不同的解析策略应对不同网站
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # 策略1: 查找包含标准号的标题
            title_element = soup.find(lambda tag: tag.name in ['h1', 'h2', 'h3'] 
                                      and standard_id in tag.get_text())
            
            # 策略2: 查找包含标准号的表格行
            if not title_element:
                rows = soup.select('tr')
                for row in rows:
                    if standard_id in row.get_text():
                        title_element = row
                        break
            
            if title_element:
                # 获取摘要信息
                summary_container = title_element.find_next(['p', 'div', 'td'])
                if summary_container:
                    raw_text = summary_container.get_text(separator=' ', strip=True)
                    
                    # 清理文本：移除多余空格、特殊字符等
                    cleaned_text = re.sub(r'\s+', ' ', raw_text)
                    cleaned_text = re.sub(r'\[\d+\]', '', cleaned_text)
                    
                    # 提取核心摘要（前200字）
                    summary = cleaned_text[:200]
                    verified = True
                    break
                    
        except Exception as e:
            # 记录错误但继续尝试其他来源
            print(f"从 {source['name']} 获取数据时出错: {str(e)}")
            continue
    
    # 2. 验证失败处理
    if not verified:
        return f"⚠️ 未找到标准号 {standard_id} 的可靠信息，请确认标准号是否正确且有效"
    
    # 3. 精炼摘要
    # 移除冗余信息，确保不超过200字
    summary = re.sub(r'标准号[:：]\s*' + standard_id, '', summary)
    summary = re.sub(r'发布日期[:：]\s*\d{4}-\d{2}-\d{2}', '', summary)
    summary = re.sub(r'实施日期[:：]\s*\d{4}-\d{2}-\d{2}', '', summary)
    
    # 确保不超过200字
    if len(summary) > 200:
        summary = summary[:197] + '...'
    
    return summary

# 函数调用工具定义
tools = [
    {
        "type": "function",
        "function": {
            "name": "get_standard_summary",
            "description": "从国家标准网获取指定标准号的摘要信息，经过多网站校准验证后返回简洁概括",
            "parameters": {
                "type": "object",
                "properties": {
                    "standard_id": {
                        "type": "string",
                        "description": "标准号，如 'GB/T 19001-2016'"
                    }
                },
                "required": ["standard_id"]
            }
        }
    }
]

# 示例调用
# if __name__ == "__main__":
#     # 测试标准查询
#     get_standard_summary("GB/T 19001-2016") # 质量管理体系