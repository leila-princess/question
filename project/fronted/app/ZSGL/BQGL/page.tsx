'use client';

import React, { useState, useEffect } from 'react';
import {
  Card, Tabs, Table, Button, Space, Input, Modal, Form,
  Tag, message, Popconfirm, Typography, Row, Col, List, Badge,
  Divider, Select, ColorPicker
} from 'antd';
import {
  PlusOutlined, EditOutlined, DeleteOutlined, SearchOutlined,
  AppstoreOutlined, EnvironmentOutlined, ExperimentOutlined,
  BarChartOutlined, FileTextOutlined, TagOutlined
} from '@ant-design/icons';

const { Title, Text } = Typography;
const { Option } = Select;

interface TagItem {
  id: string;
  name: string;
  color: string;
  nodeType: string;
  nodeCount: number;
  createTime: string;
}

interface NodeWithType {
  id: string;
  name: string;
  nodeType: string;
  tags: string[];
}

const nodeTypeConfig: Record<string, { label: string; icon: React.ReactNode; color: string }> = {
  equipment: { label: '设备', icon: <AppstoreOutlined />, color: '#1890ff' },
  environment: { label: '环境', icon: <EnvironmentOutlined />, color: '#52c41a' },
  project: { label: '项目', icon: <ExperimentOutlined />, color: '#fa8c16' },
  indicator: { label: '指标', icon: <BarChartOutlined />, color: '#722ed1' },
  method: { label: '方法', icon: <FileTextOutlined />, color: '#13c2c2' },
  document: { label: '文档', icon: <FileTextOutlined />, color: '#eb2f96' },
};

const mockTagsByType: Record<string, TagItem[]> = {
  equipment: [
    { id: 'tag-eq-1', name: '高温', color: '#ff4d4f', nodeType: 'equipment', nodeCount: 5, createTime: '2026-01-10' },
    { id: 'tag-eq-2', name: '低温', color: '#1890ff', nodeType: 'equipment', nodeCount: 3, createTime: '2026-01-10' },
    { id: 'tag-eq-3', name: '可靠性', color: '#52c41a', nodeType: 'equipment', nodeCount: 8, createTime: '2026-01-10' },
  ],
  environment: [
    { id: 'tag-env-1', name: '气候环境', color: '#1890ff', nodeType: 'environment', nodeCount: 4, createTime: '2026-01-10' },
    { id: 'tag-env-2', name: '力学环境', color: '#fa8c16', nodeType: 'environment', nodeCount: 3, createTime: '2026-01-10' },
  ],
  project: [
    { id: 'tag-proj-1', name: '性能试验', color: '#722ed1', nodeType: 'project', nodeCount: 6, createTime: '2026-01-10' },
    { id: 'tag-proj-2', name: '环境试验', color: '#52c41a', nodeType: 'project', nodeCount: 4, createTime: '2026-01-10' },
  ],
  indicator: [
    { id: 'tag-ind-1', name: '性能指标', color: '#1890ff', nodeType: 'indicator', nodeCount: 12, createTime: '2026-01-10' },
    { id: 'tag-ind-2', name: '可靠性指标', color: '#fa8c16', nodeType: 'indicator', nodeCount: 5, createTime: '2026-01-10' },
  ],
  method: [
    { id: 'tag-method-1', name: '测试方法', color: '#13c2c2', nodeType: 'method', nodeCount: 8, createTime: '2026-01-10' },
    { id: 'tag-method-2', name: '分析方法', color: '#722ed1', nodeType: 'method', nodeCount: 4, createTime: '2026-01-10' },
  ],
  document: [
    { id: 'tag-doc-1', name: '国家标准', color: '#1890ff', nodeType: 'document', nodeCount: 15, createTime: '2026-01-10' },
    { id: 'tag-doc-2', name: '军用标准', color: '#722ed1', nodeType: 'document', nodeCount: 10, createTime: '2026-01-10' },
  ],
};

const mockNodesByTag: Record<string, NodeWithType[]> = {
  'tag-eq-1': [
    { id: 'eq-1', name: '无人机A型', nodeType: 'equipment', tags: ['tag-eq-1', 'tag-eq-3'] },
    { id: 'eq-3', name: '主战坦克99A', nodeType: 'equipment', tags: ['tag-eq-1'] },
  ],
  'tag-eq-2': [
    { id: 'eq-1', name: '无人机A型', nodeType: 'equipment', tags: ['tag-eq-2'] },
    { id: 'eq-4', name: '远程预警雷达', nodeType: 'equipment', tags: ['tag-eq-2'] },
  ],
};

export default function TagManagementPage() {
  const [activeTab, setActiveTab] = useState('equipment');
  const [tags, setTags] = useState<Record<string, TagItem[]>>(mockTagsByType);
  const [searchText, setSearchText] = useState('');
  const [modalVisible, setModalVisible] = useState(false);
  const [editingTag, setEditingTag] = useState<TagItem | null>(null);
  const [selectedTag, setSelectedTag] = useState<TagItem | null>(null);
  const [form] = Form.useForm();
  const [loading, setLoading] = useState(false);

  const handleAdd = () => {
    setEditingTag(null);
    form.resetFields();
    form.setFieldsValue({ nodeType: activeTab, color: '#1890ff' });
    setModalVisible(true);
  };

  const handleEdit = (tag: TagItem) => {
    setEditingTag(tag);
    form.setFieldsValue(tag);
    setModalVisible(true);
  };

  const handleDelete = async (nodeType: string, tagId: string) => {
    setLoading(true);
    try {
      await new Promise(resolve => setTimeout(resolve, 500));
      setTags(prev => ({
        ...prev,
        [nodeType]: prev[nodeType].filter(t => t.id !== tagId),
      }));
      if (selectedTag?.id === tagId) setSelectedTag(null);
      message.success('删除成功');
    } catch {
      message.error('删除失败');
    } finally {
      setLoading(false);
    }
  };

  const handleSubmit = async (values: any) => {
    setLoading(true);
    try {
      await new Promise(resolve => setTimeout(resolve, 500));
      const now = new Date().toISOString().split('T')[0];
      if (editingTag) {
        setTags(prev => ({
          ...prev,
          [values.nodeType]: prev[values.nodeType].map(t =>
            t.id === editingTag.id ? { ...t, ...values } : t
          ),
        }));
        message.success('修改成功');
      } else {
        const newTag: TagItem = {
          id: `tag-${Date.now()}`,
          ...values,
          nodeCount: 0,
          createTime: now,
        };
        setTags(prev => ({
          ...prev,
          [values.nodeType]: [...prev[values.nodeType], newTag],
        }));
        message.success('新增成功');
      }
      setModalVisible(false);
      form.resetFields();
      setEditingTag(null);
    } catch {
      message.error('操作失败');
    } finally {
      setLoading(false);
    }
  };

  const filteredTags = (tags[activeTab] || []).filter(tag =>
    !searchText || tag.name.includes(searchText)
  );

  const columns = [
    {
      title: '标签名称',
      dataIndex: 'name',
      key: 'name',
      render: (name: string, record: TagItem) => (
        <Tag
          color={record.color}
          style={{ cursor: 'pointer', fontSize: 14, padding: '4px 12px' }}
          onClick={() => setSelectedTag(record)}
        >
          {name}
        </Tag>
      ),
    },
    {
      title: '颜色',
      dataIndex: 'color',
      key: 'color',
      width: 80,
      render: (color: string) => (
        <div style={{
          width: 24,
          height: 24,
          borderRadius: 4,
          background: color,
          border: '1px solid #d9d9d9',
        }} />
      ),
    },
    {
      title: '关联节点数',
      dataIndex: 'nodeCount',
      key: 'nodeCount',
      width: 120,
      render: (count: number) => <Badge count={count} showZero color="blue" />,
    },
    {
      title: '创建时间',
      dataIndex: 'createTime',
      key: 'createTime',
      width: 120,
    },
    {
      title: '操作',
      key: 'action',
      width: 150,
      render: (_: any, record: TagItem) => (
        <Space>
          <Button type="link" size="small" icon={<EditOutlined />} onClick={() => handleEdit(record)}>
            编辑
          </Button>
          <Popconfirm title="确定删除？" onConfirm={() => handleDelete(record.nodeType, record.id)}>
            <Button type="link" size="small" danger icon={<DeleteOutlined />}>
              删除
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  const tabItems = Object.entries(nodeTypeConfig).map(([key, config]) => ({
    key,
    label: (
      <span>
        {config.icon}
        {config.label}
      </span>
    ),
  }));

  const selectedTagNodes = selectedTag ? (mockNodesByTag[selectedTag.id] || []) : [];

  return (
    <div style={{ padding: 24, background: '#f5f5f5', minHeight: '100vh' }}>
      <Card style={{ borderRadius: 12, boxShadow: '0 2px 8px rgba(0,0,0,0.06)' }}>
        <div style={{ marginBottom: 24 }}>
          <Title level={3} style={{ margin: 0, color: '#235cff' }}>
            标签管理
          </Title>
          <Text type="secondary">
            管理各类节点的标签，支持新增、编辑和删除，可查看标签关联的节点数据
          </Text>
        </div>

        <Row gutter={16} style={{ marginBottom: 24 }}>
          {Object.entries(nodeTypeConfig).map(([key, config]) => (
            <Col span={4} key={key}>
              <Card
                size="small"
                style={{
                  textAlign: 'center',
                  cursor: 'pointer',
                  background: activeTab === key ? '#e6f7ff' : '#fff',
                  border: activeTab === key ? '2px solid #1890ff' : '1px solid #f0f0f0',
                }}
                onClick={() => setActiveTab(key)}
              >
                <div style={{ fontSize: 24, color: config.color, marginBottom: 8 }}>
                  {config.icon}
                </div>
                <div style={{ fontWeight: 'bold' }}>{config.label}</div>
                <div style={{ fontSize: 12, color: '#999' }}>
                  {(tags[key] || []).length} 个标签
                </div>
              </Card>
            </Col>
          ))}
        </Row>

        <Row gutter={24}>
          <Col span={14}>
            <Card
              title={
                <Space>
                  <TagOutlined />
                  <span>{nodeTypeConfig[activeTab]?.label}标签列表</span>
                </Space>
              }
              extra={
                <Space>
                  <Input
                    placeholder="搜索标签"
                    prefix={<SearchOutlined />}
                    value={searchText}
                    onChange={e => setSearchText(e.target.value)}
                    allowClear
                    style={{ width: 200 }}
                  />
                  <Button type="primary" icon={<PlusOutlined />} onClick={handleAdd}>
                    新增标签
                  </Button>
                </Space>
              }
            >
              <Table
                columns={columns}
                dataSource={filteredTags}
                rowKey="id"
                loading={loading}
                pagination={{ pageSize: 10, showSizeChanger: true }}
              />
            </Card>
          </Col>

          <Col span={10}>
            <Card title="标签关联节点">
              {selectedTag ? (
                <div>
                  <div style={{ marginBottom: 16 }}>
                    <Tag color={selectedTag.color} style={{ fontSize: 14, padding: '4px 12px' }}>
                      {selectedTag.name}
                    </Tag>
                    <Text type="secondary" style={{ marginLeft: 8 }}>
                      共 {selectedTagNodes.length} 个节点
                    </Text>
                  </div>

                  <List
                    dataSource={selectedTagNodes}
                    renderItem={node => (
                      <List.Item>
                        <List.Item.Meta
                          avatar={
                            <div style={{ color: nodeTypeConfig[node.nodeType]?.color }}>
                              {nodeTypeConfig[node.nodeType]?.icon}
                            </div>
                          }
                          title={node.name}
                          description={
                            <Space size={[0, 4]} wrap>
                              {node.tags.map(tagId => {
                                const allTags = Object.values(tags).flat();
                                const tag = allTags.find(t => t.id === tagId);
                                return tag ? (
                                  <Tag key={tagId} color={tag.color} style={{ margin: 0 }}>
                                    {tag.name}
                                  </Tag>
                                ) : null;
                              })}
                            </Space>
                          }
                        />
                      </List.Item>
                    )}
                  />
                </div>
              ) : (
                <div style={{ textAlign: 'center', padding: 40, color: '#999' }}>
                  点击左侧标签查看关联节点
                </div>
              )}
            </Card>
          </Col>
        </Row>

        <Modal
          title={editingTag ? '编辑标签' : '新增标签'}
          open={modalVisible}
          onCancel={() => { setModalVisible(false); form.resetFields(); setEditingTag(null); }}
          footer={null}
          width={500}
        >
          <Form form={form} layout="vertical" onFinish={handleSubmit} style={{ marginTop: 24 }}>
            <Form.Item label="节点类型" name="nodeType" rules={[{ required: true }]}>
              <Select disabled={!!editingTag}>
                {Object.entries(nodeTypeConfig).map(([key, config]) => (
                  <Option key={key} value={key}>{config.label}</Option>
                ))}
              </Select>
            </Form.Item>
            <Form.Item label="标签名称" name="name" rules={[{ required: true, message: '请输入标签名称' }]}>
              <Input placeholder="请输入标签名称" />
            </Form.Item>
            <Form.Item label="标签颜色" name="color" rules={[{ required: true }]}>
              <ColorPicker />
            </Form.Item>
            <Form.Item style={{ textAlign: 'right', marginBottom: 0, marginTop: 24 }}>
              <Space>
                <Button onClick={() => { setModalVisible(false); form.resetFields(); }}>取消</Button>
                <Button type="primary" htmlType="submit" loading={loading}>保存</Button>
              </Space>
            </Form.Item>
          </Form>
        </Modal>
      </Card>
    </div>
  );
}

export const dynamic = 'force-dynamic';
