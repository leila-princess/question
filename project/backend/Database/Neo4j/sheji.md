1.	设备类型(二级)：EquipmentType
字段名	中文名	备注
Id	ID	非空ZBLX_0001
Name	名称	非空
Label	标签	顿号表示不同标签
Description	描述	
Type	分类	依据整表类别（一级：陆地）
Status	状态	启用/停用
User_id	上传用户	用户id
Create_time	上传时间	上传时自动获取时间
2.	设备节点：Equipment
字段名	中文名	备注
Id	ID	非空ZB_0001
Name	名称	非空
Label	标签	顿号表示不同标签
Type	类别	非空，依据二级类别
Description	描述	
Status	状态	启用/停用
User_id	上传用户	用户id
Create_time	上传时间	上传时自动获取时间

3.	环境节点：Environment
字段名	中文名	备注
Id	ID	非空HJ_0001
Name	名称	非空
Label	标签	顿号表示不同标签
Description	描述	
Type	分类	Doc_id链接到MySQL
Class	分级	Doc_id链接到MySQL
Limit	约束	Doc_id链接到MySQL
Request	要求	Doc_id链接到MySQL
Status	状态	启用/停用
User_id	上传用户	用户id
Create_time	上传时间	上传时自动获取时间
4.	项目节点：TestProject
字段名	中文名	备注
Id	ID	非空XM_0001
Name	名称	非空
Type	类别	8类环境
Label	标签	顿号表示不同标签
Description	描述	
Status	状态	启用/停用
User_id	上传用户	用户id
Create_time	上传时间	上传时自动获取时间
5.	指标节点：Indicator
字段名	中文名	备注
Id	ID	非空ZB_0001
Name	名称	非空
Label	标签	顿号表示不同标签
Description	描述	
Type	类别	距离、时间、速度、高度、其他
Status	状态	启用/停用
User_id	上传用户	用户id
Create_time	上传时间	上传时自动获取时间

6.	方法节点：TestMethod
字段名	中文名	备注
Id	ID	非空FF_0001
Name	名称	非空
Type	类别	8类环境+指标
Label	标签	顿号表示不同标签
Description	描述	
Source_name	来源	File_name
Source_id	来源_id	File_id
Status	状态	启用/停用
User_id	上传用户	用户id
Create_time	上传时间	上传时自动获取时间

7.	文件节点：File
字段名	中文名	备注
Id	ID	非空WJ_00001
Type	类型	标准/方案/规范/模板/其他
Label	标签	
Name	名称	
Chroma_id	关联向量数据库ID	
minIo_id	文件数据库id	
Image_true	是否有图片	True/false
Image_minIO_id	图片文件数据库id	[]数组格式
Status	状态	启用/停用
User_id	上传用户	用户id
Create_time	上传时间	上传时自动获取时间
关系：
设备类别->设备：包含HAS
设备->项目：关联CON
环境->项目：关联
设备->指标：关联
项目->方法：关联
指标->方法：关联
方法->文件：关联