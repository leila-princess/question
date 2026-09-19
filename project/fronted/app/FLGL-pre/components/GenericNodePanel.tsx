'use client';

import React, { useState, useEffect, useMemo } from 'react';
import { Card, Table, Button, Space, Input, Modal, Form, Select, Tag, message, Popconfirm, Typography } from 'antd';
import { PlusOutlined, EditOutlined, DeleteOutlined, SearchOutlined, LinkOutlined, FilterOutlined } from '@ant-design/icons';
import { fetchNodes, createNode, updateNode, deleteNode, type NodeType, type GraphNode } from '../api';
import { formatTableDateDisplay } from '../utils/tableDateDisplay';
import RelationModal from './RelationModal';

const { Text } = Typography;
const { Option } = Select;

const ENV_MAP: Record<string, string> = {
  'HJ_0001': '复杂电磁环境', 'HJ_0002': '复杂地理环境', 'HJ_0003': '复杂气象环境',
  'HJ_0004': '复杂水文环境', 'HJ_0005': '复杂水声环境', 'HJ_0006': '高原综合环境',
  'HJ_0007': '海上综合环境', 'HJ_0008': '其他环节',
};

const LABEL_MAP: Record<string, string> = {
  id: 'ID', name: '名称', label: '标签', description: '描述', type: '类型',
  status: '状态', user_id: '用户', create_time: '创建时间',
  environmentid: '环境ID', class: '类别', combination: '典型组合',
  limit: '限制', request: '要求', sy: '术语',
  source_name: '来源名称', source_id: '来源ID',
  chroma_id: 'Chroma ID', minio_id: 'MinIO ID',
  image_true: '图像', image_minioid: '图像MinIO',
  equipment_type: '设备类型', equipment_type_ids: '设备类型ID', equipment_type_names: '设备类型',
};

const TEXTAREA_FIELDS = ['description', 'limit', 'request', 'combination', 'sy'];

function getCurrentUserName(): string {
  if (typeof window === 'undefined') return '系统管理员';
  const userStr = localStorage.getItem('userInfo');
  if (userStr) {
    try {
      const user = JSON.parse(userStr) as { userName?: string; name?: string };
      if (user.userName) return String(user.userName);
      if (user.name) return String(user.name);
    } catch { /* empty */ }
  }
  return '系统管理员';
}

function getCurrentTimeString(): string {
  const d = new Date();
  return d.toISOString().replace('T', ' ').substring(0, 19);
}

const SELECT_FIELDS: Record<string, { options: Array<{ label: string; value: string }> }> = {
  status: { options: [{ label: '启用', value: 'active' }, { label: '停用', value: 'inactive' }] },
  environmentid: { options: Object.entries(ENV_MAP).map(([k, v]) => ({ label: v, value: k })) },
};

const FILTERABLE_FIELDS = ['status', 'type'];

/** 环境表格：列顺序（不含用户、创建时间、操作） */
const ENV_TABLE_COLUMN_ORDER = [
  'name', 'id', 'description', 'class', 'type', 'limit', 'request', 'combination', 'status',
] as const;

const ENV_TABLE_TITLE: Record<string, string> = {
  name: '名称',
  id: 'ID',
  description: '描述',
  class: '类别',
  type: '类型',
  limit: '约束',
  request: '要求',
  combination: '组合',
  status: '状态',
};

const ENV_COLUMN_WIDTH: Record<string, number> = {
  name: 100,
  id: 88,
  description: 120,
  class: 120,
  type: 120,
  limit: 140,
  request: 140,
  combination: 140,
  status: 88,
};

/** 试验方法：表格列顺序 */
const METHOD_TABLE_COLUMN_ORDER = [
  'name', 'id', 'description', 'source_name', 'sytj', 'sygc', 'jgpd', 'status',
] as const;

const METHOD_TABLE_TITLE: Record<string, string> = {
  name: '名称',
  id: 'ID',
  description: '描述',
  source_name: '来源文件名称',
  sytj: '试验条件',
  sygc: '试验过程',
  jgpd: '结果评定',
  status: '状态',
};

const METHOD_COLUMN_WIDTH: Record<string, number> = {
  name: 120,
  id: 90,
  description: 120,
  source_name: 130,
  sytj: 120,
  sygc: 120,
  jgpd: 120,
  status: 80,
};

export default function GenericNodePanel({
  nodeType, title, allNodes, selectedCategory, categoryNodes, onRefresh, extraButtons, refreshKey, methodCategoryMap,
}: {
  nodeType: NodeType; title: string; allNodes: GraphNode[];
  selectedCategory: string | null; categoryNodes?: GraphNode[];
  onRefresh: () => void;
  extraButtons?: React.ReactNode;
  refreshKey?: number;
  /** 方法 Tab：后端 /method-categories 返回的 id -> 分类 key 列表，用于左侧树过滤 */
  methodCategoryMap?: Record<string, string[]>;

}) {
  const [nodes, setNodes] = useState<GraphNode[]>([]);
  const [loading, setLoading] = useState(false);
  const [modalVisible, setModalVisible] = useState(false);
  const [editing, setEditing] = useState<GraphNode | null>(null);
  const [searchText, setSearchText] = useState('');
  const [form] = Form.useForm();
  const [relationModal, setRelationModal] = useState<{ open: boolean; node: GraphNode }>({ open: false, node: {} as GraphNode });
  const [fields, setFields] = useState<string[]>([]);
  const [filters, setFilters] = useState<Record<string, string | undefined>>({});
  const isEnv = nodeType === 'Environment';
  const isMethod = nodeType === 'TestMethod';
  const [fileNodes, setFileNodes] = useState<GraphNode[]>([]);

  useEffect(() => { loadData(); }, [refreshKey]);

  useEffect(() => {
    if (!isMethod) return;
    (async () => {
      try {
        const data = await fetchNodes('File');
        setFileNodes(Array.isArray(data) ? data : []);
      } catch { /* 文件列表仅用于方法表单下拉，失败不阻断 */ }
    })();
  }, [isMethod, refreshKey, modalVisible]);

  const fileSelectOptions = useMemo(
    () =>
      fileNodes.map((f) => ({
        value: f.id,
        label: (f.name || f.label || f.id) as string,
      })),
    [fileNodes],
  );

  const loadData = async () => {
    setLoading(true);
    try {
      const data = await fetchNodes(nodeType);
      const list = Array.isArray(data) ? data : [];
      setNodes(list);
      if (list.length > 0 && fields.length === 0) {
        const sample = list[0];
        const fieldList = Object.keys(sample).filter(k => k !== '_labels');
        setFields(fieldList);
      }
    } catch { message.error('加载失败'); }
    finally { setLoading(false); }
  };

  const handleSubmit = async (values: any) => {
    setLoading(true);
    try {
      if (isEnv || isMethod) {
        const { user_id: _u, create_time: _c, ...rest } = values;
        if (editing) {
          await updateNode(nodeType, editing.id, rest);
          message.success('修改成功');
        } else {
          await createNode(nodeType, { ...rest, user_id: getCurrentUserName() });
          message.success('新增成功');
        }
      } else if (editing) {
        await updateNode(nodeType, editing.id, values);
        message.success('修改成功');
      } else {
        await createNode(nodeType, values);
        message.success('新增成功');
      }
      setModalVisible(false);
      form.resetFields();
      setEditing(null);
      loadData();
      onRefresh();
       } catch (err: any) {
         const msg = err?.message || '';
         if (msg.includes('名称已存在')) {
           message.error('名称已存在，新增失败');
         } else {
           message.error('操作失败');
         }
       }
    finally { setLoading(false); }
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
    if (selectedCategory && isMethod) {
      const hasMap = methodCategoryMap && Object.keys(methodCategoryMap).length > 0;
      if (hasMap) {
        result = result.filter((n) => {
          const keys = methodCategoryMap![n.id] || [];
          return keys.includes(selectedCategory);
        });
      }
    } else if (selectedCategory) {
      const rawKey = selectedCategory.startsWith('eqtype_') ? selectedCategory.slice(7) : selectedCategory;
      const catNode = categoryNodes?.find(n => n.id === rawKey);
      if (catNode && nodeType === 'Equipment') {
        result = result.filter(n =>
          (n.equipment_type === catNode.name) ||
          (n.equipment_type_ids || []).includes(catNode.id)
        );
      } else {
        result = result.filter(n => n.id === selectedCategory || n.environmentid === selectedCategory);
      }
    }
    if (searchText) {
      result = result.filter(n =>
        (n.name || '').includes(searchText) || (n.id || '').includes(searchText)
      );
    }
    for (const [field, value] of Object.entries(filters)) {
      if (value) {
        result = result.filter(n => n[field] === value);
      }
    }
    return result;
  }, [nodes, selectedCategory, searchText, filters, categoryNodes, isMethod, methodCategoryMap, nodeType]);

  const displayFields = useMemo(() => {
    const base = fields.length > 0 ? fields : ['id', 'name', 'type', 'description', 'status'];
    const priority = ['name', 'id', 'description'];
    const rest = base.filter(f => !priority.includes(f));
    return [...priority.filter(p => base.includes(p)), ...rest];
  }, [fields]);

  const tableFields = useMemo(() => {
    const dropTime = (f: string) => !['user_id', 'create_time'].includes(f);
    if (isMethod) {
      return [...METHOD_TABLE_COLUMN_ORDER];
    }
    if (isEnv) {
      const available = fields.length > 0
        ? new Set(fields.filter((k) => k !== 'label'))
        : new Set(ENV_TABLE_COLUMN_ORDER as unknown as string[]);
      return ENV_TABLE_COLUMN_ORDER.filter((f) => available.has(f));
    }
    return displayFields.filter(dropTime);
  }, [displayFields, isEnv, isMethod, fields]);
  const compactFields = new Set(['description', 'limit', 'request', 'combination', 'sy', 'source_name', 'sytj', 'sygc', 'jgpd']);
  const compactWidths: Record<string, number> = {
    id: 100,
    name: 150,
    type: 120,
    status: 90,
    source_name: 160,
    create_time: 150,
    description: 100,
    limit: 180,
    request: 180,
    combination: 160,
    sy: 160,
  };

  const uniqueValues = useMemo(() => {
    const result: Record<string, Set<string>> = {};
    for (const field of FILTERABLE_FIELDS) {
      const values = new Set<string>();
      for (const n of nodes) {
        if (n[field]) values.add(String(n[field]));
      }
      if (values.size > 0) result[field] = values;
    }
    return result;
  }, [nodes]);

  const envTableScrollX = useMemo((): number | undefined => {
    if (!isEnv) return undefined;
    const dataW = tableFields.reduce((s, f) => s + (ENV_COLUMN_WIDTH[f] ?? compactWidths[f] ?? 120), 0);
    return dataW + 100 + 120 + 200;
  }, [isEnv, tableFields]);

  const methodTableScrollX = useMemo((): number | undefined => {
    if (!isMethod) return undefined;
    const dataW = tableFields.reduce((s, f) => s + (METHOD_COLUMN_WIDTH[f] ?? compactWidths[f] ?? 120), 0);
    return dataW + 100 + 100 + 200;
  }, [isMethod, tableFields]);

  const columns = [
    ...tableFields.map(f => ({
      title: isEnv
        ? (ENV_TABLE_TITLE[f] ?? LABEL_MAP[f] ?? f)
        : isMethod
          ? (METHOD_TABLE_TITLE[f] ?? LABEL_MAP[f] ?? f)
          : (LABEL_MAP[f] || f),
      dataIndex: f,
      key: f,
      width: isEnv
        ? (ENV_COLUMN_WIDTH[f] ?? compactWidths[f] ?? 120)
        : isMethod
          ? (METHOD_COLUMN_WIDTH[f] ?? compactWidths[f] ?? 120)
          : compactWidths[f],
      ellipsis: compactFields.has(f) || f === 'name',
      // 新增：如果字段是 id，提供排序函数
      sorter: f === 'id' ? (a: GraphNode, b: GraphNode) => {
        const aId = a.id ?? '';
        const bId = b.id ?? '';
        return String(aId).localeCompare(String(bId));
      } : undefined,
      filterDropdown: uniqueValues[f] ? (
        <div style={{ padding: 8 }}>
          <Select
            style={{ width: 160 }}
            placeholder="全部"
            allowClear
            value={filters[f]}
            onChange={(v) => setFilters(prev => ({ ...prev, [f]: v }))}
            options={Array.from(uniqueValues[f]!).map(v => ({ label: v, value: v }))}
          />
        </div>
      ) : undefined,
      filterIcon: uniqueValues[f] ? <FilterOutlined style={{ color: filters[f] ? '#1677ff' : undefined }} /> : undefined,
      render: (val: any, record: GraphNode) => {
        if (f === 'status') {
          return <Tag color={val === 'active' ? 'green' : val === 'inactive' ? 'red' : 'default'}>{val === 'active' ? '启用' : val === 'inactive' ? '停用' : val || '—'}</Tag>;
        }
        if (f === 'environmentid') {
          return <Tag>{ENV_MAP[val] || val || '—'}</Tag>;
        }
        if (f === 'equipment_type_names' && Array.isArray(val)) {
          return <Space wrap>{val.map((v: string) => <Tag key={v}>{v}</Tag>)}</Space>;
        }
        return <Text ellipsis={{ tooltip: val }}>{val || '—'}</Text>;
      },
    })),
    {
      title: '用户', dataIndex: 'user_id', key: 'user_id', width: 90,
      render: (v: string) => <Text>{v || '—'}</Text>,
    },
    {
      title: '创建时间', dataIndex: 'create_time', key: 'create_time', width: 100,
      render: (v: string) =>
        isEnv || isMethod ? <Text>{formatTableDateDisplay(v)}</Text> : <Text>{v || '—'}</Text>,
    },
    {
      title: '操作', key: 'action', width: 200, fixed: 'right' as const,
      render: (_: any, record: GraphNode) => (
        <Space>
          <Button
            type="link"
            size="small"
            icon={<EditOutlined />}
            onClick={() => {
              setEditing(record);
              if (isEnv || isMethod) {
                const ct = (record as GraphNode & { create_time?: string }).create_time;
                form.setFieldsValue({
                  ...record,
                  user_id: getCurrentUserName(),
                  create_time: ct != null && String(ct).trim() ? String(ct) : getCurrentTimeString(),
                });
              } else {
                form.setFieldsValue(record);
              }
              setModalVisible(true);
            }}
          >
            编辑
          </Button>
          <Button type="link" size="small" icon={<LinkOutlined />} onClick={() => setRelationModal({ open: true, node: record })}>关联</Button>
          <Popconfirm title="确定删除？" onConfirm={() => handleDelete(record.id)}>
            <Button type="link" danger size="small" icon={<DeleteOutlined />}>删除</Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <Card
      title={title}
      extra={
        <Space>
          <Input placeholder="搜索" prefix={<SearchOutlined />} value={searchText} onChange={e => setSearchText(e.target.value)} allowClear style={{ width: 200 }} />
          {extraButtons}
          <Button
            type="primary"
            icon={<PlusOutlined />}
            onClick={() => {
              setEditing(null);
              form.resetFields();
              if (isEnv) {
                form.setFieldsValue({ user_id: getCurrentUserName(), create_time: getCurrentTimeString() });
              } else if (isMethod) {
                form.setFieldsValue({
                  user_id: getCurrentUserName(),
                  create_time: getCurrentTimeString(),
                  status: 'active',
                  type: '分则',
                });
              }
              setModalVisible(true);
            }}
          >
            新增
          </Button>
        </Space>
      }
    >
      <Table
        tableLayout={isEnv || isMethod ? 'fixed' : undefined}
        scroll={
          isEnv && envTableScrollX != null
            ? { x: envTableScrollX }
            : isMethod && methodTableScrollX != null
              ? { x: methodTableScrollX }
              : undefined
        }
        columns={columns}
        dataSource={filtered}
        rowKey="id"
        loading={loading}
        size="small"
        pagination={{ pageSize: 15, showSizeChanger: true, showTotal: (t) => `共 ${t} 条` }}
      />

      <Modal
        title={`${editing ? '编辑' : '新增'}${title.replace('管理', '')}`}
        open={modalVisible}
        onCancel={() => { setModalVisible(false); form.resetFields(); setEditing(null); }}
        footer={null}
        width={isMethod ? 720 : isEnv ? 640 : 600}
      >
        <Form form={form} layout="vertical" onFinish={handleSubmit} style={{ marginTop: 16 }}>
          {isMethod ? (
            <>
              <Form.Item name="type" hidden>
                <Input type="hidden" />
              </Form.Item>
              <Form.Item name="source_name" hidden>
                <Input type="hidden" />
              </Form.Item>
              <Form.Item label="名称" name="name" rules={[{ required: true, message: '请输入名称' }]}>
                <Input />
              </Form.Item>
              <Form.Item label="描述" name="description">
                <Input.TextArea rows={3} autoSize={{ minRows: 2, maxRows: 8 }} />
              </Form.Item>
              <Form.Item label="标签" name="label">
                <Input />
              </Form.Item>
              <Form.Item label="来源文件" name="source_id">
                <Select
                  showSearch
                  allowClear
                  placeholder="选择文件节点"
                  optionFilterProp="label"
                  options={fileSelectOptions}
                  onChange={(id) => {
                    const f = fileNodes.find((x) => x.id === id);
                    form.setFieldsValue({
                      source_name: f ? String(f.name || f.label || '') : '',
                    });
                  }}
                />
              </Form.Item>
              <Form.Item label="试验条件" name="sytj">
                <Input.TextArea autoSize={{ minRows: 3, maxRows: 12 }} />
              </Form.Item>
              <Form.Item label="试验过程" name="sygc">
                <Input.TextArea autoSize={{ minRows: 3, maxRows: 12 }} />
              </Form.Item>
              <Form.Item label="结果评定" name="jgpd">
                <Input.TextArea autoSize={{ minRows: 3, maxRows: 12 }} />
              </Form.Item>
              <Form.Item label="状态" name="status" rules={[{ required: true }]}>
                <Select options={SELECT_FIELDS.status.options} />
              </Form.Item>
              <Form.Item label="修改用户" name="user_id" rules={[{ required: true }]}>
                <Input readOnly />
              </Form.Item>
              <Form.Item label="修改时间" name="create_time">
                <Input readOnly />
              </Form.Item>
            </>
          ) : (
            <>
              {displayFields.map(f => {
                if (f === 'create_time' || f === 'user_id') return null;
                if (f === 'id' && !editing) return null;

                if (TEXTAREA_FIELDS.includes(f) || (isEnv && (f === 'class' || f === 'type'))) {
                  const isEnvClassType = isEnv && (f === 'class' || f === 'type');
                  return (
                    <Form.Item key={f} label={LABEL_MAP[f] || f} name={f}>
                      <Input.TextArea
                        rows={isEnvClassType ? undefined : 2}
                        autoSize={isEnvClassType ? { minRows: 3, maxRows: 12 } : undefined}
                      />
                    </Form.Item>
                  );
                }
                if (SELECT_FIELDS[f]) {
                  return <Form.Item key={f} label={LABEL_MAP[f] || f} name={f} rules={f === 'name' ? [{ required: true }] : undefined}><Select options={SELECT_FIELDS[f].options} /></Form.Item>;
                }
                return <Form.Item key={f} label={LABEL_MAP[f] || f} name={f} rules={f === 'name' ? [{ required: true }] : undefined}><Input disabled={f === 'id' && !!editing} /></Form.Item>;
              })}
              {isEnv && (
                <>
                  <Form.Item label="新增/编辑用户" name="user_id" rules={[{ required: true }]}>
                    <Input readOnly />
                  </Form.Item>
                  <Form.Item label="修改时间" name="create_time">
                    <Input readOnly />
                  </Form.Item>
                </>
              )}
            </>
          )}
          <Form.Item style={{ textAlign: 'right' }}>
            <Space><Button onClick={() => { setModalVisible(false); setEditing(null); }}>取消</Button><Button type="primary" htmlType="submit" loading={loading}>保存</Button></Space>
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
