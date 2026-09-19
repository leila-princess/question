'use client';

/**
 * 设备类型管理：独立实现，不通过 GenericNodePanel，以满足一级类别 / 其他(自定义) / 创建者信息等需求。
 * 与左侧分类树：一级 key 为各节点的 Type 值，二级 key 为节点 Id（见 page.tsx buildTree for EquipmentType）。
 */
import React, { useState, useEffect, useMemo } from 'react';
import { Card, Table, Button, Space, Input, Modal, Form, Select, Tag, message, Popconfirm, Typography } from 'antd';
import { PlusOutlined, EditOutlined, DeleteOutlined, SearchOutlined, LinkOutlined, FilterOutlined } from '@ant-design/icons';
import { fetchNodes, createNode, updateNode, deleteNode, type GraphNode, type NodeType, type EquipmentTreeNode } from '../api';
import { batchDeleteNodes, useBatchRowSelection } from '../hooks/useBatchRowSelection';
import RelationModal from './RelationModal';
import { formatTableDateDisplay } from '../utils/tableDateDisplay';
import { useTablePagination } from '@/utils/useTablePagination';
import {
  DEFAULT_FLGL_STATUS,
  FLGL_STATUS_OPTIONS,
  normalizeStatusValue,
  StatusTag,
} from '../utils/statusDisplay';
import MathAwareTextArea from '@/components/MathAwareTextArea';

const { Text } = Typography;

const nodeType: NodeType = 'EquipmentType';

/** 表单内选择「其他(自定义父级)」时的 value，避免与数据库中重名的「其他」类混淆 */
const TYPE_OTHER_VALUE = '__FLGL_TYPE_OTHER__';

const STATUS_OPTIONS = FLGL_STATUS_OPTIONS;

const FILTERABLE_FIELDS = ['status', 'type'];

const LABELS: Record<string, string> = {
  name: '名称',
  type: '一级类别',
  label: '标签',
  description: '描述',
  status: '状态',
  type_category: '一级类别',
  type_custom: '父级标题',
  user_name: '编辑用户',
  create_time: '编辑时间',
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

/** 根据左侧完整设备树当前选中项解析一级 Type：一级/二级/三级节点都可反推 */
function resolveFirstLevelTypeFromTree(selectedCategory: string | null, list: GraphNode[], tree?: EquipmentTreeNode[]): string | undefined {
  if (!selectedCategory) return undefined;
  const raw = String(selectedCategory);
  if (raw.startsWith('level1_')) return raw.slice('level1_'.length).trim() || undefined;
  if (raw.startsWith('eqtype_')) {
    const typeId = raw.slice('eqtype_'.length).trim();
    const asNode = list.find((n) => String(n.id) === typeId);
    if (asNode?.type) return String(asNode.type).trim() || undefined;
    for (const level1 of tree || []) {
      if (((level1.children || []) as EquipmentTreeNode[]).some((level2) => String(level2.id).trim() === typeId)) {
        return String(level1.name || '').trim() || undefined;
      }
    }
    return undefined;
  }
  if (raw.startsWith('equipment_')) {
    const equipmentId = raw.slice('equipment_'.length).trim();
    for (const level1 of tree || []) {
      for (const level2 of (level1.children || []) as EquipmentTreeNode[]) {
        if (((level2.children || []) as GraphNode[]).some((eq) => String(eq.id).trim() === equipmentId)) {
          return String(level1.name || '').trim() || undefined;
        }
      }
    }
    return undefined;
  }
  const asNode = list.find((n) => String(n.id) === raw);
  if (asNode?.type) return String(asNode.type).trim() || undefined;
  const allTypes = new Set(
    list.map((n) => n.type).filter((t): t is string => !!t && String(t).trim() !== ''),
  );
  if (allTypes.has(raw)) return raw;
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
  equipmentTree,
  onRefresh,
}: {
  allNodes: GraphNode[];
  selectedCategory: string | null;
  categoryNodes?: GraphNode[];
  equipmentTree?: EquipmentTreeNode[];
  onRefresh: () => void;
}) {
  const [nodes, setNodes] = useState<GraphNode[]>([]);
  const [loading, setLoading] = useState(false);
  const [modalVisible, setModalVisible] = useState(false);
  const [editing, setEditing] = useState<GraphNode | null>(null);
  const [searchText, setSearchText] = useState('');
  const [form] = Form.useForm();
  const [relationModal, setRelationModal] = useState<{ open: boolean; node: GraphNode }>({ open: false, node: {} as GraphNode });
  const { selectedRowKeys, rowSelection, clearSelection } = useBatchRowSelection<GraphNode>();
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
      const resolvedType = resolveFirstLevelTypeFromTree(selectedCategory, nodes, equipmentTree);
      const normalized = selectedCategory.startsWith('level1_')
        ? selectedCategory.slice('level1_'.length)
        : selectedCategory.startsWith('eqtype_')
          ? selectedCategory.slice('eqtype_'.length)
          : selectedCategory.startsWith('equipment_')
            ? selectedCategory.slice('equipment_'.length)
            : selectedCategory;
      const asNode = nodes.find((n) => String(n.id) === String(normalized));
      if (resolvedType) {
        result = result.filter((n) => String(n.type || '') === resolvedType);
      } else if (asNode) {
        result = result.filter((n) => n.id === normalized);
      } else {
        result = result.filter((n) => String(n.type || '') === String(normalized));
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
  }, [nodes, selectedCategory, searchText, filters, equipmentTree]);

  const tablePagination = useTablePagination(filtered.length, {
    resetDeps: [searchText, selectedCategory, filters],
  });

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
          status: normalizeStatusValue(values.status),
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
          status: normalizeStatusValue(values.status),
          user_name: uname,
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

  const handleBatchDelete = async () => {
    const ids = selectedRowKeys.map(String).filter(Boolean);
    if (!ids.length) return;
    setLoading(true);
    try {
      const { ok, failed } = await batchDeleteNodes(nodeType, ids, deleteNode);
      if (ok) message.success(`已删除 ${ok} 项`);
      if (failed.length) message.warning(`${failed.length} 项删除失败`);
      clearSelection();
      await loadData();
      onRefresh();
    } finally {
      setLoading(false);
    }
  };

  const openAdd = () => {
    const def = resolveFirstLevelTypeFromTree(selectedCategory, nodes, equipmentTree);
    if (!def) {
      message.warning('请先在左侧选择一级设备类别');
      return;
    }
    setEditing(null);
    const userName = getCurrentUserName();
    const now = getCurrentTime();
    if (def && firstLevelTypes.includes(def)) {
      form.setFieldsValue({
        type_category: def,
        type_custom: '',
        name: '',
        label: '',
        description: '',
        status: DEFAULT_FLGL_STATUS,
        user_name: userName,
        create_time: now,
      });
    } else if (def) {
      form.setFieldsValue({
        type_category: TYPE_OTHER_VALUE,
        type_custom: def,
        name: '',
        label: '',
        description: '',
        status: DEFAULT_FLGL_STATUS,
        user_name: userName,
        create_time: now,
      });
    } else {
      form.setFieldsValue({
        type_category: undefined,
        type_custom: '',
        name: '',
        label: '',
        description: '',
        status: DEFAULT_FLGL_STATUS,
        user_name: userName,
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
      status: normalizeStatusValue(record.status),
      user_name: (record as { user_name?: string }).user_name || (record as { user_name?: string }).user_name,
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
          return <StatusTag value={val} />;
        }
        const s = val == null || val === '' ? '—' : String(val);
        return <Text ellipsis={{ tooltip: s === '—' ? undefined : s }}>{s}</Text>;
      },
    })),
    {
      title: '编辑用户',
      dataIndex: 'user_name',
      key: 'user_name',
      width: 100,
      render: (v: unknown) => {
        if (v != null && v !== '') return <Text>{String(v)}</Text>;
        return <Text>—</Text>;
      },
    },
    {
      title: '编辑时间',
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
          {/* 关联操作暂时下线，后续仍会启用
          <Button
            type="link"
            size="small"
            icon={<LinkOutlined />}
            onClick={() => setRelationModal({ open: true, node: record })}
          >
            关联
          </Button>
          */}
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
          {selectedRowKeys.length > 0 && (
            <Popconfirm title={`确定删除选中的 ${selectedRowKeys.length} 项？`} onConfirm={() => void handleBatchDelete()}>
              <Button danger icon={<DeleteOutlined />}>批量删除</Button>
            </Popconfirm>
          )}
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
        rowSelection={rowSelection}
        loading={loading}
        size="small"
        pagination={tablePagination}
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
            extra="一级类别已锁定，不可编辑"
          >
            <Select
              showSearch
              allowClear={false}
              disabled
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
              <Input placeholder="将作为节点 Type 保存，并作为左侧树的一级分类显示" allowClear={false} disabled maxLength={200} />
            </Form.Item>
          ) : null}

          <Form.Item name="name" label={LABELS.name} rules={[{ required: true, message: '请填写名称' }]}>
            <Input placeholder="名称" />
          </Form.Item>
          <Form.Item name="label" label={LABELS.label}>
            <Input allowClear maxLength={200} />
          </Form.Item>
          <Form.Item name="description" label={LABELS.description}>
            <MathAwareTextArea rows={2} />
          </Form.Item>
          <Form.Item name="status" label={LABELS.status} rules={[{ required: true, message: '请选择状态' }]}>
            <Select options={STATUS_OPTIONS} />
          </Form.Item>
          <Form.Item name="user_name" label={LABELS.user_name}>
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
