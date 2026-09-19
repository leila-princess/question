'use client';

/**
 * 设备类型管理：独立实现，不通过 GenericNodePanel，以满足一级类别 / 其他(自定义) / 创建者信息等需求。
 * 与左侧分类树：一级 key 为各节点的 Type 值，二级 key 为节点 Id（见 page.tsx buildTree for EquipmentType）。
 */
import React, { useState, useEffect, useMemo } from 'react';
import { Card, Table, Button, Space, Input, Modal, Form, Select, Tag, message, Popconfirm, Typography } from 'antd';
import { PlusOutlined, EditOutlined, DeleteOutlined, SearchOutlined, LinkOutlined, FilterOutlined } from '@ant-design/icons';
import { fetchNodes, createNode, updateNode, deleteNode, type GraphNode, type NodeType } from '../api';
import RelationModal from './RelationModal';
import { formatTableDateDisplay } from '../utils/tableDateDisplay';

const { Text } = Typography;

const nodeType: NodeType = 'EquipmentType';

/** 表单内选择「其他(自定义父级)」时的 value，避免与数据库中重名的「其他」类混淆 */
const TYPE_OTHER_VALUE = '__FLGL_TYPE_OTHER__';

const STATUS_OPTIONS = [
  { label: '启用', value: 'active' },
  { label: '停用', value: 'inactive' },
];

const FILTERABLE_FIELDS = ['status', 'type'];

const LABELS: Record<string, string> = {
  name: '名称',
  type: '一级类别',
  label: '标签',
  description: '描述',
  status: '状态',
  type_category: '一级类别',
  type_custom: '父级标题',
  user_id: '新增用户',
  create_time: '新增时间',
};

function getCurrentUserName(): string {
  if (typeof window === 'undefined') return '管理员';
  const userStr = localStorage.getItem('userInfo');
  if (userStr) {
    try {
      const u = JSON.parse(userStr) as { userName?: string; name?: string };
      const n = (u.userName || u.name || '').trim();
      if (n) return n;
    } catch {
      /* */
    }
  }
  const direct = (localStorage.getItem('userName') || '').trim();
  if (direct) return direct;
  return '管理员';
}

function getCurrentTime(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}:${String(d.getSeconds()).padStart(2, '0')}`;
}

/** 根据分类树当前选中项解析一级 Type：可选为二级节点 id 或一级 type 标题 */
function resolveFirstLevelTypeFromTree(selectedCategory: string | null, list: GraphNode[]): string | undefined {
  if (!selectedCategory) return undefined;
  const asNode = list.find((n) => String(n.id) === String(selectedCategory));
  if (asNode?.type) return String(asNode.type).trim() || undefined;
  const allTypes = new Set(
    list.map((n) => n.type).filter((t): t is string => !!t && String(t).trim() !== ''),
  );
  if (allTypes.has(String(selectedCategory))) {
    return String(selectedCategory);
  }
  return undefined;
}

function buildTypeCategoryInitial(
  typeStr: string | undefined,
  knownFirstLevels: string[],
): { type_category: string; type_custom: string } {
  const t = (typeStr || '').trim();
  if (!t) {
    return { type_category: '', type_custom: '' };
  }
  if (knownFirstLevels.includes(t)) {
    return { type_category: t, type_custom: '' };
  }
  return { type_category: TYPE_OTHER_VALUE, type_custom: t };
}

export default function EquipmentTypePanel({
  allNodes: _allNodes,
  selectedCategory,
  categoryNodes: _categoryNodes,
  onRefresh,
}: {
  allNodes: GraphNode[];
  selectedCategory: string | null;
  categoryNodes?: GraphNode[];
  onRefresh: () => void;
}) {
  const [nodes, setNodes] = useState<GraphNode[]>([]);
  const [loading, setLoading] = useState(false);
  const [modalVisible, setModalVisible] = useState(false);
  const [editing, setEditing] = useState<GraphNode | null>(null);
  const [searchText, setSearchText] = useState('');
  const [form] = Form.useForm();
  const [relationModal, setRelationModal] = useState<{ open: boolean; node: GraphNode }>({ open: false, node: {} as GraphNode });
  const [filters, setFilters] = useState<Record<string, string | undefined>>({});

  const typeCategoryWatch = Form.useWatch('type_category', form);

  const firstLevelTypes = useMemo(() => {
    return Array.from(new Set(nodes.map((n) => n.type).filter((t): t is string => !!t && String(t).trim() !== '')))
      .map((t) => String(t).trim())
      .sort();
  }, [nodes]);

  const firstLevelOptions = useMemo(
    () => [
      ...firstLevelTypes.map((t) => ({ label: t, value: t })),
      { label: '其他(自定义父级标题)', value: TYPE_OTHER_VALUE },
    ],
    [firstLevelTypes],
  );

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

  const uniqueValues = useMemo(() => {
    const out: Record<string, Set<string>> = {};
    for (const field of FILTERABLE_FIELDS) {
      const set = new Set<string>();
      for (const n of nodes) {
        const v = n[field as keyof GraphNode];
        if (v != null && String(v).trim() !== '') set.add(String(v));
      }
      if (set.size) out[field] = set;
    }
    return out;
  }, [nodes]);

  const filtered = useMemo(() => {
    let result = nodes;
    if (selectedCategory) {
      const asNode = nodes.find((n) => String(n.id) === String(selectedCategory));
      if (asNode) {
        result = result.filter((n) => n.id === selectedCategory);
      } else {
        result = result.filter((n) => String(n.type || '') === String(selectedCategory));
      }
    }
    if (searchText) {
      const s = searchText.toLowerCase();
      result = result.filter(
        (n) => (n.name || '').toLowerCase().includes(s) || (n.id || '').toLowerCase().includes(s),
      );
    }
    for (const [field, value] of Object.entries(filters)) {
      if (value) {
        result = result.filter((n) => n[field as keyof GraphNode] === value);
      }
    }
    return result;
  }, [nodes, selectedCategory, searchText, filters]);

  const buildSubmitType = (typeCategory: string, typeCustom: string): string => {
    if (typeCategory === TYPE_OTHER_VALUE) {
      return (typeCustom || '').trim();
    }
    return (typeCategory || '').trim();
  };

  const handleSubmit = async (values: Record<string, unknown>) => {
    const typeCat = String(values.type_category ?? '');
    const typeCustom = String(values.type_custom ?? '');
    const typeToSave = buildSubmitType(typeCat, typeCustom);
    if (!typeToSave) {
      message.error(typeCat === TYPE_OTHER_VALUE ? '请填写父级标题' : '请选择一级类别');
      return;
    }
    if (!String(values.name || '').trim()) {
      message.error('请填写名称');
      return;
    }

    setLoading(true);
    try {
      if (editing) {
        await updateNode(nodeType, editing.id, {
          name: String(values.name).trim(),
          type: typeToSave,
          label: (values.label != null ? String(values.label) : '') || undefined,
          description: values.description != null ? String(values.description) : undefined,
          status: String(values.status || 'active'),
        });
        message.success('修改成功');
      } else {
        const uname = getCurrentUserName();
        const ctime = getCurrentTime();
        await createNode(nodeType, {
          name: String(values.name).trim(),
          type: typeToSave,
          label: (values.label != null ? String(values.label) : '') || undefined,
          description: values.description,
          status: (values.status as string) || 'active',
          user_id: uname,
          create_time: ctime,
        });
        message.success('新增成功');
      }
      setModalVisible(false);
      form.resetFields();
      setEditing(null);
      await loadData();
      onRefresh();
    } catch {
      message.error('操作失败');
    } finally {
      setLoading(false);
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await deleteNode(nodeType, id);
      message.success('删除成功');
      await loadData();
      onRefresh();
    } catch {
      message.error('删除失败');
    }
  };

  const openAdd = () => {
    setEditing(null);
    const def = resolveFirstLevelTypeFromTree(selectedCategory, nodes);
    const userName = getCurrentUserName();
    const now = getCurrentTime();
    if (def && firstLevelTypes.includes(def)) {
      form.setFieldsValue({
        type_category: def,
        type_custom: '',
        name: '',
        label: '',
        description: '',
        status: 'active',
        user_id: userName,
        create_time: now,
      });
    } else if (def) {
      form.setFieldsValue({
        type_category: TYPE_OTHER_VALUE,
        type_custom: def,
        name: '',
        label: '',
        description: '',
        status: 'active',
        user_id: userName,
        create_time: now,
      });
    } else {
      form.setFieldsValue({
        type_category: undefined,
        type_custom: '',
        name: '',
        label: '',
        description: '',
        status: 'active',
        user_id: userName,
        create_time: now,
      });
    }
    setModalVisible(true);
  };

  const openEdit = (record: GraphNode) => {
    setEditing(record);
    const { type_category, type_custom } = buildTypeCategoryInitial(record.type, firstLevelTypes);
    form.setFieldsValue({
      type_category: (type_category || undefined) as string | undefined,
      type_custom: type_custom || undefined,
      name: record.name,
      label: (record as { label?: string }).label,
      description: record.description,
      status: record.status || 'active',
      user_id: (record as { user_id?: string }).user_id || (record as { user_name?: string }).user_name,
      create_time: record.create_time,
    });
    setModalVisible(true);
  };

  const tableFields = [
    { key: 'name' as const, label: '名称' },
    { key: 'id' as const, label: 'ID' },
    { key: 'type' as const, label: '一级类别' },
    { key: 'label' as const, label: '标签' },
    { key: 'description' as const, label: '描述' },
    { key: 'status' as const, label: '状态' },
  ];

  const columns = [
    ...tableFields.map((c) => ({
      title: c.label,
      dataIndex: c.key,
      key: c.key,
      width: c.key === 'id' ? 100 : c.key === 'name' ? 100 : c.key === 'label' ? 100 : c.key === 'description' ? 150 : c.key === 'status' ? 100 : c.key === 'type' ? 120 : undefined,
      ellipsis: c.key === 'description' ? true : undefined,
      // 新增：如果字段是 id，提供排序函数
      // 修改为：
      sorter: c.key === 'id' ? (a: GraphNode, b: GraphNode) => {
        const aId = a.id ?? '';
        const bId = b.id ?? '';
        return String(aId).localeCompare(String(bId));
      } : undefined,
      filterDropdown: uniqueValues[c.key]
        ? (
            <div style={{ padding: 8 }}>
              <Select
                style={{ width: 120 }}
                placeholder="全部"
                allowClear
                value={filters[c.key]}
                onChange={(v) => setFilters((prev) => ({ ...prev, [c.key]: v }))}
                options={Array.from(uniqueValues[c.key]!).map((v) => ({ label: v, value: v }))}
              />
            </div>
          )
        : undefined,
      filterIcon: uniqueValues[c.key] ? (
        <FilterOutlined style={{ color: filters[c.key] ? '#1677ff' : undefined }} />
      ) : undefined,
      render: (val: unknown) => {
        if (c.key === 'status') {
          return (
            <Tag color={val === 'active' ? 'green' : val === 'inactive' ? 'red' : 'default'}>
              {val === 'active' ? '启用' : val === 'inactive' ? '停用' : String(val || '—')}
            </Tag>
          );
        }
        const s = val == null || val === '' ? '—' : String(val);
        return <Text ellipsis={{ tooltip: s === '—' ? undefined : s }}>{s}</Text>;
      },
    })),
    {
      title: '新增用户',
      dataIndex: 'user_id',
      key: 'user_id',
      width: 100,
      render: (v: unknown) => {
        if (v != null && v !== '') return <Text>{String(v)}</Text>;
        return <Text>—</Text>;
      },
    },
    {
      title: '新增时间',
      dataIndex: 'create_time',
      key: 'create_time',
      width: 100,
      render: (v: unknown) => <Text>{formatTableDateDisplay(v)}</Text>,
    },
    {
      title: '操作',
      key: 'action',
      width: 200,
      fixed: 'right' as const,
      render: (_: unknown, record: GraphNode) => (
        <Space>
          <Button type="link" size="small" icon={<EditOutlined />} onClick={() => openEdit(record)}>
            编辑
          </Button>
          <Button
            type="link"
            size="small"
            icon={<LinkOutlined />}
            onClick={() => setRelationModal({ open: true, node: record })}
          >
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

  return (
    <Card
      title="设备类型管理"
      extra={
        <Space>
          <Input
            placeholder="搜索"
            prefix={<SearchOutlined />}
            value={searchText}
            onChange={(e) => setSearchText(e.target.value)}
            allowClear
            style={{ width: 200 }}
          />
          <Button type="primary" icon={<PlusOutlined />} onClick={openAdd}>
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
        scroll={{ x: 1400 }}
      />

      <Modal
        title={editing ? '编辑设备类型' : '新增设备类型'}
        open={modalVisible}
        onCancel={() => {
          setModalVisible(false);
          form.resetFields();
          setEditing(null);
        }}
        footer={null}
        width={640}
      >
        <Form form={form} layout="vertical" onFinish={handleSubmit} style={{ marginTop: 16 }}>
          <Form.Item
            name="type_category"
            label={LABELS.type_category}
            rules={[{ required: true, message: '请选择一级类别' }]}
            extra="若左侧已选择分类，将自动带出该一级类别；可改为「其他」并输入新的父级标题，保存后分类树会显示新类别。"
          >
            <Select
              showSearch
              allowClear
              options={firstLevelOptions}
              placeholder="请选择"
              optionFilterProp="label"
            />
          </Form.Item>
          {typeCategoryWatch === TYPE_OTHER_VALUE ? (
            <Form.Item
              name="type_custom"
              label={LABELS.type_custom}
              rules={[
                { required: true, message: '请填写父级标题' },
                { whitespace: true, message: '请填写非空白内容' },
              ]}
            >
              <Input placeholder="将作为节点 Type 保存，并作为左侧树的一级分类显示" allowClear maxLength={200} />
            </Form.Item>
          ) : null}

          <Form.Item name="name" label={LABELS.name} rules={[{ required: true, message: '请填写名称' }]}>
            <Input placeholder="名称" />
          </Form.Item>
          <Form.Item name="label" label={LABELS.label}>
            <Input allowClear maxLength={200} />
          </Form.Item>
          <Form.Item name="description" label={LABELS.description}>
            <Input.TextArea rows={2} />
          </Form.Item>
          <Form.Item name="status" label={LABELS.status} rules={[{ required: true, message: '请选择状态' }]}>
            <Select options={STATUS_OPTIONS} />
          </Form.Item>
          <Form.Item name="user_id" label={LABELS.user_id}>
            <Input disabled placeholder="自动" />
          </Form.Item>
          <Form.Item name="create_time" label={LABELS.create_time}>
            <Input disabled placeholder="自动" />
          </Form.Item>
          <Form.Item style={{ textAlign: 'right' }}>
            <Space>
              <Button
                onClick={() => {
                  setModalVisible(false);
                  setEditing(null);
                  form.resetFields();
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
