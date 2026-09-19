# {{设备名称}}环境及边界性能试验方案

## 第一章节 试验依据

### 1.1 任务来源
{{任务来源}}

### 1.2 依据文件
| 序号 | 文件标识 | 文件名称 | 来源 |
|------|----------|----------|------|
{%- for std in 依据标准列表 %}
| {{ loop.index }} | {{ std.name }} | {{ std.name }} | {{ std.source }} |
{%- endfor %}
{%- if not 依据标准列表 %}
| 1 | 待补充 | 待补充 | 待补充 |
{%- endif %}

## 第二章节 试验内容与方法

### 2.1 试验项目

{% for env in 环境列表 %}
#### {{ env.number }} {{ env.name }}

{# 环境配置正文（分类/分级/约束等）已按产品要求暂时不输出；标题下直接为试验项目与方法。原块保留备恢复：
**分类**：{{ env.settings.type | safe }}
**分级**：{{ env.settings.class | safe }}
**约束**：{{ env.settings.limit | safe }}
**组合**：{{ env.settings.combination | safe }}
**要求**：{{ env.settings.request | safe }}
#}

{% for project in env.projects %}
##### {{ project.number }} {{ project.name }}

{% for method in project.methods %}
{{ method.SYNR }}

{% endfor %}
{% for child in project.children %}
###### {{ child.number }} {{ child.name }}

{% for method in child.methods %}
{{ method.SYNR }}

{% endfor %}
{% endfor %}
{% endfor %}
{% endfor %}

### 2.2 边界性能指标

{% for indicator in 边界性能指标 %}
#### {{ indicator.number }} {{ indicator.name }}

{% for method in indicator.methods %}
{{ method.SYNR }}

{% endfor %}
{% for child in indicator.children %}
##### {{ child.number }} {{ child.name }}

{% for method in child.methods %}
{{ method.SYNR }}

{% endfor %}
{% endfor %}
{% endfor %}
