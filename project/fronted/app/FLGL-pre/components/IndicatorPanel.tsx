'use client';

import React, { useState, useEffect, useMemo } from 'react';
import { Card, Table, Button, Space, Input, Modal, Form, Select, Tag, message, Popconfirm, Typography } from 'antd';
import { PlusOutlined, EditOutlined, DeleteOutlined, SearchOutlined, LinkOutlined } from '@ant-design/icons';
import { fetchNodes, createNode, updateNode, deleteNode, fetchNodeRelations, createRelations, deleteRelation, fetchAvailableNodes, type NodeType, type GraphNode, type RelationInput } from '../api';
import RelationModal from './RelationModal';
import { formatTableDateDisplay } from '../utils/tableDateDisplay';

const { Text } = Typography;

const LABEL_MAP: Record<string, string> = {
  id: 'ID',
  name: '名称',
  type: '类别',
  description: '描述',
  status: '状态',
  user_name: '上传用户名',
  create_time: '上传时间',
};

const STATUS_OPTIONS = [
  { label: '启用', value: 'active' },
  { label: '停用', value: 'inactive' },
];

const INDICATOR_CATS = [
  { key: '距离', keywords: ['距离', 'range', 'distance'] },
  { key: '时间', keywords: ['时间', 'time', 'duration', '周期'] },
  { key: '速度', keywords: ['速度', 'speed', 'velocity'] },
  { key: '高度', keywords: ['高度', 'altitude', 'height', '高程'] },
];

function getCurrentUser(): { userId: string; userName: string } {
  if (typeof window !== 'undefined') {
    const userStr = localStorage.getItem('userInfo');
    if (userStr) {
      try {
        const user = JSON.parse(userStr);
        return { userId: user.userId || 'admin', userName: user.userName || '管理员' };
      } catch {}
    }
  }
  return { userId: 'admin', userName: '管理员' };
}

function getCurrentTime(): string {
  const now = new Date();
  return now.toISOString().replace('T', ' ').substring(0, 19);
}

function classifyIndicator(name: string): string {
  if (!name) return '其他';
  const lower = name.toLowerCase();
  for (const cat of INDICATOR_CATS) {
    if (cat.keywords.some(kw => lower.includes(kw))) return cat.key;
  }
  return '其他';
}

interface IndicatorPanelProps {
  allNodes: GraphNode[];
  selectedCategory: string | null;
  categoryNodes?: GraphNode[];
  onRefresh: () => void;
}

export default function IndicatorPanel({ allNodes, selectedCategory, categoryNodes, onRefresh }: IndicatorPanelProps) {
  const [nodes, setNodes] = useState<GraphNode[]>([]);
  const [loading, setLoading] = useState(false);
  const [modalVisible, setModalVisible] = useState(false);
  const [editing, setEditing] = useState<GraphNode | null>(null);
  const [searchText, setSearchText] = useState('');
  const [form] = Form.useForm();
  const [relationModal, setRelationModal] = useState<{ open: boolean; node: GraphNode }>({ open: false, node: {} as GraphNode });

  const nodeType: NodeType = 'Indicator';

  const typeOptions = useMemo(() => {
    const cats = INDICATOR_CATS.map(c => c.key);
    return [...cats, '其他'].map(t => ({ label: t, value: t }));
  }, []);

  const defaultType = useMemo(() => {
    const validCats = INDICATOR_CATS.map(c => c.key);
    if (selectedCategory && validCats.includes(selectedCategory)) {
      return selectedCategory;
    }
    if (selectedCategory === '其他') {
      return '其他';
    }
    return undefined;
  }, [selectedCategory]);

  useEffect(() => { loadData(); }, []);

  const loadData = async () => {
    setLoading(true);
    try {
      const data = await fetchNodes(nodeType);
      setNodes(Array.isArray(data) ? data : []);
    } catch { message.error('加载失败'); }
    finally { setLoading(false); }
  };

  const handleSubmit = async (values: any) => {
    setLoading(true);
    try {
      const currentUser = getCurrentUser();
      const now = getCurrentTime();
      
      const submitData = {
        ...values,
        user_name: currentUser.userName,
        create_time: now,
      };

      if (editing) {
        await updateNode(nodeType, editing.id, submitData);
        message.success('修改成功');
      } else {
        await createNode(nodeType, submitData);
        message.success('新增成功');
      }
      setModalVisible(false);
      form.resetFields();
      setEditing(null);
      loadData();
      onRefresh();
    } catch (err: any) {
      if (err.message?.includes('已存在') || err.message?.includes('重复')) {
        message.error('节点名称已存在，请使用其他名称');
      } else {
        message.error('操作失败');
      }
    } finally {
      setLoading(false);
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await deleteNode(nodeType, id);
      message.success('删除成功');
      loadData();
      onRefresh();
    } catch { message.error('删除失败'); }
  };

  const filtered = useMemo(() => {
    let result = nodes;
    
    const validCats = INDICATOR_CATS.map(c => c.key);
    if (selectedCategory && (validCats.includes(selectedCategory) || selectedCategory === '其他')) {
      result = result.filter(n => n.type === selectedCategory);
    }
    
    if (searchText) {
      result = result.filter(n =>
        (n.name || '').toLowerCase().includes(searchText.toLowerCase())
      );
    }
    
    return result;
  }, [nodes, selectedCategory, searchText]);

  const columns = [
    { title: 'ID', 
      dataIndex: 'id', 
      key: 'id', 
      sorter: (a: GraphNode, b: GraphNode) => {
        const aId = a.id ?? '';
        const bId = b.id ?? '';
        return String(aId).localeCompare(String(bId));
      },
      width: 80 },
    { title: '名称', dataIndex: 'name', key: 'name', width: 100 },
    { title: '类别', dataIndex: 'type', key: 'type', width: 80 },
    { title: '描述', dataIndex: 'description', key: 'description', width: 150,ellipsis: true },
    {
      title: '状态', dataIndex: 'status', key: 'status', width: 80,
      render: (val: string) => (
        <Tag color={val === 'active' ? 'green' : val === 'inactive' ? 'red' : 'default'}>
          {val === 'active' ? '启用' : val === 'inactive' ? '停用' : val || '—'}
        </Tag>
      ),
    },
    { title: '上传用户名', dataIndex: 'user_name', key: 'user_name', width: 100 },
    { title: '上传时间', dataIndex: 'create_time', key: 'create_time', width: 100,render: (val: string) => formatTableDateDisplay(val), },
    {
      title: '操作', key: 'action', width: 200, fixed: 'right' as const,
      render: (_: any, record: GraphNode) => (
        <Space>
          <Button type="link" size="small" icon={<EditOutlined />} onClick={() => {
            setEditing(record);
            form.setFieldsValue(record);
            setModalVisible(true);
          }}>编辑</Button>
          <Button type="link" size="small" icon={<LinkOutlined />} onClick={() => setRelationModal({ open: true, node: record })}>关联</Button>
          <Popconfirm title="确定删除？" onConfirm={() => handleDelete(record.id)}>
            <Button type="link" danger size="small" icon={<DeleteOutlined />}>删除</Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  const handleAdd = () => {
    setEditing(null);
    const currentUser = getCurrentUser();
    const now = getCurrentTime();
    form.setFieldsValue({
      name: '',
      type: defaultType || '',
      description: '',
      status: 'active',
      user_name: currentUser.userName,
      create_time: now,
    });
    setModalVisible(true);
  };

  return (
    <Card
      title="指标管理"
      extra={
        <Space>
          <Input
            placeholder="按名称搜索"
            prefix={<SearchOutlined />}
            value={searchText}
            onChange={e => setSearchText(e.target.value)}
            allowClear
            style={{ width: 200 }}
          />
          <Button type="primary" icon={<PlusOutlined />} onClick={handleAdd}>新增</Button>
        </Space>
      }
    >
      <Table
        columns={columns}
        dataSource={filtered}
        rowKey="id"
        loading={loading}
        size="small"
        pagination={{ pageSize: 15, showSizeChanger: true, showTotal: (t) => `共 ${t} 条` }}
        scroll={{ x: 1200 }}
      />

      <Modal
        title={`${editing ? '编辑' : '新增'}指标`}
        open={modalVisible}
        onCancel={() => { setModalVisible(false); form.resetFields(); setEditing(null); }}
        footer={null}
        width={600}
      >
        <Form form={form} layout="vertical" onFinish={handleSubmit} style={{ marginTop: 16 }}>
          <Form.Item label={LABEL_MAP.name} name="name" rules={[{ required: true, message: '请输入名称' }]}>
            <Input placeholder="请输入指标名称" />
          </Form.Item>
          
          <Form.Item label={LABEL_MAP.type} name="type">
            <Select 
              placeholder={defaultType ? `当前选择: ${defaultType}` : "请选择指标类别"}
              options={typeOptions}
              allowClear
            />
          </Form.Item>
          
          <Form.Item label={LABEL_MAP.description} name="description">
            <Input.TextArea rows={3} placeholder="请输入指标描述" />
          </Form.Item>
          
          <Form.Item label={LABEL_MAP.status} name="status">
            <Select options={STATUS_OPTIONS} placeholder="请选择状态" />
          </Form.Item>
          
          <Form.Item label={LABEL_MAP.user_name} name="user_name">
            <Input disabled placeholder="自动获取当前用户" />
          </Form.Item>
          
          <Form.Item label={LABEL_MAP.create_time} name="create_time">
            <Input disabled placeholder="自动获取当前时间" />
          </Form.Item>
          
          <Form.Item style={{ textAlign: 'right' }}>
            <Space>
              <Button onClick={() => { setModalVisible(false); setEditing(null); }}>取消</Button>
              <Button type="primary" htmlType="submit" loading={loading}>保存</Button>
            </Space>
          </Form.Item>
        </Form>
      </Modal>

      <RelationModal
        open={relationModal.open}
        nodeId={relationModal.node.id}
        nodeType={nodeType}
        nodeName={relationModal.node.name || ''}
        onClose={() => setRelationModal({ open: false, node: {} as GraphNode })}
        onSaved={() => { loadData(); onRefresh(); }}
      />
    </Card>
  );
}