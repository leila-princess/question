'use client';

import React, { useState, useEffect, useMemo } from 'react';
import { Card, Table, Button, Space, Input, Modal, Form, Select, Tag, message, Popconfirm } from 'antd';
import { PlusOutlined, EditOutlined, DeleteOutlined, SearchOutlined, LinkOutlined } from '@ant-design/icons';
import { fetchNodes, createNode, updateNode, deleteNode, type NodeType, type GraphNode } from '../api';
import RelationModal from './RelationModal';
import { ENVIRONMENT_ORDER } from '@/constants/environments';
import { formatTableDateDisplay } from '../utils/tableDateDisplay';

const LABEL_MAP: Record<string, string> = {
  id: 'ID',
  name: '名称',
  label: '环境类别',
  type: '层级',
  parent_id: '父项目',
  description: '描述',
  status: '状态',
  user_name: '上传用户名',
  create_time: '上传时间',
};

const STATUS_OPTIONS = [
  { label: '启用', value: 'active' },
  { label: '停用', value: 'inactive' },
];

/** 表单展示中文，提交给后端为 category（父）/ test（子） */
const TYPE_OPTIONS = [
  { label: '父级', value: 'category' },
  { label: '子级', value: 'test' },
];

// 在相关地方添加类型注解
const LABEL_SELECT_OPTIONS = ENVIRONMENT_ORDER.map((name: string) => ({ label: name, value: name }));
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

interface ProjectPanelProps {
  allNodes: GraphNode[];
  selectedCategory: string | null;
  categoryNodes?: GraphNode[];
  onRefresh: () => void;
}

export default function ProjectPanel({ allNodes, selectedCategory, onRefresh }: ProjectPanelProps) {
  const [nodes, setNodes] = useState<GraphNode[]>([]);
  const [loading, setLoading] = useState(false);
  const [modalVisible, setModalVisible] = useState(false);
  const [editing, setEditing] = useState<GraphNode | null>(null);
  const [searchText, setSearchText] = useState('');
  const [form] = Form.useForm();
  const projectType = Form.useWatch('type', form);
  const [relationModal, setRelationModal] = useState<{ open: boolean; node: GraphNode }>({ open: false, node: {} as GraphNode });

  const nodeType: NodeType = 'TestProject';

  const orderSet = useMemo(() => new Set<string>(ENVIRONMENT_ORDER as unknown as readonly string[]), []);

  function bucketForProject(n: GraphNode): string {
    const lbl = String(n.label || '').trim();
    if (orderSet.has(lbl)) return lbl;
    return '其他';
  }

  const defaultProjectLabel = useMemo(() => {
    if (!selectedCategory || selectedCategory === '其他') return undefined;
    if (orderSet.has(selectedCategory)) return selectedCategory;
    return undefined;
  }, [selectedCategory, orderSet]);

  /** 与左侧分类树同源，避免本页 fetch 与 page 的 allNodes 不一致导致父级下拉为空 */
  const listForParents = useMemo(
    () => (Array.isArray(allNodes) && allNodes.length > 0 ? allNodes : nodes),
    [allNodes, nodes],
  );

  /** 已是「子级且挂了父项目」的节点，不能再选作别人的父级 */
  function isExistingChildRow(n: GraphNode): boolean {
    const t = String(n.type || '').trim();
    const pid = String((n as { parent_id?: string }).parent_id || '').trim();
    return (t === 'test' || t === '子级') && !!pid;
  }

  useEffect(() => {
    loadData();
  }, []);

  const loadData = async () => {
    setLoading(true);
    try {
      const data = await fetchNodes(nodeType);
      setNodes(Array.isArray(data) ? data : []);
    } catch {
      message.error('加载失败');
    } finally {
      setLoading(false);
    }
  };

  const handleSubmit = async (values: Record<string, unknown>) => {
    setLoading(true);
    try {
      const currentUser = getCurrentUser();
      const now = getCurrentTime();
      const typeLevel = String(values.type || 'category').trim();
      const submitData: Record<string, unknown> = {
        ...values,
        type: typeLevel,
        user_name: currentUser.userName,
        create_time: now,
      };
      if (typeLevel === 'category') {
        submitData.parent_id = '';
      }

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
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : '';
      if (msg.includes('已存在') || msg.includes('重复')) {
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
    } catch {
      message.error('删除失败');
    }
  };

  const filtered = useMemo(() => {
    let result = nodes;

    if (selectedCategory === '其他') {
      result = result.filter((n) => bucketForProject(n) === '其他');
    } else if (selectedCategory && orderSet.has(selectedCategory)) {
      result = result.filter((n) => bucketForProject(n) === selectedCategory);
    } else if (selectedCategory && nodes.some((n) => n.id === selectedCategory)) {
      result = result.filter((n) => n.id === selectedCategory);
    }

    if (searchText) {
      result = result.filter((n) => (n.name || '').toLowerCase().includes(searchText.toLowerCase()));
    }

    return result;
  }, [nodes, selectedCategory, searchText, orderSet]);

  /** 左侧树选中「环境」节点（分类名或「其他」）时，父级下拉仅显示该环境下的父级项目 */
  const treeEnvBucketForParent = useMemo(() => {
    if (!selectedCategory) return null;
    if (selectedCategory === '其他' || orderSet.has(selectedCategory)) return selectedCategory;
    return null;
  }, [selectedCategory, orderSet]);

  const parentOptions = useMemo(() => {
    const selfId = editing?.id;
    const byEnv = treeEnvBucketForParent != null;
    return listForParents
      .filter((n) => {
        if (n.id === selfId) return false;
        if (byEnv) {
          if (bucketForProject(n) !== treeEnvBucketForParent) return false;
          if (isExistingChildRow(n)) return false;
          return true;
        }
        const t = String(n.type || '').trim();
        if (t === 'test' || t === '子级') return false;
        return true;
      })
      .map((n) => ({ label: n.name || n.id, value: n.id }));
  }, [listForParents, editing?.id, treeEnvBucketForParent]);

  const columns = [
    { title: 'ID', 
      dataIndex: 'id', 
      key: 'id',
      sorter: (a: GraphNode, b: GraphNode) => {
        const aId = a.id ?? '';
        const bId = b.id ?? '';
        return String(aId).localeCompare(String(bId));
      }, 
      width: 120 },
    { title: '名称', dataIndex: 'name', key: 'name', width: 150 },
    {
      title: LABEL_MAP.label,
      dataIndex: 'label',
      key: 'label',
      width: 110,
      render: (val: string) => (val && String(val).trim() ? val : '—'),
    },
    {
      title: LABEL_MAP.type,
      dataIndex: 'type',
      key: 'type',
      width: 72,
      render: (val: string) => {
        const v = String(val || '').trim();
        if (v === 'test' || v === '子级') return '子级';
        if (v === 'category' || v === '父级') return '父级';
        return v || '—';
      },
    },
    {
      title: '父项目',
      dataIndex: 'parent_name',
      key: 'parent_name',
      width: 120,
      ellipsis: true,
      render: (_: unknown, record: GraphNode) => {
        const r = record as { parent_name?: string; parent_id?: string };
        if (r.parent_name && String(r.parent_name).trim()) return r.parent_name;
        if (r.parent_id && String(r.parent_id).trim()) return r.parent_id;
        return '—';
      },
    },
    { title: '描述', dataIndex: 'description', key: 'description', ellipsis: true },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 80,
      render: (val: string) => (
        <Tag color={val === 'active' ? 'green' : val === 'inactive' ? 'red' : 'default'}>
          {val === 'active' ? '启用' : val === 'inactive' ? '停用' : val || '—'}
        </Tag>
      ),
    },
    { title: '上传用户名', dataIndex: 'user_name', key: 'user_name', width: 100 },
    {
      title: '上传时间',
      dataIndex: 'create_time',
      key: 'create_time',
      width: 120,
      render: (val: string) => formatTableDateDisplay(val),
    },
    {
      title: '操作',
      key: 'action',
      width: 200,
      fixed: 'right' as const,
      render: (_: unknown, record: GraphNode) => (
        <Space>
          <Button
            type="link"
            size="small"
            icon={<EditOutlined />}
            onClick={() => {
              setEditing(record);
              const r = record as { parent_id?: string; type?: string; label?: string };
              const rawType = String(r.type || '').trim();
              const formType = rawType === 'test' || rawType === '子级' ? 'test' : 'category';
              form.setFieldsValue({
                name: record.name,
                label: r.label && String(r.label).trim() ? r.label : undefined,
                type: formType,
                parent_id: r.parent_id && String(r.parent_id).trim() ? r.parent_id : undefined,
                description: record.description,
                status: record.status || 'active',
                user_name: record.user_name,
                create_time: record.create_time,
              });
              setModalVisible(true);
            }}
          >
            编辑
          </Button>
          <Button type="link" size="small" icon={<LinkOutlined />} onClick={() => setRelationModal({ open: true, node: record })}>
            关联
          </Button>
          <Popconfirm title="确定删除？" onConfirm={() => handleDelete(record.id)}>
            <Button type="link" danger size="small" icon={<DeleteOutlined />}>
              删除
            </Button>
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
      label: defaultProjectLabel,
      type: 'category',
      parent_id: undefined,
      description: '',
      status: 'active',
      user_name: currentUser.userName,
      create_time: now,
    });
    setModalVisible(true);
  };

  return (
    <Card
      title="项目管理"
      extra={
        <Space>
          <Input
            placeholder="按名称搜索"
            prefix={<SearchOutlined />}
            value={searchText}
            onChange={(e) => setSearchText(e.target.value)}
            allowClear
            style={{ width: 200 }}
          />
          <Button type="primary" icon={<PlusOutlined />} onClick={handleAdd}>
            新增
          </Button>
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
        scroll={{ x: 1120 }}
      />

      <Modal
        title={`${editing ? '编辑' : '新增'}项目`}
        open={modalVisible}
        onCancel={() => {
          setModalVisible(false);
          form.resetFields();
          setEditing(null);
        }}
        footer={null}
        width={600}
      >
        <Form
          form={form}
          layout="vertical"
          onFinish={handleSubmit}
          style={{ marginTop: 16 }}
          onValuesChange={(changed) => {
            if (changed.type === 'category') {
              form.setFieldsValue({ parent_id: undefined });
            }
          }}
        >
          <Form.Item label={LABEL_MAP.name} name="name" rules={[{ required: true, message: '请输入名称' }]}>
            <Input placeholder="请输入项目名称" />
          </Form.Item>

          <Form.Item
            label={LABEL_MAP.label}
            name="label"
            tooltip="与图谱中的「环境」节点关联请在「关联」中操作；此处为分类用环境类别名"
            rules={[{ required: true, message: '请选择环境类别' }]}
          >
            <Select placeholder="请选择环境类别" options={LABEL_SELECT_OPTIONS} allowClear={false} />
          </Form.Item>

          <Form.Item label={LABEL_MAP.type} name="type" initialValue="category" rules={[{ required: true, message: '请选择层级' }]}>
            <Select options={TYPE_OPTIONS} placeholder="父级 / 子级" />
          </Form.Item>

          {projectType === 'test' ? (
            <Form.Item label={LABEL_MAP.parent_id} name="parent_id" rules={[{ required: true, message: '请选择父项目' }]}>
              <Select
                showSearch
                optionFilterProp="label"
                placeholder="选择父级项目"
                options={parentOptions}
                allowClear
                key={`parent-${treeEnvBucketForParent ?? 'all'}-${parentOptions.length}`}
              />
            </Form.Item>
          ) : null}

          <Form.Item label={LABEL_MAP.description} name="description">
            <Input.TextArea rows={3} placeholder="请输入项目描述" />
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
              <Button
                onClick={() => {
                  setModalVisible(false);
                  setEditing(null);
                }}
              >
                取消
              </Button>
              <Button type="primary" htmlType="submit" loading={loading}>
                保存
              </Button>
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
        onSaved={() => {
          loadData();
          onRefresh();
        }}
      />
    </Card>
  );
}
