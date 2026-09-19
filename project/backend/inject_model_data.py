import json
import pymysql
from config import Config

def inject_model_data():
    if not Config.MYSQL_POOL:
        print("MySQL未连接")
        return
    
    try:
        with Config.MYSQL_POOL.connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT COUNT(*) FROM mxgl_models")
                count = cursor.fetchone()[0]
                if count > 0:
                    print(f"表中已有 {count} 条记录，跳过注入")
                    return
                
                models = [
                    {
                        "model_key": "llm",
                        "model_name": "qwen-plus",
                        "model_type": "llm",
                        "model_path": "",
                        "api_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                        "api_key": "REPLACE_WITH_YOUR_API_KEY",
                        "description": "通义千问Plus大语言模型",
                        "is_local": False,
                        "is_active": True
                    },
                    {
                        "model_key": "embedding",
                        "model_name": "paraphrase-multilingual-MiniLM-L12-v2",
                        "model_type": "embedding",
                        "model_path": r"C:\Users\hyx\.cache\huggingface\hub\models--sentence-transformers--paraphrase-multilingual-MiniLM-L12-v2",
                        "api_url": "",
                        "api_key": "",
                        "description": "多语言语义搜索模型",
                        "is_local": True,
                        "is_active": True
                    },
                    {
                        "model_key": "vector_retrieval",
                        "model_name": "paraphrase-multilingual-MiniLM-L12-v2",
                        "model_type": "embedding",
                        "model_path": r"C:\Users\hyx\.cache\huggingface\hub\models--sentence-transformers--paraphrase-multilingual-MiniLM-L12-v2",
                        "api_url": "",
                        "api_key": "",
                        "description": "向量检索模型",
                        "is_local": True,
                        "is_active": True
                    },
                    {
                        "model_key": "cross_encoder",
                        "model_name": "ms-marco-MiniLM-L6-v2",
                        "model_type": "reranker",
                        "model_path": r"C:\Users\hyx\antd-demo1\ANZHUANGBAO\ms-marco-MiniLM-L6-v2",
                        "api_url": "",
                        "api_key": "",
                        "description": "交叉编码器重排序模型",
                        "is_local": True,
                        "is_active": True
                    }
                ]
                
                for model in models:
                    cursor.execute("""
                        INSERT INTO mxgl_models 
                        (model_key, model_name, model_type, model_path, api_url, api_key, description, is_local, is_active)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """, (
                        model["model_key"], model["model_name"], model["model_type"],
                        model["model_path"], model["api_url"], model["api_key"],
                        model["description"], model["is_local"], model["is_active"]
                    ))
                
                conn.commit()
                print(f"成功注入 {len(models)} 条模型数据")
                
    except Exception as e:
        print(f"注入模型数据失败: {e}")

if __name__ == "__main__":
    inject_model_data()
