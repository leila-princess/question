'use client';

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Space,
  Button,
  Select,
  Input,
  Spin,
  message,
  Table,
  Alert,
} from 'antd';
import { SearchOutlined, UndoOutlined, NodeIndexOutlined } from '@ant-design/icons';
import KnowledgeGraph, {
  GraphData,
  GraphNode,
  type ExploreConfig,
  type NodeDrawerBodyContext,
} from '@/components/KnowledgeGraph/KnowledgeGraph';
import {
  fetchTpzsBaseGraph,
  searchTpzsGraph,
  ClearGraphData,
  exploreNode as fetchKgExplore,
  fetchTpzsNodePanel,
  type TpzsNodePanelPayload,
} from '../../apiService';
import EnvironmentTable, {
  type EnvironmentTableRow,
  type ProjectNode,
  type IndicatorNode,
  type SelectedProjectEntry,
  type SelectedIndicatorEntry,
} from '@/components/EnvironmentTable';
import TpzsEnvironmentFieldsPanel from './TpzsEnvironmentFieldsPanel';

const nodeColors: Record<string, string> = {
  EquipmentTypeKind: '#096dd9',
  EquipmentType: '#1890ff',
  Equipment: '#52c41a',
  Environment: '#722ed1',
  TestProject: '#fa8c16',
  Indicator: '#13c2c2',
  File: '#eb2f96',
  TestMethod: '#faad14',
};

const edgeColors: Record<string, string> = {
  HAS: '#52c41a',
  CON: '#13c2c2',
  关联: '#fa8c16',
};

const nodeLabels: Record<string, string> = {
  EquipmentTypeKind: '设备一级分类',
  EquipmentType: '设备类型',
  Equipment: '具体设备',
  Environment: '环境',
  TestProject: '测试项目',
  Indicator: '指标',
  File: '文件',
  TestMethod: '试验方法',
};

const edgeLabels: Record<string, string> = {
  HAS: '包含',
  CON: '关联测试',
  关联: '关联',
};

const TPZS_EXPLORE_EDGE_TYPE_KEYS = Object.keys(edgeColors);

const SEARCHABLE_TYPES = [
  'EquipmentType',
  'Equipment',
  'Environment',
  'TestProject',
  'Indicator',
  'TestMethod',
  'File',
] as const;

function normalizeGraphPayload(nodes: GraphNode[], edges: GraphData['edges']): GraphData {
  const processedNodes: GraphNode[] = (nodes || []).map((node) => {
    const props = node.properties as { Name?: string; name?: string } | undefined;
    const label =
      props && (props.Name ?? props.name) != null && String(props.Name ?? props.name).length > 0
        ? String(props.Name ?? props.name)
        : node.label || '未命名';
    return { ...node, label };
  });
  return { nodes: processedNodes, edges: edges || [] };
}

function TpzsNodeDrawerBody({ node, onViewNeighbors }: NodeDrawerBodyContext) {
  const [loading, setLoading] = useState(false);
  const [panel, setPanel] = useState<TpzsNodePanelPayload | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setLoadError(null);
    setPanel(null);
    fetchTpzsNodePanel(String(node.id), node.group)
      .then((data) => {
        if (!cancelled) setPanel(data);
      })
      .catch((e) => {
        if (!cancelled) setLoadError(e instanceof Error ? e.message : '加载失败');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [node.id, node.group]);

  const noopProject = useCallback((_entry: SelectedProjectEntry, _checked: boolean) => {}, []);
  const noopIndicator = useCallback((_entry: SelectedIndicatorEntry, _checked: boolean) => {}, []);

  const envRows: EnvironmentTableRow[] = useMemo(() => {
    const raw = panel?.environmentRows;
    if (!raw?.length) return [];
    return raw.map((r) => ({
      equipmentTypeId: r.equipmentTypeId,
      equipmentTypeName: r.equipmentTypeName,
      projectsByEnv: (r.projectsByEnv || {}) as Record<string, ProjectNode[]>,
      indicators: (r.indicators || []) as IndicatorNode[],
    }));
  }, [panel]);

  const mainBlock = useMemo(() => {
    if (loading) {
      return (
        <div style={{ textAlign: 'center', padding: 24 }}>
          <Spin />
        </div>
      );
    }
    if (loadError) {
      return <Alert type="error" message={loadError} showIcon />;
    }
    if (!panel) return null;

    if (panel.panel === 'env_table' && envRows.length > 0) {
      return (
        <EnvironmentTable
          mode="ssyq"
          dataSource={envRows}
          selectedProjects={[]}
          selectedIndicators={[]}
          onProjectSelect={noopProject}
          onIndicatorSelect={noopIndicator}
          singleSelect
          showSelectAll={false}
          scrollY={380}
          emptyText="暂无设备-环境数据"
        />
      );
    }

    if (panel.panel === 'equipment_type_kind_list') {
      const types = panel.equipmentTypes || [];
      return (
        <Table
          size="small"
          pagination={false}
          rowKey={(r) => r.graphId || r.id}
          dataSource={types}
          locale={{ emptyText: '该一级分类下暂无设备类型' }}
          columns={[
            { title: '设备类型名称', dataIndex: 'name', key: 'name', ellipsis: true },
            { title: '标识', dataIndex: 'id', key: 'id', width: 120, ellipsis: true },
          ]}
        />
      );
    }

    if (panel.panel === 'equipment_type_devices') {
      const devices = panel.typeDevices || [];
      return (
        <Table
          size="small"
          pagination={false}
          rowKey="id"
          dataSource={devices}
          locale={{ emptyText: '该类型下暂无设备' }}
          columns={[
            { title: '设备名称', dataIndex: 'name', key: 'name', ellipsis: true },
            { title: '标识', dataIndex: 'id', key: 'id', width: 120, ellipsis: true },
          ]}
        />
      );
    }

    if (panel.panel === 'environment_fields' && panel.environmentFields?.length) {
      return <TpzsEnvironmentFieldsPanel fields={panel.environmentFields} />;
    }

    if (panel.panel === 'empty') {
      return <Alert type="info" message="暂无表格数据" showIcon />;
    }

    return (
      <Alert
        type="info"
        message={panel.hint || '该节点类型不按设备-环境表格展示。'}
        showIcon
      />
    );
  }, [loading, loadError, panel, envRows, noopProject, noopIndicator]);

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      {mainBlock}
      <Button type="default" icon={<NodeIndexOutlined />} block onClick={onViewNeighbors}>
        查看邻居节点
      </Button>
    </Space>
  );
}

const KnowledgeGraphView: React.FC = () => {
  const [graphData, setGraphData] = useState<GraphData | null>(null);
  const [initialLoading, setInitialLoading] = useState(true);
  const [searchLoading, setSearchLoading] = useState(false);
  const [nodeType, setNodeType] = useState<string>('Equipment');
  const [keyword, setKeyword] = useState('');

  const loadBase = useCallback(async () => {
    const result = await fetchTpzsBaseGraph(500);
    setGraphData(normalizeGraphPayload(result.nodes, result.edges));
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setInitialLoading(true);
      try {
        await loadBase();
      } catch (e) {
        if (!cancelled) {
          message.error(e instanceof Error ? e.message : '加载设备一级分类失败');
        }
      } finally {
        if (!cancelled) setInitialLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [loadBase]);

  const handleKgExplore = useCallback(async (nodeId: string, config: ExploreConfig): Promise<GraphData> => {
    const nt = config.targetNodeTypes?.length ? [...config.targetNodeTypes] : [];
    const et = config.targetEdgeTypes?.length ? [...config.targetEdgeTypes] : [];
    return fetchKgExplore(String(nodeId), config.maxDepth, nt, et, 400);
  }, []);

  const handleSearch = async () => {
    setSearchLoading(true);
    try {
      const result = await searchTpzsGraph(nodeType, keyword, 150);
      setGraphData(normalizeGraphPayload(result.nodes, result.edges));
      if (!result.nodes?.length) {
        message.info('未找到匹配节点，可更换关键词或节点类型后重试');
      }
    } catch (e) {
      message.error(e instanceof Error ? e.message : '查询失败');
    } finally {
      setSearchLoading(false);
    }
  };

  const handleResetToEquipmentTypes = async () => {
    setSearchLoading(true);
    try {
      await loadBase();
      setKeyword('');
      message.success('已恢复为设备一级分类视图');
    } catch (e) {
      message.error(e instanceof Error ? e.message : '恢复失败');
    } finally {
      setSearchLoading(false);
    }
  };

  const handleClearGraph = async () => {
    const response = await ClearGraphData([], [], 0);
    if (response.success) {
      return true;
    }
    throw new Error(response.message || '未知错误');
  };

  const typeSelectOptions = useMemo(
    () =>
      SEARCHABLE_TYPES.map((t) => ({
        value: t,
        label: nodeLabels[t] || t,
      })),
    [],
  );

  const handleNodeClick = useCallback((node: GraphNode) => {
    console.log('节点被点击:', node);
  }, []);

  const handleGraphError = useCallback((error: string) => {
    console.error('图谱错误:', error);
  }, []);

  const renderTpzsDrawer = useCallback((ctx: NodeDrawerBodyContext) => {
    return <TpzsNodeDrawerBody key={`${ctx.node.id}-${ctx.node.group}`} node={ctx.node} onViewNeighbors={ctx.onViewNeighbors} />;
  }, []);

  if (initialLoading || !graphData) {
    return (
      <div style={{ padding: '20px', textAlign: 'center' }}>
        <Spin size="large" tip="加载设备一级分类..." />
      </div>
    );
  }

  return (
    <div style={{ padding: '20px' }}>
      {/* <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <Title level={3} style={{ margin: 0 }}>
          知识图谱可视化
        </Title>
        <Space>
          <Popconfirm
            title="确定清空整个图数据库吗？"
            description="此操作不可恢复，所有节点和关系将被永久删除。"
            onConfirm={handleClearGraph}
            okText="确定"
            cancelText="取消"
            placement="bottomRight"
          >
            <Button icon={<DeleteOutlined />} danger>
              清空
            </Button>
          </Popconfirm>
        </Space>
      </div> */}

      <Space wrap style={{ marginBottom: 16 }} align="center">
        <span style={{ color: 'rgba(0,0,0,0.65)' }}>节点类型</span>
        <Select
          style={{ minWidth: 140 }}
          value={nodeType}
          options={typeSelectOptions}
          onChange={setNodeType}
        />
        <Input.Search
          allowClear
          placeholder="输入关键词模糊查询"
          style={{ width: 280 }}
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
          onSearch={handleSearch}
          enterButton={
            <Button type="primary" icon={<SearchOutlined />} loading={searchLoading}>
              查询
            </Button>
          }
        />
        <Button icon={<UndoOutlined />} onClick={handleResetToEquipmentTypes} disabled={searchLoading}>
          恢复一级分类
        </Button>
      </Space>

      <KnowledgeGraph
        initialData={graphData}
        clearData={handleClearGraph}
        exploreNode={handleKgExplore}
        exploreModalNodeTypes={['EquipmentTypeKind', ...SEARCHABLE_TYPES]}
        exploreModalEdgeTypes={TPZS_EXPLORE_EDGE_TYPE_KEYS}
        nodeColors={nodeColors}
        edgeColors={edgeColors}
        nodeLabels={nodeLabels}
        edgeLabels={edgeLabels}
        height={600}
        showLegend={true}
        showControls={true}
        showTable={false}
        title="知识图谱"
        onNodeClick={handleNodeClick}
        onError={handleGraphError}
        renderNodeDrawerBody={renderTpzsDrawer}
      />
    </div>
  );
};

export default KnowledgeGraphView;
