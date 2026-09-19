'use client';

import React, { useState, useEffect, useMemo } from 'react';
import { Modal, Table, Button, Space, Select, Tag, message, Typography } from 'antd';
import { PlusOutlined, DeleteOutlined } from '@ant-design/icons';
import { fetchAvailableNodes, createRelations, deleteRelation, type NodeType, type NodeRelation, type RelationInput } from '../api';
import { formatTableDateDisplay } from '../utils/tableDateDisplay';

const { Text } = Typography;

const NODE_TYPE_LABELS: Record<NodeType, string> = {
  EquipmentType: '设备类型',
  Equipment: '设备',
  Environment: '环境',
  TestProject: '项目',
  Indicator: '指标',
  TestMethod: '方法',
  File: '文件',
};

const REL_TYPE_LABELS: Record<string, string> = {
  HAS: '包含',
  CON: '关联',
  BELONGS_TO: '属于',
};

const REL_TYPE_OPTIONS = [
  { label: '包含（HAS）', value: 'HAS' },
  { label: '关联（CON）', value: 'CON' },
  { label: '属于（BELONGS_TO）', value: 'BELONGS_TO' },
];

const RELATION_RULES: Record<NodeType, Array<{ targetType: NodeType; relType: string; direction: 'outgoing' | 'incoming' }>> = {
  EquipmentType: [{ targetType: 'Equipment', relType: 'HAS', direction: 'outgoing' }],
  Equipment: [
    { targetType: 'TestProject', relType: 'CON', direction: 'outgoing' },
    { targetType: 'Indicator', relType: 'CON', direction: 'outgoing' },
  ],
  Environment: [{ targetType: 'TestProject', relType: 'CON', direction: 'outgoing' }],
  TestProject: [
    { targetType: 'Environment', relType: 'CON', direction: 'incoming' },
    { targetType: 'TestMethod', relType: 'CON', direction: 'outgoing' },
    { targetType: 'TestProject', relType: 'HAS', direction: 'outgoing' },
  ],
  Indicator: [{ targetType: 'TestMethod', relType: 'CON', direction: 'outgoing' }],
  TestMethod: [
    { targetType: 'TestProject', relType: 'CON', direction: 'incoming' },
    { targetType: 'Indicator', relType: 'CON', direction: 'incoming' },
    { targetType: 'TestMethod', relType: 'BELONGS_TO', direction: 'outgoing' },
    { targetType: 'File', relType: 'BELONGS_TO', direction: 'incoming' },
  ],
  File: [{ targetType: 'TestMethod', relType: 'BELONGS_TO', direction: 'outgoing' }],
};

function relationOptionsFor(nodeType: NodeType) {
  const rules = RELATION_RULES[nodeType] || [];
  const seen = new Set<NodeType>();
  return rules.filter((r) => {
    if (seen.has(r.targetType)) return false;
    seen.add(r.targetType);
    return true;
  }).map((r) => ({ label: NODE_TYPE_LABELS[r.targetType], value: r.targetType }));
}

function defaultNewRel(nodeType: NodeType) {
  const rule = (RELATION_RULES[nodeType] || [])[0] || { targetType: 'Equipment' as NodeType, relType: 'CON', direction: 'outgoing' as const };
  return { targetType: rule.targetType, targetId: '', relType: rule.relType, direction: rule.direction };
}

function defaultRuleFor(nodeType: NodeType, targetType: NodeType) {
  return (RELATION_RULES[nodeType] || []).find((r) => r.targetType === targetType) || defaultNewRel(nodeType);
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
  const [newRel, setNewRel] = useState<{ targetType: NodeType; targetId: string; relType: string; direction: 'outgoing' | 'incoming' }>(() =>
    defaultNewRel('Equipment')
  );

  useEffect(() => {
    if (open && nodeId) {
      const next = defaultNewRel(nodeType);
      setNewRel(next);
      loadAvailable(next.targetType);
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
      const nodes = await fetchAvailableNodes(targetType, { sourceNodeId: nodeId, sourceNodeType: nodeType });
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
      const next = defaultNewRel(nodeType);
      setNewRel(next);
      loadAvailable(next.targetType);
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
        render: (t: string) => <Tag>{REL_TYPE_LABELS[t] || t}（{t}）</Tag>,
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
        {relationOptionsFor(nodeType).length === 0 ? (
          <div style={{ marginTop: 8 }}><Text type="secondary">当前节点类型暂无可配置关联。</Text></div>
        ) : null}
        <div style={{ display: relationOptionsFor(nodeType).length ? 'flex' : 'none', gap: 8, marginTop: 8, flexWrap: 'wrap' }}>
          <Select
            value={newRel.targetType}
            onChange={(v: NodeType) => {
              const rule = defaultRuleFor(nodeType, v);
              setNewRel({ targetType: v, targetId: '', relType: rule.relType, direction: rule.direction });
              loadAvailable(v);
            }}
            style={{ width: 140 }}
            size="small"
            options={relationOptionsFor(nodeType)}
          />
          <Select
            value={newRel.targetId}
            onChange={(v) => setNewRel(prev => ({ ...prev, targetId: v }))}
            placeholder="选择节点"
            style={{ width: 200 }}
            size="small"
            showSearch
            virtual={false}
            options={availableNodes.map(n => ({ label: n.name, value: n.id }))}
          />
          <Select
            value={newRel.relType}
            onChange={(v) => setNewRel(prev => ({ ...prev, relType: v }))}
            style={{ width: 100 }}
            size="small"
            options={REL_TYPE_OPTIONS}
            disabled
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
            disabled
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
