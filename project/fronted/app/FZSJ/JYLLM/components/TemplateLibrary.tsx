'use client';

import React, { useState, useEffect } from 'react';
import {
  Card, List, Button, Space, Tag, Typography, Spin, Empty, Tabs,
  Input, Tooltip, Badge, Descriptions, Divider, Modal, message
} from 'antd';
import {
  FileTextOutlined, PlusOutlined, EditOutlined, DeleteOutlined,
  CopyOutlined, EyeOutlined, CheckCircleOutlined, StarOutlined,
  HistoryOutlined, SearchOutlined, FileMarkdownOutlined
} from '@ant-design/icons';
import { useRouter } from 'next/navigation';

const { Title, Text, Paragraph } = Typography;
const { Search } = Input;
const { TabPane } = Tabs;

interface Template {
  sections: any;
  variables: any;
  id: string;
  name: string;
  description: string;
  category: string;
  version: string;
  is_default?: boolean;
  is_system?: boolean;
  created_at: string;
  variables_count: number;
  sections_count: number;
  content?: string;
}

interface TemplateLibraryProps {
  userId: string;
  onSelect?: (template: Template) => void;
  showSelectButton?: boolean;
}

export default function TemplateLibrary({ userId, onSelect, showSelectButton = true }: TemplateLibraryProps) {
  const router = useRouter();
  const [activeTab, setActiveTab] = useState('my');
  const [templates, setTemplates] = useState<Template[]>([]);
  const [systemTemplates, setSystemTemplates] = useState<Template[]>([]);
  const [loading, setLoading] = useState(false);
  const [searchKeyword, setSearchKeyword] = useState('');
  const [selectedTemplate, setSelectedTemplate] = useState<Template | null>(null);
  const [previewModalOpen, setPreviewModalOpen] = useState(false);

  useEffect(() => {
    loadTemplates();
  }, [activeTab, searchKeyword]);

  const loadTemplates = async () => {
    setLoading(true);
    try {
      // 加载用户模板
      if (activeTab === 'my') {
        const response = await fetch(`/api/templates/list?user_id=${userId}&keyword=${searchKeyword}`);
        const result = await response.json();
        if (result.success) {
          setTemplates(result.items || []);
        }
      }
      
      // 加载系统模板
      if (activeTab === 'system') {
        const response = await fetch('/api/templates/system');
        const result = await response.json();
        if (result.success) {
          setSystemTemplates(result.items || []);
        }
      }
    } catch (e) {
      console.error('Load templates error:', e);
    } finally {
      setLoading(false);
    }
  };

  const handleCreateDefault = async () => {
    try {
      const response = await fetch('/api/templates/default', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: userId }),
      });
      const result = await response.json();
      if (result.success) {
        message.success('默认模板已创建');
        loadTemplates();
      }
    } catch (e) {
      message.error('创建失败');
    }
  };

  const handleDelete = async (id: string) => {
    try {
      const response = await fetch(`/api/templates/delete/${id}?user_id=${userId}`, {
        method: 'DELETE',
      });
      const result = await response.json();
      if (result.success) {
        message.success('已删除');
        loadTemplates();
      }
    } catch (e) {
      message.error('删除失败');
    }
  };

  const handlePreview = async (template: Template) => {
    try {
      const response = await fetch(`/api/templates/detail/${template.id}`);
      const result = await response.json();
      if (result.success) {
        setSelectedTemplate(result.template);
        setPreviewModalOpen(true);
      }
    } catch (e) {
      message.error('加载详情失败');
    }
  };

  const renderTemplateItem = (template: Template, isSystem: boolean = false) => (
    <List.Item
      actions={[
        <Tooltip title="预览" key="preview">
          <Button type="text" icon={<EyeOutlined />} onClick={() => handlePreview(template)} />
        </Tooltip>,
        showSelectButton && (
          <Button
            key="select"
            type="primary"
            size="small"
            icon={<CheckCircleOutlined />}
            onClick={() => onSelect?.(template)}
          >
            选择
          </Button>
        ),
        !isSystem && (
          <Tooltip title="编辑" key="edit">
            <Button 
              type="text" 
              icon={<EditOutlined />}
              onClick={() => router.push(`/FZSJ/template?id=${template.id}`)}
            />
          </Tooltip>
        ),
        !isSystem && !template.is_default && (
          <Tooltip title="删除" key="delete">
            <Button 
              type="text" 
              danger
              icon={<DeleteOutlined />}
              onClick={() => handleDelete(template.id)}
            />
          </Tooltip>
        ),
      ].filter(Boolean)}
    >
      <List.Item.Meta
        avatar={
          <div style={{ 
            width: 48, 
            height: 48, 
            background: isSystem ? '#f0f5ff' : '#f6ffed',
            borderRadius: 8,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center'
          }}>
            <FileTextOutlined style={{ fontSize: 24, color: isSystem ? '#1890ff' : '#52c41a' }} />
          </div>
        }
        title={
          <Space>
            <Text strong>{template.name}</Text>
            {template.is_default && <Tag color="blue">默认</Tag>}
            {isSystem && <Tag color="orange">系统</Tag>}
          </Space>
        }
        description={
          <Space direction="vertical" size={0}>
            <Text type="secondary" ellipsis style={{ maxWidth: 300 }}>
              {template.description}
            </Text>
            <Space size="small">
              <Tag>{template.category}</Tag>
              <Text type="secondary" style={{ fontSize: 12 }}>
                {template.variables_count} 变量 · {template.sections_count} 章节
              </Text>
            </Space>
          </Space>
        }
      />
    </List.Item>
  );

  return (
    <div style={{ padding: '0 0 24px' }}>
      <Tabs activeKey={activeTab} onChange={setActiveTab}>
        <TabPane 
          tab={
            <span>
              <FileTextOutlined /> 我的模板
              {templates.length > 0 && <Badge count={templates.length} style={{ marginLeft: 4 }} />}
            </span>
          } 
          key="my"
        >
          <div style={{ marginBottom: 16 }}>
            <Space style={{ width: '100%', justifyContent: 'space-between' }}>
              <Search
                placeholder="搜索模板..."
                value={searchKeyword}
                onChange={(e) => setSearchKeyword(e.target.value)}
                style={{ width: 300 }}
                prefix={<SearchOutlined />}
              />
              <Space>
                <Button icon={<PlusOutlined />} onClick={handleCreateDefault}>
                  创建默认模板
                </Button>
                <Button 
                  type="primary" 
                  icon={<PlusOutlined />}
                  onClick={() => router.push('/FZSJ/template')}
                >
                  新建模板
                </Button>
              </Space>
            </Space>
          </div>

          {loading ? (
            <div style={{ textAlign: 'center', padding: 40 }}>
              <Spin size="large" />
            </div>
          ) : templates.length === 0 ? (
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description={
                <Space direction="vertical">
                  <Text>暂无自定义模板</Text>
                  <Button type="primary" onClick={handleCreateDefault}>
                    创建默认模板
                  </Button>
                </Space>
              }
            />
          ) : (
            <List
              grid={{ gutter: 16, xs: 1, sm: 1, md: 2, lg: 2, xl: 2 }}
              dataSource={templates}
              renderItem={template => (
                <List.Item>
                  <Card
                    hoverable
                    size="small"
                    title={
                      <Space>
                        <FileMarkdownOutlined />
                        <span style={{ fontWeight: 'bold' }}>{template.name}</span>
{template.is_default && <Tag color="blue">默认</Tag>}
                      </Space>
                    }
                    extra={
                      <Space>
                        <Button 
                          type="text" 
                          size="small"
                          icon={<EyeOutlined />}
                          onClick={() => handlePreview(template)}
                        >
                          预览
                        </Button>
                        {showSelectButton && (
                          <Button 
                            type="primary" 
                            size="small"
                            onClick={() => onSelect?.(template)}
                          >
                            选择
                          </Button>
                        )}
                      </Space>
                    }
                    style={{ width: '100%' }}
                  >
                    <Paragraph type="secondary" ellipsis={{ rows: 2 }}>
                      {template.description || '暂无描述'}
                    </Paragraph>
                    <Space size="small" wrap>
                       <Tag color="blue">{template.category}</Tag>
                       <Tag>{template.variables_count} 变量</Tag>
                       <Tag>{template.sections_count} 章节</Tag>
                    </Space>
                    <Divider style={{ margin: '12px 0' }} />
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                      <Text type="secondary" style={{ fontSize: 12 }}>
                        版本 {template.version}
                      </Text>
                      <Space>
                        <Button 
                          type="text" 
                          size="small"
                          icon={<EditOutlined />}
                          onClick={() => router.push(`/FZSJ/template?id=${template.id}`)}
                        >
                          编辑
                        </Button>
                        {!template.is_default && (
                          <Button 
                            type="text" 
                            size="small"
                            danger
                            icon={<DeleteOutlined />}
                            onClick={() => handleDelete(template.id)}
                          >
                            删除
                          </Button>
                        )}
                      </Space>
                    </div>
                  </Card>
                </List.Item>
              )}
            />
          )}
        </TabPane>

        <TabPane 
          tab={
            <span>
              <StarOutlined /> 系统模板
            </span>
          } 
          key="system"
        >
          {loading ? (
            <div style={{ textAlign: 'center', padding: 40 }}>
              <Spin size="large" />
            </div>
          ) : systemTemplates.length === 0 ? (
            <Empty description="暂无系统模板" />
          ) : (
            <List
              grid={{ gutter: 16, xs: 1, sm: 1, md: 2, lg: 2, xl: 2 }}
              dataSource={systemTemplates}
              renderItem={template => (
                <List.Item>
                  <Card
                    hoverable
                    size="small"
                    title={
                      <Space>
                        <FileTextOutlined />
                        <span style={{ fontWeight: 'bold' }}>{template.name}</span>
                         <Tag color="orange">系统</Tag>
                      </Space>
                    }
                    extra={
                      showSelectButton && (
                        <Button 
                          type="primary" 
                          size="small"
                          onClick={() => onSelect?.(template)}
                        >
                          选择
                        </Button>
                      )
                    }
                    style={{ width: '100%' }}
                  >
                    <Paragraph type="secondary" ellipsis={{ rows: 2 }}>
                      {template.description || '暂无描述'}
                    </Paragraph>
                    <Space size="small" wrap>
                       <Tag color="blue">{template.category}</Tag>
                       <Tag>{template.variables_count} 变量</Tag>
                       <Tag>{template.sections_count} 章节</Tag>
                    </Space>
                    <Divider style={{ margin: '12px 0' }} />
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                      <Text type="secondary" style={{ fontSize: 12 }}>
                        版本 {template.version}
                      </Text>
                      <Button 
                        type="text" 
                        size="small"
                        icon={<EyeOutlined />}
                        onClick={() => handlePreview(template)}
                      >
                        预览
                      </Button>
                    </div>
                  </Card>
                </List.Item>
              )}
            />
          )}
        </TabPane>
      </Tabs>

      {/* 预览模态框 */}
      <Modal
        title={selectedTemplate?.name}
        open={previewModalOpen}
        onCancel={() => setPreviewModalOpen(false)}
        width={800}
        footer={
          <Space>
            <Button onClick={() => setPreviewModalOpen(false)}>关闭</Button>
            {showSelectButton && (
              <Button 
                type="primary" 
                onClick={() => {
                  if (selectedTemplate) {
                    onSelect?.(selectedTemplate);
                    setPreviewModalOpen(false);
                  }
                }}
              >
                选择此模板
              </Button>
            )}
          </Space>
        }
      >
        {selectedTemplate && (
          <Space direction="vertical" style={{ width: '100%' }} size="large">
            <Descriptions column={2} size="small">
              <Descriptions.Item label="ID">{selectedTemplate.id}</Descriptions.Item>
              <Descriptions.Item label="名称">{selectedTemplate.name}</Descriptions.Item>
              <Descriptions.Item label="分类">{selectedTemplate.category}</Descriptions.Item>
              <Descriptions.Item label="版本">{selectedTemplate.version}</Descriptions.Item>
              <Descriptions.Item label="变量数">{selectedTemplate.variables?.length || 0}</Descriptions.Item>
              <Descriptions.Item label="章节数">{selectedTemplate.sections?.length || 0}</Descriptions.Item>
            </Descriptions>
            
            {selectedTemplate.description && (
              <div>
                <Text strong>描述：</Text>
                <Paragraph>{selectedTemplate.description}</Paragraph>
              </div>
            )}

            {selectedTemplate.content && (
              <div>
                <Text strong>模板内容预览：</Text>
                <div style={{ 
                  maxHeight: 400, 
                  overflow: 'auto', 
                  background: '#f5f5f5', 
                  padding: 16,
                  borderRadius: 4,
                  marginTop: 8
                }}>
                  <pre style={{ margin: 0, whiteSpace: 'pre-wrap', wordWrap: 'break-word' }}>
                    {selectedTemplate.content.substring(0, 2000)}
                    {selectedTemplate.content.length > 2000 && '...'}
                  </pre>
                </div>
              </div>
            )}
          </Space>
        )}
      </Modal>
    </div>
  );
}
