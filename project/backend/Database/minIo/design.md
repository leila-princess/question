文件数据库设计
文件数据库：用一个 Bucket（如 document-repo），通过路径前缀区分五类文件。这种方式简单、易于管理。
1.整体结构：
minio-bucket/
├── 标准/                    # 五类文件的顶层目录
│   ├── GB_T_38924.6-2020/  # 每个标准一个文件夹
│   │   ├── origin.pdf      # 原始PDF文件
│   │   ├── metadata.json  # 文件级元数据
│   │   └── images/         # 该标准下提取的图片
│   │       ├── page5_fig1.png
│   │       └── page12_table1.png
│   ├── ISO_9001-2021/
│   │   ├── origin.pdf
│   │   └── images/
│   └── ...
├── 方案/
│   └── ...
├── 模板/
│   └── ...
├── 规范/
│   └── ...
└── 其他/
    └── ...


2.文件保存路径：
路径前缀	说明	示例
标准/	存放各类标准文档	标准／类别/GB_T_38924.6-2020/origin.pdf
方案/	存放方案类文档	方案/某项目方案/origin.docx
模板/	存放模板文件	模板/模板1/origin.docx
规范/	存放规范文件	规范/规范1/origin.pdf
其他/	其他杂项	其他/文档/origin.pdf


原始文件（origin.pdf）的元数据
X-Amz-Meta-Doc-Id: std006                    # 唯一文档ID
X-Amz-Meta-Title: GB/T 38924.6-2020          # 标准名称
X-Amz-Meta-Category: 标准                    # 五类分类
X-Amz-Meta-Abstract: 摘要                    # 摘要
X-Amz-Meta-File-Type: pdf                    # 文件类型
X-Amz-Meta-File-Label:标签                   # 文件标签
X-Amz-Meta-File-ICS: xx                  # 国际分类号
X-Amz-Meta-File-CCS: xx                  # 中国分类号
X-Amz-Meta-Source-Path: 标准/GB_T_38924.6-2020/origin.pdf  # 自身路径
X-Amz-Meta-Upload-Userid: user_id # 上传用户
X-Amz-Meta-Upload-Time: 2026-01-15T10:30:00Z # 上传时间
X-Amz-Meta-File-Size: 2457600                # 文件大小(bytes)
X-Amz-Meta-Checksum: md5:xxxxx               # 文件校验和

图片文件（page5_table1.png）的元数据
X-Amz-Meta-Doc-Id: std006                    # 关联的文档ID
X-Amz-Meta-Source-File: 标准/GB_T_38924.6-2020/origin.pdf  # 来源文件
X-Amz-Meta-Page-Number: 5                    # 所在页码
X-Amz-Meta-Image-Type: table                 # 图片类型：table/figure/logo/other
X-Amz-Meta-Description: 振动试验类别表        # 图片描述
















