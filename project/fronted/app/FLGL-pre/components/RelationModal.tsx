'use client';

import React, { useState, useEffect, useMemo } from 'react';
import { Modal, Table, Button, Space, Select, Tag, message, Typography } from 'antd';
import { PlusOutlined, DeleteOutlined } from '@ant-design/icons';
import { fetchAvailableNodes, createRelations, deleteRelation, type NodeType, type NodeRelation, type RelationInput } from '../api';
import { formatTableDateDisplay } from '../utils/tableDateDisplay';

const { Text } = Typography;

const NODE_TYPE_OPTIONS: Array<{ label: string; value: NodeType }> = [
  { label: '设备类型', value: 'EquipmentType' },
  { label: '设备', value: 'Equipment' },
  { label: '环境', value: 'Environment' },
  { label: '项目', value: 'TestProject' },
  { label: '指标', value: 'Indicator' },
  { label: '方法', value: 'TestMethod' },
  { label: '文件', value: 'File' },
];

const REL_TYPE_OPTIONS = [
  { label: 'HAS', value: 'HAS' },
  { label: 'CON', value: 'CON' },
  { label: '关联', value: '关联' },
];

/** 图中为 Environment-[:CON]->TestProject；在项目侧建边需 incoming（环境指向项目） */
function defaultNewRel(nodeType: NodeType) {
  if (nodeType === 'TestProject') {
    return { targetType: 'Environment' as NodeType, targetId: '', relType: 'CON', direction: 'incoming' };
  }
  return { targetType: 'Equipment' as NodeType, targetId: '', relType: 'CON', direction: 'outgoing' };
}

interface Props {
  open: boolean;
  nodeId: string;
  nodeType: NodeType;
  nodeName: string;
  onClose: () => void;
  onSaved: () => void;
}

export default function RelationModal({ open, nodeId, nodeType, nodeName, onClose, onSaved }: Props) {
  const [relations, setRelations] = useState<NodeRelation[]>([]);
  const [loading, setLoading] = useState(false);
  const [adding, setAdding] = useState(false);
  const [availableNodes, setAvailableNodes] = useState<Array<{ id: string; name: string }>>([]);
  const [newRel, setNewRel] = useState<{ targetType: NodeType; targetId: string; relType: string; direction: string }>(() =>
    defaultNewRel('Equipment')
  );

  useEffect(() => {
    if (open && nodeId) {
      setNewRel(defaultNewRel(nodeType));
      loadRelations();
    }
  }, [open, nodeId, nodeType]);

  const loadRelations = async () => {
    setLoading(true);
    try {
      const res = await fetch(`/api/FLGL/nodes/${nodeType}/${nodeId}/relations`);
      const json = await res.json();
      if (json.success) setRelations(json.data || []);
    } catch {
      message.error('加载关联失败');
    } finally {
      setLoading(false);
    }
  };

  const loadAvailable = async (targetType: NodeType) => {
    try {
      const nodes = await fetchAvailableNodes(targetType);
      setAvailableNodes(nodes);
    } catch {
      message.error('加载可用节点失败');
    }
  };

  const handleAdd = async () => {
    if (!newRel.targetId) { message.warning('请选择关联节点'); return; }
    setAdding(true);
    try {
      await createRelations(nodeId, [{
        target_id: newRel.targetId,
        rel_type: newRel.relType,
        direction: newRel.direction as 'outgoing' | 'incoming',
      }]);
      message.success('添加关联成功');
      setNewRel(defaultNewRel(nodeType));
      loadRelations();
      onSaved();
    } catch {
      message.error('添加关联失败');
    } finally {
      setAdding(false);
    }
  };

  const handleDelete = async (rel: NodeRelation) => {
    try {
      await deleteRelation(nodeId, rel.target_id, rel.rel_type, rel.direction);
      message.success('删除关联成功');
      loadRelations();
      onSaved();
    } catch {
      message.error('删除关联失败');
    }
  };

  const columns = useMemo(() => {
    const showRelationTime = relations.some((r) => {
      const t = (r as NodeRelation & { create_time?: string }).create_time;
      return t != null && String(t).trim() !== '';
    });
    const timeCol = {
      title: '时间',
      key: 'create_time',
      width: 120,
      render: (_: unknown, record: NodeRelation) =>
        formatTableDateDisplay((record as NodeRelation & { create_time?: string }).create_time),
    };
    const base = [
      {
        title: '关联节点类型',
        dataIndex: 'target_labels',
        key: 'target_labels',
        render: (labels: string[]) => (
          <Space wrap>
            {labels.map((l) => (
              <Tag key={l} color="blue">
                {l}
              </Tag>
            ))}
          </Space>
        ),
      },
      {
        title: '关联节点',
        dataIndex: 'target_name',
        key: 'target_name',
        render: (name: string, record: NodeRelation) => <Text>{name || record.target_id}</Text>,
      },
      {
        title: '方向',
        dataIndex: 'direction',
        key: 'direction',
        render: (d: string) => (
          <Tag color={d === 'outgoing' ? 'green' : 'orange'}>
            {d === 'outgoing' ? '→ 指向该节点' : '← 被该节点指向'}
          </Tag>
        ),
      },
      {
        title: '关系类型',
        dataIndex: 'rel_type',
        key: 'rel_type',
        render: (t: string) => <Tag>{t}</Tag>,
      },
    ];
    const tail = {
      title: '操作',
      key: 'action',
      render: (_: unknown, record: NodeRelation) => (
        <Button type="link" danger size="small" icon={<DeleteOutlined />} onClick={() => handleDelete(record)}>
          删除
        </Button>
      ),
    };
    return showRelationTime ? [...base, timeCol, tail] : [...base, tail];
  }, [relations]);

  return (
    <Modal
      title={`管理关联 — ${nodeName}`}
      open={open}
      onCancel={onClose}
      footer={null}
      width={800}
    >
      <div style={{ marginBottom: 16, padding: 12, background: '#f5f5f5', borderRadius: 8 }}>
        <Text strong>新增关联：</Text>
        <div style={{ display: 'flex', gap: 8, marginTop: 8, flexWrap: 'wrap' }}>
          <Select
            value={newRel.targetType}
            onChange={(v: NodeType) => { setNewRel(prev => ({ ...prev, targetType: v, targetId: '' })); loadAvailable(v); }}
            style={{ width: 140 }}
            size="small"
            options={NODE_TYPE_OPTIONS}
          />
          <Select
            value={newRel.targetId}
            onChange={(v) => setNewRel(prev => ({ ...prev, targetId: v }))}
            placeholder="选择节点"
            style={{ width: 200 }}
            size="small"
            showSearch
            options={availableNodes.map(n => ({ label: n.name, value: n.id }))}
          />
          <Select
            value={newRel.relType}
            onChange={(v) => setNewRel(prev => ({ ...prev, relType: v }))}
            style={{ width: 100 }}
            size="small"
            options={REL_TYPE_OPTIONS}
          />
          <Select
            value={newRel.direction}
            onChange={(v) => setNewRel(prev => ({ ...prev, direction: v }))}
            style={{ width: 130 }}
            size="small"
            options={[
              { label: '→ 指向该节点', value: 'outgoing' },
              { label: '← 被该节点指向', value: 'incoming' },
            ]}
          />
          <Button type="primary" size="small" icon={<PlusOutlined />} onClick={handleAdd} loading={adding}>
            添加
          </Button>
        </div>
      </div>

      <Table
        columns={columns}
        dataSource={relations}
        rowKey={(r) => `${r.target_id}_${r.rel_type}_${r.direction}`}
        size="small"
        loading={loading}
        pagination={false}
        scroll={{ y: 300 }}
      />
    </Modal>
  );
}
