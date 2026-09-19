'use client';

import React, { useState, useEffect, useMemo } from 'react';
import { Card, Table, Button, Space, Input, Modal, Form, Select, Tag, message, Popconfirm, TreeSelect } from 'antd';
import { PlusOutlined, EditOutlined, DeleteOutlined, SearchOutlined, LinkOutlined, FilterOutlined } from '@ant-design/icons';
import { fetchNodes, createNode, updateNode, deleteNode, type NodeType, type GraphNode } from '../api';
import { batchDeleteNodes, useBatchRowSelection } from '../hooks/useBatchRowSelection';
import RelationModal from './RelationModal';
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
  type: '类别',
  parent_id: '父指标',
  equipment_id: '关联设备',
  description: '描述',
  status: '状态',
  user_name: '编辑用户',
  create_time: '编辑时间',
};

const STATUS_OPTIONS = FLGL_STATUS_OPTIONS;

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

interface IndicatorPanelProps {
  allNodes: GraphNode[];
  selectedCategory: string | null;
  selectedEquipmentId?: string | null;
  selectedEquipmentIds?: string[];
  categoryNodes?: GraphNode[];
  onRefresh: () => void;
  onLocateEquipment?: (equipmentId: string) => void;
}

export default function IndicatorPanel({ selectedEquipmentId, selectedEquipmentIds = [], onRefresh, onLocateEquipment }: IndicatorPanelProps) {
  const [nodes, setNodes] = useState<GraphNode[]>([]);
  const [loading, setLoading] = useState(false);
  const [modalVisible, setModalVisible] = useState(false);
  const [editing, setEditing] = useState<GraphNode | null>(null);
  const [searchText, setSearchText] = useState('');
  const [typeLevelFilter, setTypeLevelFilter] = useState<LevelTypeLabel | undefined>();
  const [form] = Form.useForm();
  const [relationModal, setRelationModal] = useState<{ open: boolean; node: GraphNode }>({ open: false, node: {} as GraphNode });
  const [equipmentNodes, setEquipmentNodes] = useState<GraphNode[]>([]);
  const { selectedRowKeys, rowSelection, clearSelection } = useBatchRowSelection<GraphNode>();

  const nodeType: NodeType = 'Indicator';

  const defaultType = 'test';

  useEffect(() => { loadData(); void loadEquipmentOptions(); }, []);

  const loadEquipmentOptions = async () => {
    try {
      const eqs = await fetchNodes('Equipment');
      setEquipmentNodes(Array.isArray(eqs) ? eqs : []);
    } catch {
      /* 忽略 */
    }
  };

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
        equipment_id: selectedEquipmentId || values.equipment_id || '',
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
      result = result.filter(n => Array.isArray(n.equipment_ids) && equipmentScope.some((id) => n.equipment_ids.includes(id)));
    }
    
    if (searchText) {
      result = result.filter(n =>
        `${n.name || ''} ${n.parent_name || ''}`.toLowerCase().includes(searchText.toLowerCase())
      );
    }

    if (typeLevelFilter) {
      result = result.filter((n) => matchesLevelTypeFilter(n.type, typeLevelFilter));
    }
    
    return result;
  }, [nodes, selectedEquipmentId, selectedEquipmentIds, searchText, typeLevelFilter]);

  const tablePagination = useTablePagination(filtered.length, {
    resetDeps: [searchText, typeLevelFilter, selectedEquipmentId, selectedEquipmentIds],
  });

  const indicatorTreeOptions = useMemo(() => {
    const byId = new Map<string, GraphNode & { children?: any[] }>();
    filtered.forEach((n) => {
      if (!n.id) return;
      byId.set(n.id, { ...n, children: [] });
    });
    const roots: Array<GraphNode & { children?: any[] }> = [];
    byId.forEach((n) => {
      const pid = String(n.parent_id || '').trim();
      if (pid && byId.has(pid)) {
        byId.get(pid)?.children?.push(n);
      } else {
        roots.push(n);
      }
    });
    const toTree = (arr: Array<GraphNode & { children?: any[] }>): any[] =>
      arr.map((n) => ({
        title: `${n.name || n.id}${n.type === 'category' ? '（父）' : ''}`,
        value: n.id,
        key: n.id,
        children: toTree((n.children || []) as any),
      }));
    return toTree(roots);
  }, [filtered]);

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
    { title: '名称', dataIndex: 'name', key: 'name', width: 120 },
    {
      title: '类别',
      dataIndex: 'type',
      key: 'type',
      width: 90,
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
      render: (val: string) => (
        resolveLevelTypeLabel(val) === '父级'
          ? <Tag color="blue">父级</Tag>
          : <Tag color="purple">子级</Tag>
      ),
    },
    { title: '父指标', dataIndex: 'parent_name', key: 'parent_name', width: 120, ellipsis: true, render: (v: string) => v || '—' },
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
    { title: '描述', dataIndex: 'description', key: 'description', width: 150,ellipsis: true },
    {
      title: '状态', dataIndex: 'status', key: 'status', width: 80,
      render: (val: string) => <StatusTag value={val} />,
    },
    { title: '编辑用户', dataIndex: 'user_name', key: 'user_name', width: 100 },
    { title: '编辑时间', dataIndex: 'create_time', key: 'create_time', width: 100,render: (val: string) => formatTableDateDisplay(val), },
    {
      title: '操作', key: 'action', width: 200, fixed: 'right' as const,
      render: (_: any, record: GraphNode) => (
        <Space>
          <Button type="link" size="small" icon={<EditOutlined />} onClick={() => {
            setEditing(record);
            form.setFieldsValue({
              ...record,
              status: normalizeStatusValue(record.status),
              equipment_id: record.equipment_id || selectedEquipmentId || undefined,
              parent_id: record.parent_id || undefined,
              type: record.type || 'test',
            });
            setModalVisible(true);
          }}>编辑</Button>
          {/* 关联操作暂时下线，后续仍会启用
          <Button type="link" size="small" icon={<LinkOutlined />} onClick={() => setRelationModal({ open: true, node: record })}>关联</Button>
          */}
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
      type: defaultType,
      equipment_id: selectedEquipmentId || undefined,
      parent_id: undefined,
      description: '',
      status: DEFAULT_FLGL_STATUS,
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
          {selectedRowKeys.length > 0 && (
            <Popconfirm title={`确定删除选中的 ${selectedRowKeys.length} 项？`} onConfirm={() => void handleBatchDelete()}>
              <Button danger icon={<DeleteOutlined />}>批量删除</Button>
            </Popconfirm>
          )}
          <Button type="primary" icon={<PlusOutlined />} onClick={handleAdd}>新增</Button>
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

          <Form.Item label={LABEL_MAP.equipment_id} name="equipment_id" rules={[{ required: true, message: '请先在左侧选择三级设备' }]}> 
            <Select
              disabled={!!selectedEquipmentId}
              showSearch
              optionFilterProp="label"
              placeholder="请在左侧选择三级设备"
              options={equipmentNodes.map((n) => ({ label: n.name || n.id, value: n.id }))}
            />
          </Form.Item>

          <Form.Item label={LABEL_MAP.type} name="type" rules={[{ required: true, message: '请选择类别' }]}>
            <Select
              options={[
                { label: '父级(category)', value: 'category' },
                { label: '子级(test)', value: 'test' },
              ]}
            />
          </Form.Item>

          <Form.Item noStyle shouldUpdate={(prev, cur) => prev.type !== cur.type}>
            {({ getFieldValue }) =>
              getFieldValue('type') === 'test' ? (
                <Form.Item label={LABEL_MAP.parent_id} name="parent_id">
                  <TreeSelect
                    allowClear
                    treeDefaultExpandAll
                    placeholder="选择父指标（仅子级需要）"
                    treeData={indicatorTreeOptions}
                  />
                </Form.Item>
              ) : null
            }
          </Form.Item>
          
          <Form.Item label={LABEL_MAP.description} name="description">
            <MathAwareTextArea rows={3} placeholder="请输入指标描述" />
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