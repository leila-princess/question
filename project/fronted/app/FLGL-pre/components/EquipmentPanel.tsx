'use client';

import React, { useState, useEffect, useMemo, useCallback } from 'react';
import { Card, Table, Button, Space, Input, Modal, Form, Select, Tag, message, Popconfirm } from 'antd';
import type { DefaultOptionType } from 'antd/es/select';
import { PlusOutlined, EditOutlined, DeleteOutlined, SearchOutlined, LinkOutlined } from '@ant-design/icons';
import {
  fetchNodes,
  createNode,
  updateNode,
  deleteNode,
  fetchNodeRelations,
  createRelations,
  deleteRelation,
  type NodeType,
  type GraphNode,
} from '../api';
import RelationModal from './RelationModal';
import { formatTableDateDisplay } from '../utils/tableDateDisplay';

const LABEL_MAP: Record<string, string> = {
  id: 'ID',
  name: '名称',
  type: '类别',
  label: '标签',
  description: '描述',
  status: '状态',
  user_name: '上传用户名',
  create_time: '上传时间',
};

const STATUS_OPTIONS = [
  { label: '启用', value: 'active' },
  { label: '停用', value: 'inactive' },
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

function isSecondaryTreeKey(key: string | null | undefined): key is string {
  return !!key && key.startsWith('eqtype_');
}

function isPrimaryTreeKey(key: string | null | undefined): key is string {
  return !!key && key.startsWith('type_');
}

function equipmentTypeIdFromTreeKey(key: string): string {
  return key.slice('eqtype_'.length).trim();
}

interface EquipmentPanelProps {
  allNodes: GraphNode[];
  selectedCategory: string | null;
  categoryNodes?: GraphNode[];
  onRefresh: () => void;
}

export default function EquipmentPanel({ allNodes: _allNodes, selectedCategory, categoryNodes, onRefresh }: EquipmentPanelProps) {
  const [nodes, setNodes] = useState<GraphNode[]>([]);
  const [loading, setLoading] = useState(false);
  const [modalVisible, setModalVisible] = useState(false);
  const [editing, setEditing] = useState<GraphNode | null>(null);
  const [searchText, setSearchText] = useState('');
  const [form] = Form.useForm();
  const [relationModal, setRelationModal] = useState<{ open: boolean; node: GraphNode }>({ open: false, node: {} as GraphNode });
  /** 新增时若左侧已点选二级设备类型，则表单中设备类型锁定为该项 */
  const [typeLockedFromTree, setTypeLockedFromTree] = useState(false);

  const nodeType: NodeType = 'Equipment';

  const leafIds = useMemo(
    () => new Set((categoryNodes || []).map((n) => String(n.id || '').trim()).filter(Boolean)),
    [categoryNodes],
  );

  /** 与左侧设备分类树一致：一级为 type 分组，可选值为二级 EquipmentType 的 id */
  const equipmentTypeGroupedOptions = useMemo(() => {
    const list = categoryNodes || [];
    if (!list.length) return [] as DefaultOptionType[];
    const types = Array.from(new Set(list.map((n) => String(n.type || '').trim()).filter(Boolean)));
    const typeFilter = isPrimaryTreeKey(selectedCategory) ? selectedCategory.slice('type_'.length) : null;
    const typesFiltered = typeFilter ? types.filter((t) => t === typeFilter) : types;
    return typesFiltered
      .map((t) => ({
        label: t,
        options: list
          .filter((n) => String(n.type || '').trim() === t)
          .map((n) => ({ label: (n.name || n.id) as string, value: n.id as string })),
      }))
      .filter((g) => (g.options?.length ?? 0) > 0);
  }, [categoryNodes, selectedCategory]);

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

  /** 与分类树二级节点 id 对应 */
  function linkedEquipmentTypeId(n: GraphNode): string {
    const r = n as Record<string, unknown>;
    const single =
      r.equipment_type_id ?? r.equipmenttypeid ?? r.equipmentTypeId ?? r.equipmenttypeId;
    if (single != null && String(single).trim() !== '') return String(single).trim();
    const arr = r.equipment_type_ids;
    if (Array.isArray(arr) && arr.length > 0 && arr[0] != null) return String(arr[0]).trim();
    return '';
  }

  const syncEquipmentTypeRelation = useCallback(async (equipmentId: string, newTypeId: string) => {
    const rels = await fetchNodeRelations(nodeType, equipmentId);
    for (const r of rels) {
      const labels = r.target_labels || [];
      if (!labels.some((l) => l === 'EquipmentType')) continue;
      if (r.rel_type !== 'CON' && r.rel_type !== 'HAS') continue;
      try {
        await deleteRelation(equipmentId, r.target_id, r.rel_type, r.direction);
      } catch {
        /* 忽略重复删 */
      }
    }
    if (newTypeId) {
      await createRelations(equipmentId, [{ target_id: newTypeId, rel_type: 'CON', direction: 'incoming' }]);
    }
  }, [nodeType]);

  const handleSubmit = async (values: Record<string, unknown>) => {
    const etId = String(values.equipment_type_id || '').trim();
    if (etId.startsWith('type_')) {
      message.error('只能选择下一级设备类型，不能选择一级分类');
      return;
    }
    if (!leafIds.has(etId)) {
      message.error('请选择有效的二级设备类型');
      return;
    }
    const etNode = (categoryNodes || []).find((c) => String(c.id).trim() === etId);
    if (!etNode) {
      message.error('请选择有效的二级设备类型');
      return;
    }

    setLoading(true);
    try {
      const currentUser = getCurrentUser();
      const now = getCurrentTime();
      const typeName = String(etNode.name || etNode.id || '').trim();

      const submitData: Record<string, unknown> = {
        name: String(values.name || '').trim(),
        type: typeName,
        label: String(values.label ?? '').trim(),
        description: values.description,
        status: values.status,
        user_name: currentUser.userName,
        create_time: now,
      };

      if (editing) {
        await updateNode(nodeType, editing.id, submitData);
        const prevEt = linkedEquipmentTypeId(editing);
        if (prevEt !== etId) {
          await syncEquipmentTypeRelation(editing.id, etId);
        }
        message.success('修改成功');
      } else {
        const created = await createNode(nodeType, submitData);
        const newId = created?.id != null ? String(created.id).trim() : '';
        if (newId && etId) {
          try {
            await createRelations(newId, [{ target_id: etId, rel_type: 'CON', direction: 'incoming' }]);
          } catch {
            message.warning('设备已保存，但关联设备类型失败，请在「关联」中手动关联');
          }
        }
        message.success('新增成功');
      }
      setModalVisible(false);
      form.resetFields();
      setEditing(null);
      setTypeLockedFromTree(false);
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

    if (selectedCategory) {
      if (selectedCategory.startsWith('eqtype_')) {
        const catId = selectedCategory.slice(7).trim();
        const catNode = categoryNodes?.find((c) => String(c.id).trim() === catId);
        result = result.filter((n) => {
          const etid = linkedEquipmentTypeId(n);
          if (catId && etid && etid === catId) return true;
          if (catNode?.name && (n.type === catNode.name || n.name === catNode.name)) return true;
          return false;
        });
      } else if (categoryNodes?.some((c) => String(c.id).trim() === String(selectedCategory).trim())) {
        const catId = String(selectedCategory).trim();
        const catNode = categoryNodes?.find((c) => String(c.id).trim() === catId);
        result = result.filter((n) => {
          const etid = linkedEquipmentTypeId(n);
          if (catId && etid && etid === catId) return true;
          if (catNode?.name && n.type === catNode.name) return true;
          return false;
        });
      } else if (selectedCategory.startsWith('type_')) {
        const typeKey = selectedCategory.slice(5);
        result = result.filter((n) => {
          const cat = (n as { equipment_type_category?: string }).equipment_type_category;
          if (cat && cat === typeKey) return true;
          const etid = (n as { equipment_type_id?: string }).equipment_type_id;
          const matchTypeNode = categoryNodes?.some((cn) => cn.type === typeKey && cn.id === etid);
          if (matchTypeNode) return true;
          return n.type === typeKey;
        });
      }
    }

    if (searchText) {
      result = result.filter((n) => (n.name || '').toLowerCase().includes(searchText.toLowerCase()));
    }

    return result;
  }, [nodes, selectedCategory, searchText, categoryNodes]);

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
    { title: '类别', dataIndex: 'type', key: 'eq_type', width: 120, ellipsis: true },
    {
      title: LABEL_MAP.label,
      dataIndex: 'label',
      key: 'label',
      width: 100,
      ellipsis: true,
      render: (val: string) => (val && String(val).trim() ? val : '—'),
    },
    { title: '描述', dataIndex: 'description', key: 'description', ellipsis: true,width: 200, },
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
              setTypeLockedFromTree(false);
              const etId = linkedEquipmentTypeId(record);
              const lbl = String((record as GraphNode & { label?: string }).label ?? '').trim();
              form.setFieldsValue({
                name: record.name,
                equipment_type_id: etId || undefined,
                label: lbl || undefined,
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

    if (isSecondaryTreeKey(selectedCategory)) {
      const etid = equipmentTypeIdFromTreeKey(selectedCategory);
      setTypeLockedFromTree(true);
      form.setFieldsValue({
        name: '',
        equipment_type_id: etid,
        label: '',
        description: '',
        status: 'active',
        user_name: currentUser.userName,
        create_time: now,
      });
    } else {
      setTypeLockedFromTree(false);
      form.setFieldsValue({
        name: '',
        equipment_type_id: undefined,
        label: '',
        description: '',
        status: 'active',
        user_name: currentUser.userName,
        create_time: now,
      });
    }
    setModalVisible(true);
  };

  return (
    <Card
      title="设备管理"
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
        scroll={{ x: 1300 }}
      />

      <Modal
        title={`${editing ? '编辑' : '新增'}设备`}
        open={modalVisible}
        onCancel={() => {
          setModalVisible(false);
          form.resetFields();
          setEditing(null);
          setTypeLockedFromTree(false);
        }}
        footer={null}
        width={600}
      >
        <Form form={form} layout="vertical" onFinish={handleSubmit} style={{ marginTop: 16 }}>
          <Form.Item label={LABEL_MAP.name} name="name" rules={[{ required: true, message: '请输入名称' }]}>
            <Input placeholder="请输入设备名称" />
          </Form.Item>

          <Form.Item
            label="设备类型（须选二级分类）"
            name="equipment_type_id"
            extra={
              typeLockedFromTree
                ? '已从左侧分类树二级节点自动带出'
                : '结构与左侧分类树一致：仅可选择二级设备类型，不可选择一级分类'
            }
            rules={[
              { required: true, message: '请选择二级设备类型' },
              {
                validator: async (_, value) => {
                  const v = String(value ?? '').trim();
                  if (!v) return Promise.reject(new Error('请选择二级设备类型'));
                  if (v.startsWith('type_')) {
                    return Promise.reject(new Error('只能选择下一级设备类型，不能选择一级分类'));
                  }
                  if (!leafIds.has(v)) {
                    return Promise.reject(new Error('请选择有效的二级设备类型'));
                  }
                  return Promise.resolve();
                },
              },
            ]}
          >
            <Select
              placeholder="请选择二级设备类型"
              options={equipmentTypeGroupedOptions}
              showSearch
              optionFilterProp="label"
              disabled={typeLockedFromTree && !editing}
              allowClear={!typeLockedFromTree || !!editing}
            />
          </Form.Item>

          <Form.Item label={LABEL_MAP.label} name="label">
            <Input placeholder="请输入标签" allowClear maxLength={200} showCount />
          </Form.Item>

          <Form.Item label={LABEL_MAP.description} name="description">
            <Input.TextArea rows={3} placeholder="请输入设备描述" />
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
                  setTypeLockedFromTree(false);
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
