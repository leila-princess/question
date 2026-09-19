向量数据库设计：
字段名	类型	说明	示例值
id	字符串	文档块的唯一标识符，由 doc_id 和 chunk_index 拼接而成	"std001_0"
document	字符串	文档块的实际文本内容（来自 content）	"无人机低温工作试验温度应为 -20℃..."
metadata	字典	附加的结构化元数据，内部包含以下子字段：

- doc_id：原文档ID
- title：文档标题
- chunk_index：块索引
- start_pos：起始位置（可能为 None）
- end_pos：结束位置（可能为 None）
- type: "标准"   #标准/方案/模板/规范/其他
- neo4j_testmethod_id:关联方法ID
- source_file：minIO#里面的原文件路径
- images:[] # 关联的图片路径
-table:[] # 转换成一个Markdown表格,同时纯文本转为向量
- formula:[] # 将LaTeX或MathML代码作为原始文本
- User_id: # 上传用户id
- Create_time:date # 上传时间

	{"doc_id":"std001", "title":"低温试验规范", "chunk_index":0, "start_pos":0, "end_pos":200,
"type”: "标准“,
neo4j_testmethod_id:FF_0001
"source_file": "minio://document-repo/标准/GB_T_38924.6-2020/origin.pdf",  
 "images": [                              
        "minio://document-repo/标准/GB_T_38924.6-2020/images/page5_table1.png"]},
“table”:[{存储机构化表格，具体页码，表几}]
“formula”: [{E = mc^2，页码，公式编号，
User_id
Create_time

}]
embedding	浮点数列表（向量）	由嵌入模型（paraphrase-multilingual-MiniLM-L12-v2）根据 document 字段自动生成并存储，不显式出现在 add() 参数中，但会在查询时用于相似度计算	[0.123, -0.456, ...] (384维)

