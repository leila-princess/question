'use client';



import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import {

  Tabs, Button, Table, Select, Modal, message, Tag, Space, Alert, InputNumber, Input,

} from 'antd';

import { PlusOutlined } from '@ant-design/icons';

import type { ColumnsType } from 'antd/es/table';

import type { DataNode } from 'antd/es/tree';

import EquipmentSearchTree from '@/app/FLGL/components/EquipmentSearchTree';

import { buildGraphPickerTreeData } from '@/app/FLGL/utils/equipmentTreeUtils';

import type {

  EquipmentTab,

  EquipmentTabLinkRow,

  GraphRecommendation,

} from './semanticTypes';

import {

  applyRecommendationsToTabs,

  tabToConfirmLinks,

  EQUIPMENT_LINK_NODE_TYPES,

  pickRecommendedEquipmentType,

} from './semanticTypes';

import EquipmentGraphPreview from './EquipmentGraphPreview';



const { Option } = Select;

const { Search } = Input;

/** 内部标记「用户新输入、尚未入库的节点名」；界面展示时去掉此前缀 */
const NEW_NODE_PREFIX = '__new__';

function stripNewNodePrefix(value: string): string {
  const v = String(value || '');
  return v.startsWith(NEW_NODE_PREFIX) ? v.slice(NEW_NODE_PREFIX.length) : v;
}

function toNewNodeId(name: string): string {
  const n = String(name || '').trim().replace(new RegExp(`^${NEW_NODE_PREFIX}`), '');
  return n ? `${NEW_NODE_PREFIX}${n}` : '';
}

function displayNodeTagLabel(value: string, nodes: { id: string; name: string }[]): string {
  const v = String(value || '');
  if (v.startsWith(NEW_NODE_PREFIX)) return stripNewNodePrefix(v);
  const hit = nodes.find((n) => n.id === v);
  return hit?.name || v;
}



function filterEquipmentListByKeyword(

  list: { id: string; name: string }[],

  keyword: string,

): { id: string; name: string }[] {

  const kw = keyword.trim().toLowerCase();

  if (!kw) return list;

  return list.filter(

    (e) =>

      (e.name || '').toLowerCase().includes(kw) ||

      (e.id || '').toLowerCase().includes(kw),

  );

}



interface DeviceLinkTabsProps {

  equipmentTabs: EquipmentTab[];

  recommendations?: GraphRecommendation[];

  onChange: (tabs: EquipmentTab[]) => void;

}



function newRowKey() {

  return `row-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`;

}



async function fetchNodesForType(

  nodeType: string,

  equipmentId: string,

): Promise<{ id: string; name: string }[]> {

  if (!nodeType || !equipmentId) return [];

  const qs = new URLSearchParams({ node_type: nodeType });

  if (nodeType !== 'Environment') {

    qs.set('equipment_id', equipmentId);

  }

  const res = await fetch(`/api/wjgl1/graph-picker/nodes?${qs.toString()}`);

  const data = await res.json();

  if (data.warning) message.warning(data.warning);

  if (!data.success) return [];

  return (data.data || []).map((n: { id: string; name: string }) => ({

    id: n.id,

    name: n.name || n.id,

  }));

}



async function fetchEquipmentsUnderType(typeId: string): Promise<{ id: string; name: string }[]> {

  const res = await fetch(

    `/api/wjgl1/graph-picker/equipment-type/${encodeURIComponent(typeId)}/equipments`

  );

  const data = await res.json();

  if (!data.success) return [];

  return (data.data || []).map((n: { id: string; name: string }) => ({

    id: n.id,

    name: n.name || n.id,

  }));

}



export default function DeviceLinkTabs({

  equipmentTabs,

  recommendations = [],

  onChange,

}: DeviceLinkTabsProps) {

  const [activeKey, setActiveKey] = useState('');

  const [addOpen, setAddOpen] = useState(false);

  const [equipTreeRaw, setEquipTreeRaw] = useState<DataNode[]>([]);

  const [typeEquipSearch, setTypeEquipSearch] = useState('');

  const [typeTreeVisible, setTypeTreeVisible] = useState(false);

  const [treeLoading, setTreeLoading] = useState(false);

  const [nodesLoading, setNodesLoading] = useState<Record<string, boolean>>({});

  const [typeEquipments, setTypeEquipments] = useState<{ id: string; name: string }[]>([]);

  const [typeEquipLoading, setTypeEquipLoading] = useState(false);

  const [pendingEquipId, setPendingEquipId] = useState('');

  const initRef = useRef(false);



  const recommendedType = useMemo(

    () => pickRecommendedEquipmentType(recommendations),

    [recommendations],

  );



  const filteredTypeEquipments = useMemo(

    () => filterEquipmentListByKeyword(typeEquipments, typeEquipSearch),

    [typeEquipments, typeEquipSearch],

  );



  useEffect(() => {

    if (initRef.current || equipmentTabs.length > 0) return;

    const equipOnly = applyRecommendationsToTabs([], recommendations);

    if (equipOnly.length) {

      initRef.current = true;

      onChange(equipOnly);

      setActiveKey(equipOnly[0].key);

    }

  }, [recommendations, equipmentTabs.length, onChange]);



  useEffect(() => {

    if (!recommendedType?.node_id || equipmentTabs.length) return;

    setTypeEquipLoading(true);

    void fetchEquipmentsUnderType(recommendedType.node_id)

      .then(list => setTypeEquipments(list))

      .finally(() => setTypeEquipLoading(false));

  }, [recommendedType?.node_id, equipmentTabs.length]);



  useEffect(() => {

    if (equipmentTabs.length && !activeKey) setActiveKey(equipmentTabs[0].key);

  }, [equipmentTabs, activeKey]);



  const loadTree = useCallback(async () => {

    setTreeLoading(true);

    try {

      const res = await fetch('/api/wjgl1/graph-picker/equipment-tree');

      const data = await res.json();

      if (data.success) {

        const existing = new Set(equipmentTabs.map(t => t.equipment_id));

        setEquipTreeRaw(buildGraphPickerTreeData(data.data || [], existing));

      }

    } finally {

      setTreeLoading(false);

    }

  }, [equipmentTabs]);



  const updateTab = (tabKey: string, patch: Partial<EquipmentTab>) => {

    onChange(equipmentTabs.map(t => (t.key === tabKey ? { ...t, ...patch } : t)));

  };



  const addEquipment = (eqId: string, eqName: string) => {

    if (!eqId) return;

    if (equipmentTabs.some(t => t.equipment_id === eqId)) {

      message.info('该设备 Tab 已存在');

      setActiveKey(eqId);

      setAddOpen(false);

      setTypeTreeVisible(false);

      return;

    }

    initRef.current = true;

    onChange([...equipmentTabs, { key: eqId, equipment_id: eqId, equipment_name: eqName, rows: [] }]);

    setActiveKey(eqId);

    setAddOpen(false);

    setTypeTreeVisible(false);

    setPendingEquipId('');

  };



  const confirmRecommendedEquipment = () => {

    const hit = typeEquipments.find(e => e.id === pendingEquipId);

    if (!hit) {

      message.warning('请先选择具体设备');

      return;

    }

    addEquipment(hit.id, hit.name);

  };



  const loadNodesForRow = async (tab: EquipmentTab, rowKey: string, nodeType: string) => {

    if (!tab.equipment_id) {

      message.warning('请先确认具体设备');

      return;

    }

    const cacheKey = `${tab.equipment_id}:${nodeType}`;

    setNodesLoading(prev => ({ ...prev, [cacheKey]: true }));

    try {

      const nodes = await fetchNodesForType(nodeType, tab.equipment_id);

      updateTab(tab.key, {

        rows: tab.rows.map(r =>

          r.key === rowKey ? { ...r, node_type: nodeType, node_ids: [], nodes } : r

        ),

      });

      if (!nodes.length && nodeType && nodeType !== 'Environment') {

        message.info('该设备下暂无关联的项目/指标/方法');

      }

    } finally {

      setNodesLoading(prev => ({ ...prev, [cacheKey]: false }));

    }

  };



  const renderAssocTable = (tab: EquipmentTab) => {

    const columns: ColumnsType<EquipmentTabLinkRow> = [

      {

        title: '节点类型',

        dataIndex: 'node_type',

        width: 140,

        render: (val: string, record) => (

          <Select

            value={val || undefined}

            placeholder="选择类型"

            style={{ width: '100%' }}

            onChange={v => void loadNodesForRow(tab, record.key, v)}

          >

            {EQUIPMENT_LINK_NODE_TYPES.map(o => (

              <Option key={o.value} value={o.value}>{o.label}</Option>

            ))}

          </Select>

        ),

      },

      {

        title: '关联节点',

        dataIndex: 'node_ids',

        width: 360,

        render: (_: string[], record) => {

          const cacheKey = `${tab.equipment_id}:${record.node_type}`;

          const options = (record.nodes || []).map((n) => ({
            value: n.id,
            label: `${n.name || n.id} (${n.id})`,
          }));

          return (

            <Select

              mode="tags"

              showSearch

              optionFilterProp="label"

              tokenSeparators={[',', '，', ';']}

              value={record.node_ids || []}

              placeholder={record.node_type ? '搜索或输入新节点名称' : '先选类型'}

              style={{ width: '100%' }}

              disabled={!record.node_type}

              loading={nodesLoading[cacheKey]}

              options={options}

              tagRender={(props) => {
                const display = displayNodeTagLabel(String(props.value), record.nodes || []);
                return (
                  <Tag
                    closable={props.closable}
                    onClose={props.onClose}
                    style={{ marginInlineEnd: 4 }}
                  >
                    {display}
                  </Tag>
                );
              }}

              onOpenChange={open => {

                if (open && record.node_type && !(record.nodes?.length)) {

                  void loadNodesForRow(tab, record.key, record.node_type);

                }

              }}

              onChange={vals => {

                const normalized = (vals as string[]).map((v) => {

                  const raw = stripNewNodePrefix(String(v || '').trim());

                  if (!raw) return '';

                  const hit = (record.nodes || []).find((n) => n.id === raw || n.name === raw);

                  if (hit) return hit.id;

                  return toNewNodeId(raw);

                }).filter(Boolean);

                updateTab(tab.key, {

                  rows: tab.rows.map(r =>

                    r.key === record.key ? { ...r, node_ids: normalized } : r

                  ),

                });

              }}

            />

          );

        },

      },

      {

        title: '关联度',

        dataIndex: 'rate',

        width: 140,

        render: (val: number | null | undefined, record) => (

          <InputNumber

            style={{ width: '100%' }}

            min={0}

            max={1}

            step={0.01}

            precision={2}

            placeholder="0.00-1.00"

            value={val ?? null}

            onChange={(v) => {

              const next = typeof v === 'number' && !Number.isNaN(v) ? Number(v.toFixed(2)) : null;

              updateTab(tab.key, {

                rows: tab.rows.map((r) => (r.key === record.key ? { ...r, rate: next } : r)),

              });

            }}

          />

        ),

      },

      {

        title: '',

        width: 64,

        render: (_: unknown, record) => (

          <Button type="link" danger size="small" onClick={() => {

            updateTab(tab.key, { rows: tab.rows.filter(r => r.key !== record.key) });

          }}>

            删除

          </Button>

        ),

      },

    ];



    const usedTypes = new Set(tab.rows.filter(r => r.node_type).map(r => r.node_type));



    return (

      <div>

        <Table

          columns={columns}

          dataSource={tab.rows}

          rowKey="key"

          pagination={false}

          size="small"

          locale={{ emptyText: '暂无关联，点击下方新增关联行' }}

        />

        <Button

          type="dashed"

          block

          style={{ marginTop: 8 }}

          icon={<PlusOutlined />}

          onClick={() => {

            updateTab(tab.key, {

              rows: [...tab.rows, { key: newRowKey(), node_type: '', node_ids: [], nodes: [], rate: null }],

            });

          }}

        >

          新增关联行

        </Button>

        {usedTypes.size > 0 && (

          <Space wrap style={{ marginTop: 12 }}>

            {EQUIPMENT_LINK_NODE_TYPES.filter(nt => usedTypes.has(nt.value)).map(nt => (

              <Tag key={nt.value} color={nt.color}>{nt.label}</Tag>

            ))}

          </Space>

        )}

      </div>

    );

  };



  const showTypePicker = recommendedType && !equipmentTabs.length;

  const activeTab = equipmentTabs.find(t => t.key === (activeKey || equipmentTabs[0]?.key));



  return (

    <div>

      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8 }}>

        <span style={{ fontWeight: 600 }}>关联节点</span>

        <Button size="small" icon={<PlusOutlined />} onClick={() => {

          setAddOpen(true);

          void loadTree();

        }}>

          添加其他设备

        </Button>

      </div>



      {showTypePicker && (

        <Alert

          type="info"

          showIcon

          style={{ marginBottom: 12 }}

          message={`推荐设备类型：${recommendedType.node_name}`}

          description={

            <div style={{ marginTop: 8 }}>

              <div style={{ marginBottom: 8, fontSize: 13, color: '#666' }}>

                请先选择该类型下的具体设备，再关联项目 / 环境 / 指标 / 方法。

              </div>

              <Space direction="vertical" style={{ width: '100%' }} size={8}>

                <Search

                  allowClear

                  placeholder="输入设备名称关键词搜索"

                  value={typeEquipSearch}

                  onChange={(e) => setTypeEquipSearch(e.target.value)}

                />

                <Space wrap>

                <Select

                  showSearch

                  optionFilterProp="label"

                  placeholder="选择具体设备"

                  style={{ minWidth: 260 }}

                  loading={typeEquipLoading}

                  value={pendingEquipId || undefined}

                  onChange={setPendingEquipId}

                  options={filteredTypeEquipments.map(e => ({ value: e.id, label: e.name }))}

                  notFoundContent={typeEquipLoading ? '加载中…' : '无匹配设备'}

                />

                <Button type="primary" disabled={!pendingEquipId} onClick={confirmRecommendedEquipment}>

                  确认设备

                </Button>

                </Space>

                <Button

                  type="link"

                  size="small"

                  style={{ padding: 0 }}

                  onClick={() => {

                    const next = !typeTreeVisible;

                    setTypeTreeVisible(next);

                    if (next) void loadTree();

                  }}

                >

                  {typeTreeVisible ? '收起三级树' : '从三级树选择'}

                </Button>

                {typeTreeVisible && (

                  <EquipmentSearchTree

                    treeData={equipTreeRaw}

                    loading={treeLoading}

                    onSelectEquipment={addEquipment}

                    height={280}

                  />

                )}

              </Space>

            </div>

          }

        />

      )}



      {!equipmentTabs.length && !showTypePicker && recommendations.length > 0 && (

        <Alert

          type="warning"

          showIcon

          style={{ marginBottom: 12 }}

          message="未匹配到可自动创建的设备，以下为规则推荐（请补充标准名称/关键词后重试，或手动添加设备）"

          description={

            <ul style={{ margin: '8px 0 0', paddingLeft: 18 }}>

              {recommendations.slice(0, 8).map((r, i) => (

                <li key={`${r.node_id || r.node_name}-${i}`}>

                  {r.node_name || r.node_id}

                  {r.node_type ? ` (${r.node_type})` : ''}

                  {typeof r.score === 'number' ? ` · 置信 ${(r.score * 100).toFixed(0)}%` : ''}

                  {r.reason ? ` · ${r.reason}` : ''}

                </li>

              ))}

            </ul>

          }

        />

      )}



      {!equipmentTabs.length && !showTypePicker && !recommendations.length ? (

        <div style={{ color: '#999', fontSize: 13, padding: '12px 0' }}>

          暂无推荐关联。请确认上方已填写标准号/名称/关键词，且 Neo4j 图库已连接。

        </div>

      ) : !equipmentTabs.length && !showTypePicker ? null : equipmentTabs.length > 0 ? (

        <Tabs

          type="editable-card"

          hideAdd

          activeKey={activeKey || equipmentTabs[0]?.key}

          onChange={setActiveKey}

          onEdit={(key, action) => {

            if (action === 'remove' && typeof key === 'string') {

              onChange(equipmentTabs.filter(t => t.key !== key));

              if (activeKey === key) setActiveKey('');

            }

          }}

          items={equipmentTabs.map(t => ({

            key: t.key,

            label: `设备: ${t.equipment_name}`,

            closable: equipmentTabs.length > 1,

            children: (

              <>

                {renderAssocTable(t)}

                {activeTab?.equipment_id === t.equipment_id && (

                  <EquipmentGraphPreview

                    equipmentId={t.equipment_id}

                    equipmentName={t.equipment_name}

                  />

                )}

              </>

            ),

          }))}

        />

      ) : null}



      <Modal

        title="添加其他设备"

        open={addOpen}

        onCancel={() => setAddOpen(false)}

        footer={null}

        width={520}

      >

        <EquipmentSearchTree

          treeData={equipTreeRaw}

          loading={treeLoading}

          onSelectEquipment={addEquipment}

          emptyHint="暂无可添加的设备"

        />

      </Modal>

    </div>

  );

}



export function equipmentTabsHasLinks(tabs: EquipmentTab[]): boolean {

  return tabs.some(t => tabToConfirmLinks(t).length > 0);

}

