'use client';

import React, { useEffect, useState } from 'react';
import { Button, Empty, Input, List, Modal, Space, Spin, Tag, Typography, message, Tooltip, Popconfirm, Tabs } from 'antd';
import { PlusOutlined, SaveOutlined, EditOutlined, DeleteOutlined, MenuOutlined, ReloadOutlined, EyeOutlined } from '@ant-design/icons';
import MarkdownRenderer from '@/components/MarkdownRenderer';

const { Text } = Typography;

interface TemplateItem {
  id: string;
  name: string;
  description?: string;
  category?: string;
  version?: string;
}

interface TemplateSidebarProps {
  userId?: string;
  onApplyTemplate?: (template: { name: string; content: string; sections: Section[] }) => void;
}

interface Section {
  id: string;
  title: string;
  content: string;
}

const DEFAULT_SECTIONS: Section[] = [
  {
    id: 'sec-1',
    title: '1. 试验依据',
    content: `### 1.1 任务来源
\`{{任务来源描述，如年度试验工作计划}}\`

### 1.2 依据文件
列出用户提供的所有**方法标准**（如 GB/T、GJB 等）及设备技术规格来源，格式如下表：

| 序号 | 文件标识 | 文件名称 | 来源 |
|------|----------|----------|------|
| 1 | \`{{标准号}}\` | \`{{标准名称}}\` | \`{{发布机构}}\` |
| … | … | … | … |

**【检索】** 无（标准号由用户提供，后续从标准中检索具体参数）。`,
  },
  {
    id: 'sec-2',
    title: '2. 试验内容及方法',
    content: `### 2.1 试验项目

#### 2.1.1 \`{{环境名称}}\`
（须为用户所选环境的**具体全称**，禁止「试验环境」等泛称。）

##### 2.1.1.1 \`{{父级试验项目名}}\`
（方法正文写在本标题下；**禁止**「试验方法」或方法标准名独立标题行。）
- 有 SYNR：连续正文
- 无 SYNR：**（一）试验条件** / **（二）试验过程** / **（三）结果评定**（加粗行，勿用 # 标题）

###### 2.1.1.1.1 \`{{子级试验项目名}}\`
（方法正文同上。）

#### 2.1.2 \`{{第二个环境名}}\`
（仅多环境时出现；第二个**项目**须为 \`##### 2.1.1.2\`，不得占用 \`#### 2.1.2\`。）

### 2.2 边界性能指标
（仅当用户已选考核指标时出现。）

#### 2.2.1 \`{{父级考核指标名}}\`
（方法正文写在指标标题下，无独立方法名标题。）

##### 2.2.1.1 \`{{子级指标名}}\`
（方法正文同上。）`,
  },
];

const generateId = () => Math.random().toString(36).substring(2, 9);

export default function TemplateSidebar({ userId = 'admin', onApplyTemplate }: TemplateSidebarProps) {
  const [loading, setLoading] = useState(false);
  const [templates, setTemplates] = useState<TemplateItem[]>([]);
  const [keyword, setKeyword] = useState('');
  const [editing, setEditing] = useState(false);
  
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [schemeName, setSchemeName] = useState('');
  const [sections, setSections] = useState<Section[]>([]);
  
  const [editingSectionId, setEditingSectionId] = useState<string | null>(null);
  const [editTitle, setEditTitle] = useState('');
  const [editContent, setEditContent] = useState('');
  const [editTab, setEditTab] = useState<'edit' | 'preview'>('edit');

  const [draggedId, setDraggedId] = useState<string | null>(null);

  const loadTemplates = async () => {
    setLoading(true);
    try {
      const resp = await fetch(`/api/templates/list?user_id=${encodeURIComponent(userId)}`);
      const result = await resp.json();
      setTemplates((result.templates || []).filter((t: TemplateItem) => {
        if (!keyword.trim()) return true;
        const key = keyword.toLowerCase();
        return (t.name || '').toLowerCase().includes(key) || (t.description || '').toLowerCase().includes(key);
      }));
    } catch {
      setTemplates([]);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadTemplates();
  }, [keyword, userId]);

  const handleViewTemplate = async (id: string) => {
    console.log('Loading template:', id);
    try {
      const resp = await fetch(`/api/templates/detail/${encodeURIComponent(id)}`);
      const result = await resp.json();
      console.log('API response:', result);
      console.log('Template sections:', result.template?.sections);
      console.log('Template content:', result.template?.content);
      if (!result.success) {
        message.error(result.error || '模板加载失败');
        return;
      }
      const tpl = result.template || {};
      setName(tpl.metadata?.name || '');
      setDescription(tpl.metadata?.description || '');
      setSchemeName('');
      
      console.log('sections check:', tpl.sections, Array.isArray(tpl.sections), tpl.sections?.length);
      if (tpl.sections && Array.isArray(tpl.sections) && tpl.sections.length > 0) {
        console.log('Using sections from API');
        setSections(tpl.sections.map((s: any) => ({
          id: generateId(),
          title: s.title || '',
          content: s.content || ''
        })));
      } else if (tpl.content) {
        console.log('Using content');
        setSections([{
          id: generateId(),
          title: '内容',
          content: tpl.content
        }]);
      } else {
        console.log('Using DEFAULT_SECTIONS');
        setSections(JSON.parse(JSON.stringify(DEFAULT_SECTIONS)));
      }
      
      setEditing(true);
    } catch {
      message.error('模板加载失败');
    }
  };

  const handleCreate = () => {
    setName('新建模板');
    setDescription('基于标准结构的新模板');
    setSchemeName('');
    setSections(JSON.parse(JSON.stringify(DEFAULT_SECTIONS)));
    setEditing(true);
  };

  const handleApplyTemplate = () => {
    if (!name.trim()) {
      message.warning('请输入模板名称');
      return;
    }
    let currentSections = [...sections];
    if (editingSectionId) {
      currentSections = currentSections.map(s => 
        s.id === editingSectionId 
          ? { ...s, title: editTitle, content: editContent } 
          : s
      );
    }
    const fullContent = currentSections.map(s => `## ${s.title}\n${s.content}`).join('\n\n');
    onApplyTemplate?.({ name: name.trim(), content: fullContent, sections: currentSections });
    setEditing(false);
    message.success(`已应用模板：${name.trim()}`);
  };

  const handleSaveAsNew = async () => {
    if (!name.trim()) {
      message.warning('请输入模板名称');
      return;
    }
    
    let currentSections = [...sections];
    if (editingSectionId) {
      currentSections = currentSections.map(s => 
        s.id === editingSectionId 
          ? { ...s, title: editTitle, content: editContent } 
          : s
      );
      setSections(currentSections);
      setEditingSectionId(null);
    }

    const fullContent = currentSections.map(s => `## ${s.title}\n${s.content}`).join('\n\n');
    const payloadSections = currentSections.map((s, index) => ({
      index: index + 1,
      title: s.title,
      content: s.content
    }));

    try {
      const resp = await fetch('/api/templates/create', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          user_id: userId,
          name: name.trim(),
          description: description.trim(),
          category: '辅助设计',
          content: fullContent,
          sections: payloadSections
        }),
      });
      const result = await resp.json();
      if (!result.success) {
        message.error(result.error || '保存失败');
        return;
      }
      message.success('已保存为新模板');
      setEditing(false);
      loadTemplates();
    } catch {
      message.error('保存失败');
    }
  };

  const handleUseDefault = () => {
    setSections(JSON.parse(JSON.stringify(DEFAULT_SECTIONS)));
    setEditingSectionId(null);
  };

  const handleAddSection = () => {
    const newSection = {
      id: generateId(),
      title: '新章节',
      content: ''
    };
    setSections([...sections, newSection]);
    startEditSection(newSection);
  };

  const startEditSection = (section: Section) => {
    setEditingSectionId(section.id);
    setEditTitle(section.title);
    setEditContent(section.content);
    setEditTab('edit');
  };

  const handleSaveSection = () => {
    if (!editingSectionId) return;
    setSections(sections.map(s => 
      s.id === editingSectionId 
        ? { ...s, title: editTitle, content: editContent } 
        : s
    ));
    setEditingSectionId(null);
  };

  const handleCancelEditSection = () => {
    setEditingSectionId(null);
  };

  const handleDeleteSection = (id: string) => {
    setSections(sections.filter(s => s.id !== id));
    if (editingSectionId === id) {
      setEditingSectionId(null);
    }
  };

  const handleDragStart = (e: React.DragEvent, id: string) => {
    setDraggedId(id);
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', id);
    
    if (e.currentTarget instanceof HTMLElement) {
      e.currentTarget.style.opacity = '0.5';
    }
  };

  const handleDragEnd = (e: React.DragEvent) => {
    setDraggedId(null);
    if (e.currentTarget instanceof HTMLElement) {
      e.currentTarget.style.opacity = '1';
    }
  };

  const handleDragOver = (e: React.DragEvent, id: string) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
  };

  const handleDrop = (e: React.DragEvent, targetId: string) => {
    e.preventDefault();
    if (!draggedId || draggedId === targetId) return;

    const draggedIndex = sections.findIndex(s => s.id === draggedId);
    const targetIndex = sections.findIndex(s => s.id === targetId);

    if (draggedIndex === -1 || targetIndex === -1) return;

    const newSections = [...sections];
    const [draggedItem] = newSections.splice(draggedIndex, 1);
    newSections.splice(targetIndex, 0, draggedItem);

    setSections(newSections);
  };

  const styles = {
    container: { height: '100%', display: 'flex', flexDirection: 'column' as const },
    header: { padding: '8px 12px', borderBottom: '1px solid #f0f0f0' },
    listContainer: { flex: 1, overflow: 'auto' as const },
    listItem: { cursor: 'pointer', padding: '8px 12px' },
    modalBody: { maxHeight: '70vh', overflowY: 'auto' as const, paddingRight: '8px' },
    card: { marginBottom: '12px', border: '1px solid #f0f0f0', borderRadius: '8px' },
    cardHeader: { 
      display: 'flex', 
      alignItems: 'center', 
      padding: '12px', 
      backgroundColor: '#fafafa', 
      borderBottom: '1px solid #f0f0f0', 
      borderTopLeftRadius: '8px', 
      borderTopRightRadius: '8px' 
    },
    dragHandle: { cursor: 'grab', marginRight: '12px', color: '#999' },
    cardTitle: { flex: 1, fontWeight: 'bold', margin: 0 },
    cardActions: { display: 'flex', gap: '8px' },
    cardBody: { padding: '12px' },
    previewText: { 
      color: '#666', 
      fontSize: '13px', 
      display: '-webkit-box', 
      WebkitLineClamp: 3, 
      WebkitBoxOrient: 'vertical' as const, 
      overflow: 'hidden' 
    },
    addCard: { 
      border: '1px dashed #d9d9d9', 
      borderRadius: '8px', 
      padding: '16px', 
      textAlign: 'center' as const, 
      cursor: 'pointer', 
      backgroundColor: '#fafafa', 
      color: '#1890ff', 
      transition: 'all 0.3s' 
    }
  };

  return (
    <div style={styles.container}>
      <div style={styles.header}>
        <Space direction="vertical" size={8} style={{ width: '100%' }}>
          <Input
            size="small"
            placeholder="搜索模板"
            value={keyword}
            onChange={(e) => setKeyword(e.target.value)}
          />
          <Button size="small" type="primary" icon={<PlusOutlined />} onClick={handleCreate} block>
            新建模板
          </Button>
        </Space>
      </div>
      <div style={styles.listContainer}>
        {loading ? (
          <div style={{ textAlign: 'center', padding: 16 }}><Spin size="small" /></div>
        ) : templates.length === 0 ? (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无模板" />
        ) : (
          <List
            size="small"
            dataSource={templates}
            renderItem={(item) => (
              <List.Item
                style={styles.listItem}
                onClick={() => handleViewTemplate(item.id)}
              >
                <List.Item.Meta
                  title={<Text style={{ fontSize: 12 }}>{item.name}</Text>}
                  description={
                    <Space size={4}>
                      <Tag>{item.category || '未分类'}</Tag>
                      <Text type="secondary" style={{ fontSize: 11 }}>v{item.version || '1.0'}</Text>
                    </Space>
                  }
                />
              </List.Item>
            )}
          />
        )}
      </div>

      <Modal
        title={
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', paddingRight: '24px' }}>
            <span>模板编辑</span>
            <Button size="small" icon={<ReloadOutlined />} onClick={handleUseDefault}>
              使用默认模板
            </Button>
          </div>
        }
        open={editing}
        onCancel={() => setEditing(false)}
        width={1000}
        onOk={handleSaveAsNew}
        okText="保存为新模板"
        okButtonProps={{ icon: <SaveOutlined /> }}
        maskClosable={false}
        footer={
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <Space>
              <Button icon={<SaveOutlined />} onClick={handleSaveAsNew}>保存模板</Button>
              {onApplyTemplate && (
                <Button type="primary" onClick={handleApplyTemplate}>应用此模板</Button>
              )}
            </Space>
            <Button onClick={() => setEditing(false)}>取消</Button>
          </div>
        }
      >
        <div style={styles.modalBody}>
          <Space direction="vertical" size={16} style={{ width: '100%', marginBottom: '24px' }}>
            <div>
              <div style={{ marginBottom: '8px', fontWeight: 'bold' }}>模板名称</div>
              <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="输入模板名称" />
            </div>
            <div>
              <div style={{ marginBottom: '8px', fontWeight: 'bold' }}>模板描述</div>
              <Input value={description} onChange={(e) => setDescription(e.target.value)} placeholder="输入模板描述" />
            </div>
            <div>
              <div style={{ marginBottom: '8px', fontWeight: 'bold' }}>方案名称 <Text type="secondary" style={{ fontWeight: 'normal', fontSize: 12 }}>（应用模板后作为方案名）</Text></div>
              <Input value={schemeName} onChange={(e) => setSchemeName(e.target.value)} placeholder="例如：无人机A型湿热环境试验方案" />
            </div>
          </Space>

          <div style={{ marginBottom: '16px', fontWeight: 'bold', fontSize: '16px' }}>章节内容</div>
          
          {sections.map((section) => {
            const isEditing = editingSectionId === section.id;
            
            return (
              <div
                key={section.id}
                draggable={!isEditing}
                onDragStart={(e) => handleDragStart(e, section.id)}
                onDragEnd={handleDragEnd}
                onDragOver={(e) => handleDragOver(e, section.id)}
                onDrop={(e) => handleDrop(e, section.id)}
                style={{
                  ...styles.card,
                  opacity: draggedId === section.id ? 0.5 : 1,
                  border: draggedId === section.id ? '1px dashed #1890ff' : styles.card.border
                }}
              >
                <div style={styles.cardHeader}>
                  {!isEditing && (
                    <div style={styles.dragHandle}>
                      <MenuOutlined />
                    </div>
                  )}
                  <div style={styles.cardTitle}>
                    {isEditing ? '编辑章节' : section.title}
                  </div>
                  {!isEditing && (
                    <div style={styles.cardActions}>
                      <Tooltip title="预览">
                        <Button 
                          type="text" 
                          icon={<EyeOutlined />} 
                          onClick={() => startEditSection(section)} 
                          size="small"
                        />
                      </Tooltip>
                      <Tooltip title="编辑">
                        <Button 
                          type="text" 
                          icon={<EditOutlined />} 
                          onClick={() => startEditSection(section)} 
                          size="small"
                        />
                      </Tooltip>
                      <Popconfirm
                        title="确定要删除此章节吗？"
                        onConfirm={() => handleDeleteSection(section.id)}
                        okText="确定"
                        cancelText="取消"
                      >
                        <Tooltip title="删除">
                          <Button type="text" danger icon={<DeleteOutlined />} size="small" />
                        </Tooltip>
                      </Popconfirm>
                    </div>
                  )}
                </div>
                
                <div style={styles.cardBody}>
                  {isEditing ? (
                    <Space direction="vertical" size={12} style={{ width: '100%' }}>
                      <Input 
                        value={editTitle} 
                        onChange={(e) => setEditTitle(e.target.value)} 
                        placeholder="章节标题" 
                      />
                      <Tabs
                        activeKey={editTab}
                        onChange={(k) => setEditTab(k as 'edit' | 'preview')}
                        size="small"
                        items={[
                          {
                            key: 'edit',
                            label: '编辑',
                            children: (
                              <Input.TextArea
                                value={editContent}
                                onChange={(e) => setEditContent(e.target.value)}
                                autoSize={{ minRows: 6, maxRows: 20 }}
                                placeholder="章节内容 (Markdown格式)"
                                style={{ fontFamily: 'monospace', fontSize: 13 }}
                              />
                            ),
                          },
                          {
                            key: 'preview',
                            label: '预览',
                            children: (
                              <div style={{ 
                                maxHeight: 400, 
                                overflow: 'auto', 
                                padding: '12px', 
                                background: '#fafafa', 
                                borderRadius: 6,
                                border: '1px solid #f0f0f0'
                              }}>
                                <MarkdownRenderer content={editContent || '*暂无内容*'} />
                              </div>
                            ),
                          },
                        ]}
                      />
                      <Space>
                        <Button type="primary" onClick={handleSaveSection}>保存章节</Button>
                        <Button onClick={handleCancelEditSection}>取消</Button>
                      </Space>
                    </Space>
                  ) : (
                    <div style={{ maxHeight: 200, overflow: 'auto' }}>
                      <MarkdownRenderer content={section.content || '*暂无内容*'} />
                    </div>
                  )}
                </div>
              </div>
            );
          })}

          <div 
            style={styles.addCard} 
            onClick={handleAddSection}
            onMouseOver={(e) => e.currentTarget.style.borderColor = '#1890ff'}
            onMouseOut={(e) => e.currentTarget.style.borderColor = '#d9d9d9'}
          >
            <PlusOutlined style={{ marginRight: '8px' }} /> 添加新章节
          </div>
        </div>
      </Modal>
    </div>
  );
}
