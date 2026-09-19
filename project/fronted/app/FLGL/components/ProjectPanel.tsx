'use client';

import React, { useState, useEffect, useMemo } from 'react';
import { Card, Table, Button, Space, Input, Modal, Form, Select, Tag, message, Popconfirm } from 'antd';
import { PlusOutlined, EditOutlined, DeleteOutlined, SearchOutlined, LinkOutlined, FilterOutlined } from '@ant-design/icons';
import { fetchNodes, createNode, updateNode, deleteNode, type NodeType, type GraphNode } from '../api';
import { batchDeleteNodes, useBatchRowSelection } from '../hooks/useBatchRowSelection';
import RelationModal from './RelationModal';
import { ENVIRONMENT_ORDER } from '@/constants/environments';
import { formatTableDateDisplay } from '../utils/tableDateDisplay';
import EquipmentNameLinks from './EquipmentNameLinks';
import { useTablePagination } from '@/utils/useTablePagination';
import {
  DEFAULT_FLGL_STATUS,
  FLGL_STATUS_OPTIONS,
  normalizeStatusValue,
  StatusTag,
} from '../utils/statusDisplay';
import MathAwareTextArea from '@/components/MathAwareTextArea';
import {
  LEVEL_TYPE_FILTER_OPTIONS,
  type LevelTypeLabel,
  matchesLevelTypeFilter,
  resolveLevelTypeLabel,
} from '../utils/levelTypeUtils';

const LABEL_MAP: Record<string, string> = {
  id: 'ID',
  name: '名称',
  label: '环境类别',
  type: '层级',
  parent_id: '父项目',
  equipment_id: '关联设备',
  environment_id: '关联环境',
  description: '描述',
  status: '状态',
  user_name: '编辑用户',
  create_time: '编辑时间',
};

const STATUS_OPTIONS = FLGL_STATUS_OPTIONS;

/** 表单展示中文，提交给后端为 category（父）/ test（子） */
const TYPE_OPTIONS = [
  { label: '父级', value: 'category' },
  { label: '子级', value: 'test' },
];

const LABEL_SELECT_OPTIONS = ENVIRONMENT_ORDER.map((name) => ({ label: name, value: name }));

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
  selectedEquipmentId?: string | null;
  selectedEquipmentIds?: string[];
  selectedProjectEnvironment?: string | null;
  categoryNodes?: GraphNode[];
  onRefresh: () => void;
  onLocateEquipment?: (equipmentId: string) => void;
}

export default function ProjectPanel({ allNodes, selectedEquipmentId, selectedEquipmentIds = [], selectedProjectEnvironment, onRefresh, onLocateEquipment }: ProjectPanelProps) {
  const [nodes, setNodes] = useState<GraphNode[]>([]);
  const [loading, setLoading] = useState(false);
  const [modalVisible, setModalVisible] = useState(false);
  const [editing, setEditing] = useState<GraphNode | null>(null);
  const [searchText, setSearchText] = useState('');
  const [typeLevelFilter, setTypeLevelFilter] = useState<LevelTypeLabel | undefined>();
  const [form] = Form.useForm();
  const projectType = Form.useWatch('type', form);
  const [relationModal, setRelationModal] = useState<{ open: boolean; node: GraphNode }>({ open: false, node: {} as GraphNode });
  const [equipmentNodes, setEquipmentNodes] = useState<GraphNode[]>([]);
  const [environmentNodes, setEnvironmentNodes] = useState<GraphNode[]>([]);
  const { selectedRowKeys, rowSelection, clearSelection } = useBatchRowSelection<GraphNode>();

  const nodeType: NodeType = 'TestProject';

  const defaultProjectLabel = useMemo(() => {
    if (!selectedProjectEnvironment) return undefined;
    const env = environmentNodes.find((n) => n.id === selectedProjectEnvironment);
    const envName = String(env?.name || '').trim();
    return ENVIRONMENT_ORDER.find((name) => envName.includes(name) || name.includes(envName) || envName.includes(name.replace('环境', '')));
  }, [selectedProjectEnvironment, environmentNodes]);

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
    void loadRelationOptions();
  }, []);

  const loadRelationOptions = async () => {
    try {
      const [eqs, envs] = await Promise.all([fetchNodes('Equipment'), fetchNodes('Environment')]);
      setEquipmentNodes(Array.isArray(eqs) ? eqs : []);
      setEnvironmentNodes(Array.isArray(envs) ? envs : []);
    } catch {
      /* 关系选项加载失败不阻断列表 */
    }
  };

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
        equipment_id: selectedEquipmentId || values.equipment_id || '',
        environment_id: selectedProjectEnvironment || values.environment_id || '',
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

  const handleBatchDelete = async () => {
    const ids = selectedRowKeys.map(String).filter(Boolean);
    if (!ids.length) return;
    setLoading(true);
    try {
      const { ok, failed } = await batchDeleteNodes(nodeType, ids, deleteNode);
      if (ok) message.success(`已删除 ${ok} 项`);
      if (failed.length) message.warning(`${failed.length} 项删除失败`);
      clearSelection();
      loadData();
      onRefresh();
    } finally {
      setLoading(false);
    }
  };

  const filtered = useMemo(() => {
    let result = nodes;

    const equipmentScope = selectedEquipmentIds.length > 0 ? selectedEquipmentIds : selectedEquipmentId ? [selectedEquipmentId] : [];
    if (equipmentScope.length > 0) {
      result = result.filter((n) => Array.isArray(n.equipment_ids) && equipmentScope.some((id) => n.equipment_ids.includes(id)));
    }
    if (selectedProjectEnvironment) {
      result = result.filter((n) => Array.isArray(n.environment_ids) && n.environment_ids.includes(selectedProjectEnvironment));
    }

    if (searchText) {
      result = result.filter((n) => (n.name || '').toLowerCase().includes(searchText.toLowerCase()));
    }

    if (typeLevelFilter) {
      result = result.filter((n) => matchesLevelTypeFilter(n.type, typeLevelFilter));
    }

    return result;
  }, [nodes, selectedEquipmentId, selectedEquipmentIds, selectedProjectEnvironment, searchText, typeLevelFilter]);

  const tablePagination = useTablePagination(filtered.length, {
    resetDeps: [searchText, typeLevelFilter, selectedEquipmentId, selectedEquipmentIds, selectedProjectEnvironment],
  });

  /** 当前表格筛选条件下，父级下拉仅显示当前设备+环境中的父级项目。 */
  const currentTableParentScope = useMemo(() => ({
    equipmentIds: selectedEquipmentIds.length > 0 ? selectedEquipmentIds : selectedEquipmentId ? [selectedEquipmentId] : [],
    environmentId: selectedProjectEnvironment || '',
  }), [selectedEquipmentId, selectedEquipmentIds, selectedProjectEnvironment]);

  const parentOptions = useMemo(() => {
    const selfId = editing?.id;
    return listForParents
      .filter((n) => {
        if (n.id === selfId) return false;
        if (isExistingChildRow(n)) return false;
        const t = String(n.type || '').trim();
        if (t === 'test' || t === '子级') return false;
        if (currentTableParentScope.equipmentIds.length > 0) {
          if (!Array.isArray(n.equipment_ids) || !currentTableParentScope.equipmentIds.some((id) => n.equipment_ids.includes(id))) return false;
        }
        if (currentTableParentScope.environmentId) {
          if (!Array.isArray(n.environment_ids) || !n.environment_ids.includes(currentTableParentScope.environmentId)) return false;
        }
        return true;
      })
      .map((n) => ({ label: n.name || n.id, value: n.id }));
  }, [listForParents, editing?.id, currentTableParentScope]);

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
      filterDropdown: (
        <div style={{ padding: 8 }}>
          <Select
            style={{ width: 120 }}
            placeholder="全部"
            allowClear
            value={typeLevelFilter}
            onChange={(v) => setTypeLevelFilter(v)}
            options={LEVEL_TYPE_FILTER_OPTIONS}
          />
        </div>
      ),
      filterIcon: (
        <FilterOutlined style={{ color: typeLevelFilter ? '#1677ff' : undefined }} />
      ),
      render: (val: string) => resolveLevelTypeLabel(val) || '—',
    },
    {
      title: LABEL_MAP.equipment_id,
      dataIndex: 'equipment_name',
      key: 'equipment_name',
      width: 120,
      ellipsis: true,
      render: (_: unknown, record: GraphNode) => (
        <EquipmentNameLinks
          equipmentIds={record.equipment_ids as string[] | undefined}
          equipmentNames={record.equipment_names as string[] | undefined}
          onLocate={onLocateEquipment}
        />
      ),
    },
    {
      title: LABEL_MAP.environment_id,
      dataIndex: 'environment_names',
      key: 'environment_names',
      width: 120,
      ellipsis: true,
      render: (_: unknown, record: GraphNode) => {
        const names = Array.isArray(record.environment_names) ? record.environment_names : [];
        return names.length ? names.join('、') : '—';
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
      render: (val: string) => <StatusTag value={val} />,
    },
    { title: '编辑用户', dataIndex: 'user_name', key: 'user_name', width: 100 },
    {
      title: '编辑时间',
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
                equipment_id: record.equipment_id || selectedEquipmentId || undefined,
                environment_id: record.environment_id || selectedProjectEnvironment || undefined,
                description: record.description,
                status: normalizeStatusValue(record.status),
                user_name: record.user_name,
                create_time: record.create_time,
              });
              setModalVisible(true);
            }}
          >
            编辑
          </Button>
          {/* 关联操作暂时下线，后续仍会启用
          <Button type="link" size="small" icon={<LinkOutlined />} onClick={() => setRelationModal({ open: true, node: record })}>
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

  const handleAdd = () => {
    if (!selectedEquipmentId) {
      message.warning('请先在左侧选择三级设备');
      return;
    }
    if (!selectedProjectEnvironment) {
      message.warning('请先在左侧选择关联环境分类');
      return;
    }
    setEditing(null);
    const currentUser = getCurrentUser();
    const now = getCurrentTime();
    form.setFieldsValue({
      name: '',
      label: defaultProjectLabel,
      type: 'category',
      parent_id: undefined,
      equipment_id: selectedEquipmentId || undefined,
      environment_id: selectedProjectEnvironment || undefined,
      description: '',
      status: DEFAULT_FLGL_STATUS,
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
          {selectedRowKeys.length > 0 && (
            <Popconfirm title={`确定删除选中的 ${selectedRowKeys.length} 项？`} onConfirm={() => void handleBatchDelete()}>
              <Button danger icon={<DeleteOutlined />}>批量删除</Button>
            </Popconfirm>
          )}
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
        rowSelection={rowSelection}
        loading={loading}
        size="small"
        pagination={tablePagination}
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

          <Form.Item label={LABEL_MAP.equipment_id} name="equipment_id" rules={[{ required: true, message: '请先在左侧选择三级设备' }]}> 
            <Select
              disabled={!!selectedEquipmentId}
              showSearch
              optionFilterProp="label"
              placeholder="请在左侧选择三级设备"
              options={equipmentNodes.map((n) => ({ label: n.name || n.id, value: n.id }))}
            />
          </Form.Item>

          <Form.Item label={LABEL_MAP.environment_id} name="environment_id" rules={[{ required: true, message: '请在左侧选择关联环境分类' }]}> 
            <Select
              disabled={!!selectedProjectEnvironment}
              showSearch
              optionFilterProp="label"
              placeholder="请在左侧选择环境"
              options={environmentNodes.map((n) => ({ label: n.name || n.id, value: n.id }))}
            />
          </Form.Item>

          <Form.Item
            label={LABEL_MAP.label}
            name="label"
            tooltip="分类用环境类别名；图谱关联环境由上方关联环境维护"
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
                key={`parent-${currentTableParentScope.equipmentIds.join('_') || 'all'}-${currentTableParentScope.environmentId || 'all'}-${parentOptions.length}`}
              />
            </Form.Item>
          ) : null}

          <Form.Item label={LABEL_MAP.description} name="description">
            <MathAwareTextArea rows={3} placeholder="请输入项目描述" />
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
