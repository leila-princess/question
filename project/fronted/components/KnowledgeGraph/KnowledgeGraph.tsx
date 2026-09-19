'use client';

import React, { useEffect, useRef, useState, useCallback } from 'react';
import {
  Card,
  Row,
  Col,
  Spin,
  message,
  Tag,
  Typography,
  Space,
  Button,
  Drawer,
  Divider,
  Input,
  Slider,
  Select,
  Checkbox,
  Empty,
  Tooltip,
  Badge,
  Alert,
} from 'antd';
import {
  ReloadOutlined,
  ExpandAltOutlined,
  InfoCircleOutlined,
  FilterOutlined,
  NodeIndexOutlined,
  SettingOutlined,
} from '@ant-design/icons';
// @ts-ignore
import { DataSet } from 'vis-data';
// @ts-ignore
import { Network } from 'vis-network';
import 'vis-network/styles/vis-network.css';

const { Title, Text, Paragraph } = Typography;
const { Search } = Input;
const { Option } = Select;

// ==================== 类型定义 ====================

export interface GraphNode {
  id: string;
  label: string;
  group: string;
  title?: string;
  value?: number;
  properties?: Record<string, any>;
}

export interface GraphEdge {
  id: string;
  from: string;
  to: string;
  label: string;
  properties?: Record<string, any>;
}

export interface GraphData {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

export interface ExploreConfig {
  centerNodeId: string | null;
  targetNodeTypes: string[];
  targetEdgeTypes: string[];
  maxDepth: number;
}

/** 自定义节点抽屉内容（替代默认标签/属性/「探索此节点」等） */
export interface NodeDrawerBodyContext {
  node: GraphNode;
  /** 与默认抽屉中「查看邻居节点」一致：合并邻居子图并聚焦相邻节点 */
  onViewNeighbors: () => void;
}

interface KnowledgeGraphProps {
  // 数据相关
  initialData?: GraphData;
  fetchData?: () => Promise<GraphData>;
  exploreNode?: (nodeId: string, config: ExploreConfig) => Promise<GraphData>;
  /** 探索抽屉中「目标节点类型」候选项；不传则从当前已加载子图推断（子图只有一种类型时会过窄） */
  exploreModalNodeTypes?: string[];
  /** 探索抽屉中「目标关系类型」候选项；不传则从当前已加载子图推断 */
  exploreModalEdgeTypes?: string[];
  clearData?: () => Promise<boolean>;
  
  // 配置相关
  nodeColors?: Record<string, string>;
  edgeColors?: Record<string, string>;
  nodeLabels?: Record<string, string>;
  edgeLabels?: Record<string, string>;
  
  // UI配置
  height?: number | string;
  showLegend?: boolean;
  showControls?: boolean;
  showTable?: boolean;
  title?: string;
  /** 点击节点时显示探索按钮而非弹窗 */
  inlineExploreMode?: boolean;
  
  // 回调函数
  onNodeClick?: (node: GraphNode) => void;
  onError?: (error: string) => void;
  /** 若提供，则替换节点抽屉内的默认详情区（仍由本组件负责 Drawer 外壳与开关） */
  renderNodeDrawerBody?: (ctx: NodeDrawerBodyContext) => React.ReactNode;
}

// ==================== 默认配置 ====================

/**
 * 默认节点颜色映射
 */
const DEFAULT_NODE_COLORS: Record<string, string> = {
  // 知识图谱节点类型
  Standard: '#1890ff',
  Clause: '#52c41a',
  Term: '#fa8c16',
  Organization: '#722ed1',
  Person: '#eb2f96',
  TestMethod: '#13c2c2',
  Condition: '#faad14',
  Device: '#8c8c8c',
  Table: '#f5222d',
  Figure: '#bfbfbf',
  // 试验关联查询节点类型
  equipment: '#1890ff',
  environment: '#52c41a',
  project: '#fa8c16',
  indicator: '#722ed1',
  method: '#13c2c2',
  standard: '#eb2f96',
};

const DEFAULT_EDGE_COLORS: Record<string, string> = {
  HAS_PART: '#722ed1',
  DEFINES: '#52c41a',
  PUBLISHED_BY: '#1890ff',
  DRAFTED_BY: '#eb2f96',
  CITES: '#fa8c16',
  USES_METHOD: '#13c2c2',
  UNDER_CONDITION: '#faad14',
  REQUIRES_DEVICE: '#8c8c8c',
  REQUIRES_MATERIAL: '#bfbfbf',
  CONTAINS_TABLE: '#f5222d',
  CONTAINS_FIGURE: '#bfbfbf',
  REFERENCED_BY: '#595959',
  HAS_ENVIRONMENT: '#52c41a',
  HAS_PROJECT: '#fa8c16',
  PROJECT_USES_INDICATOR: '#722ed1',
  INDICATOR_USES_METHOD: '#13c2c2',
  METHOD_REFERENCES_STANDARD: '#eb2f96',
};

const DEFAULT_NODE_LABELS: Record<string, string> = {
  Standard: '标准',
  Clause: '条款',
  Term: '术语',
  Organization: '机构',
  Person: '人员',
  TestMethod: '试验方法',
  Condition: '试验条件',
  Device: '设备',
  Table: '表格',
  Figure: '图片',
  equipment: '设备',
  environment: '环境',
  project: '项目',
  indicator: '指标',
  method: '方法',
  standard: '标准',
};

/**
 * 默认关系类型中文映射
 */
const DEFAULT_EDGE_LABELS: Record<string, string> = {
  HAS_PART: '包含',
  DEFINES: '定义',
  PUBLISHED_BY: '发布',
  DRAFTED_BY: '起草',
  CITES: '引用',
  USES_METHOD: '使用方法',
  UNDER_CONDITION: '条件',
  REQUIRES_DEVICE: '需要设备',
  REQUIRES_MATERIAL: '需要材料',
  CONTAINS_TABLE: '包含表格',
  CONTAINS_FIGURE: '包含图片',
  REFERENCED_BY: '被引用',
  HAS_ENVIRONMENT: '所属环境',
  HAS_PROJECT: '包含项目',
  PROJECT_USES_INDICATOR: '考核指标',
  INDICATOR_USES_METHOD: '使用方法',
  METHOD_REFERENCES_STANDARD: '参考标准',
};

// ==================== 知识图谱组件 ====================

const KnowledgeGraph: React.FC<KnowledgeGraphProps> = ({
  initialData,
  fetchData,
  exploreNode,
  exploreModalNodeTypes,
  exploreModalEdgeTypes,
  clearData,
  nodeColors = DEFAULT_NODE_COLORS,
  edgeColors = DEFAULT_EDGE_COLORS,
  nodeLabels = DEFAULT_NODE_LABELS,
  edgeLabels = DEFAULT_EDGE_LABELS,
  height = 600,
  showLegend = true,
  showControls = true,
  showTable = false,
  title = '知识图谱',
  inlineExploreMode = false,
  onNodeClick,
  onError,
  renderNodeDrawerBody,
}) => {
  // ==================== 状态定义 ====================
  
  // 图谱相关状态
  const graphRef = useRef<HTMLDivElement>(null);
  const [network, setNetwork] = useState<Network | null>(null);
  const [graphData, setGraphData] = useState<{ nodes: DataSet<any>; edges: DataSet<any> } | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  
  // 数据缓存
  const [allNodes, setAllNodes] = useState<GraphNode[]>([]);
  const [allEdges, setAllEdges] = useState<GraphEdge[]>([]);
  const allNodesRef = useRef<GraphNode[]>([]);
  const allEdgesRef = useRef<GraphEdge[]>([]);
  
  // 显示状态
  const [displayNodes, setDisplayNodes] = useState<GraphNode[]>([]);
  const [displayEdges, setDisplayEdges] = useState<GraphEdge[]>([]);
  
  // 节点详情
  const [drawerVisible, setDrawerVisible] = useState(false);
  const [selectedNode, setSelectedNode] = useState<GraphNode | null>(null);
  const [clickedNodeId, setClickedNodeId] = useState<string | null>(null);
  const [clickedNodeDomPos, setClickedNodeDomPos] = useState<{ left: number; top: number } | null>(null);
  
  // 图例过滤（用 ref 同步，避免 loadData 后 filter effect 读到旧 state / 旧闭包把节点滤没）
  const [activeNodeFilters, setActiveNodeFilters] = useState<Set<string>>(new Set());
  const [activeEdgeFilters, setActiveEdgeFilters] = useState<Set<string>>(new Set());
  const activeNodeFiltersRef = useRef<Set<string>>(new Set());
  const activeEdgeFiltersRef = useRef<Set<string>>(new Set());
  activeNodeFiltersRef.current = activeNodeFilters;
  activeEdgeFiltersRef.current = activeEdgeFilters;
  
  // inlineExploreMode ref（避免闭包捕获旧值）
  const inlineExploreModeRef = useRef(inlineExploreMode);
  inlineExploreModeRef.current = inlineExploreMode;
  
  // 探索配置
  const [exploreConfig, setExploreConfig] = useState<ExploreConfig>({
    centerNodeId: null,
    targetNodeTypes: [],
    targetEdgeTypes: [],
    maxDepth: 1,
  });
  const [exploreModalVisible, setExploreModalVisible] = useState(false);
  
  // 图例自动生成
  const [availableNodeTypes, setAvailableNodeTypes] = useState<string[]>([]);
  const [availableEdgeTypes, setAvailableEdgeTypes] = useState<string[]>([]);
  
  // ==================== 工具函数 ====================
  
  const getNodeColor = (group: string): string => {
    return nodeColors[group] || '#bfbfbf';
  };
  
  const getEdgeColor = (type: string): string => {
    return edgeColors[type] || '#bfbfbf';
  };
  
  const getNodeLabel = (group: string): string => {
    return nodeLabels[group] || group;
  };
  
  const getEdgeLabel = (type: string): string => {
    return edgeLabels[type] || type;
  };
  
  const generateLegendFromData = useCallback((nodes: GraphNode[], edges: GraphEdge[]) => {
    const nodeTypes = new Set<string>();
    const edgeTypes = new Set<string>();
    
    nodes.forEach(node => {
      if (node.group) nodeTypes.add(node.group);
    });
    
    edges.forEach(edge => {
      if (edge.label) edgeTypes.add(edge.label);
    });
    
    setAvailableNodeTypes(Array.from(nodeTypes));
    setAvailableEdgeTypes(Array.from(edgeTypes));
  }, []);
  
  // ==================== 图谱初始化 ====================
  
  const networkRef = useRef<Network | null>(null);
  const graphDataRef = useRef<{ nodes: DataSet<any>; edges: DataSet<any> } | null>(null);
  const initializedRef = useRef(false);
  const dataLoadedRef = useRef(false);
  
  const initGraph = useCallback(() => {
    if (!graphRef.current) return;
    
    // 销毁旧实例
    if (networkRef.current) {
      networkRef.current.destroy();
      networkRef.current = null;
      graphDataRef.current = null;
      setNetwork(null);
      setGraphData(null);
    }
    
    setLoading(true);
    setError(null);
    
    try {
      const nodes = new DataSet<any>([]);
      const edges = new DataSet<any>([]);
      
      const newNetwork = new Network(
        graphRef.current,
        { nodes, edges },
        {
          nodes: {
            shape: 'dot',
            size: 20,
            font: { size: 12, face: 'Tahoma' },
            borderWidth: 2,
          },
          edges: {
            width: 2,
            smooth: true,
            arrows: { to: { enabled: true, scaleFactor: 0.5 } },
            font: { align: 'middle', size: 10 },
          },
          physics: {
            stabilization: true,
            barnesHut: { gravitationalConstant: -8000, springConstant: 0.04, springLength: 200 },
          },
          interaction: { tooltipDelay: 200, hideEdgesOnDrag: true },
        }
      );
      
      // 节点点击事件
      newNetwork.on('click', (params: any) => {
        if (params.nodes.length > 0) {
          const nodeId = params.nodes[0];
          const node = allNodesRef.current.find(n => String(n.id) === String(nodeId));
          
          if (node) {
            setSelectedNode(node);
            setExploreConfig(prev => ({ ...prev, centerNodeId: nodeId }));
            
            if (inlineExploreModeRef.current && graphRef.current) {
              const canvasPos = newNetwork.getPositions([nodeId])[nodeId];
              const scale = newNetwork.getScale();
              const viewPos = newNetwork.getViewPosition();
              const containerRect = graphRef.current.getBoundingClientRect();
              
              const screenX = (canvasPos.x - viewPos.x) * scale + containerRect.width / 2;
              const screenY = (canvasPos.y - viewPos.y) * scale + containerRect.height / 2;
              
              setClickedNodeId(nodeId);
              setClickedNodeDomPos({
                left: screenX,
                top: screenY - 35,
              });
            } else {
              setDrawerVisible(true);
            }
            
            onNodeClick?.(node);
          }
        } else if (params.edges.length === 0) {
          setSelectedNode(null);
          setDrawerVisible(false);
          setClickedNodeId(null);
          setClickedNodeDomPos(null);
        }
      });
      
      // 双击重置
      newNetwork.on('doubleClick', () => {
        resetToAll();
      });
      
      networkRef.current = newNetwork;
      graphDataRef.current = { nodes, edges };
      setGraphData({ nodes, edges });
      setNetwork(newNetwork);
      initializedRef.current = true;
      setLoading(false);
    } catch (err: any) {
      const errorMessage = `图谱初始化失败: ${err.message}`;
      setError(errorMessage);
      setLoading(false);
      onError?.(errorMessage);
    }
  }, [onNodeClick, onError]);
  
  // 组件挂载时初始化
  useEffect(() => {
    if (graphRef.current && !initializedRef.current) {
      initGraph();
    }
    
    return () => {
      if (networkRef.current) {
        networkRef.current.destroy();
        networkRef.current = null;
        graphDataRef.current = null;
        // initGraph 随 onNodeClick 等 props 变化时 effect 会先 cleanup；若保持 true 则不会再 init，updateGraph 永远拿不到 network
        initializedRef.current = false;
      }
    };
  }, [initGraph]);
  
  // ==================== 数据加载 ====================
  
  const updateGraph = useCallback((
    nodes: GraphNode[],
    edges: GraphEdge[],
    opts?: { bypassFilters?: boolean },
  ) => {
    const currentGraphData = graphDataRef.current;
    const currentNetwork = networkRef.current;
    if (!currentGraphData || !currentNetwork) return;
    
    currentGraphData.nodes.clear();
    currentGraphData.edges.clear();
    
    const bypass = Boolean(opts?.bypassFilters);
    const nodeFilters = bypass ? new Set<string>() : activeNodeFiltersRef.current;
    const edgeFilters = bypass ? new Set<string>() : activeEdgeFiltersRef.current;
    // 应用图例过滤
    let filteredNodes = nodes;
    if (nodeFilters.size > 0) {
      filteredNodes = nodes.filter(n => nodeFilters.has(n.group));
    }
    
    let filteredEdges = edges;
    if (edgeFilters.size > 0) {
      filteredEdges = edges.filter(e => edgeFilters.has(e.label));
    }
    
    // 过滤孤立节点
    const connectedNodeIds = new Set<string>();
    filteredEdges.forEach(e => {
      connectedNodeIds.add(String(e.from));
      connectedNodeIds.add(String(e.to));
    });
    
    if (edgeFilters.size > 0) {
      filteredNodes = filteredNodes.filter(n => connectedNodeIds.has(String(n.id)));
    }
    
    // 转换为 vis.js 格式
    const visNodes = filteredNodes.map(node => ({
      id: String(node.id),
      label: node.label || '未命名',
      title: node.title || node.label,
      group: node.group,
      value: node.value || 10,
      shape: 'dot',
      size: (node.value || 10) * 2,
      color: nodeColors[node.group] || '#bfbfbf',
      font: { size: 12, face: 'Tahoma' },
    }));
    
    const visEdges = filteredEdges.map(edge => ({
      id: String(edge.id),
      from: String(edge.from),
      to: String(edge.to),
      label: edgeLabels[edge.label] || edge.label,
      color: edgeColors[edge.label] || '#bfbfbf',
      arrows: 'to',
      width: 2,
    }));
    
    const existingNodeIds = new Set(currentGraphData.nodes.getIds());
    const existingEdgeIds = new Set(currentGraphData.edges.getIds());
    const newNodes = visNodes.filter(n => !existingNodeIds.has(n.id));
    const newEdges = visEdges.filter(e => !existingEdgeIds.has(e.id));
    
    if (newNodes.length > 0) {
      currentGraphData.nodes.add(newNodes);
    }
    if (newEdges.length > 0) {
      try {
        currentGraphData.edges.add(newEdges);
      } catch (err: any) {
        // 如果添加边失败，尝试逐个添加，跳过已存在的边
        console.warn('批量添加边失败，尝试逐个添加:', err.message);
        for (const edge of newEdges) {
          if (!existingEdgeIds.has(edge.id)) {
            try {
              currentGraphData.edges.add(edge);
              existingEdgeIds.add(edge.id);
            } catch (e) {
              // 跳过已存在的边
              console.warn('跳过已存在的边:', edge.id);
            }
          }
        }
      }
    }
    
    currentNetwork.fit();
    currentNetwork.redraw();
    
    setDisplayNodes(filteredNodes);
    setDisplayEdges(filteredEdges);
  }, [nodeColors, edgeColors, nodeLabels, edgeLabels]);
  
  const loadData = useCallback((data: GraphData) => {
    activeNodeFiltersRef.current = new Set();
    activeEdgeFiltersRef.current = new Set();
    setActiveNodeFilters(new Set());
    setActiveEdgeFilters(new Set());
    setAllNodes(data.nodes);
    setAllEdges(data.edges);
    allNodesRef.current = data.nodes;
    allEdgesRef.current = data.edges;
    
    // 自动生成图例
    generateLegendFromData(data.nodes, data.edges);
    
    // 更新显示（新数据加载时忽略图例过滤，避免异步 setState 仍套用旧过滤）
    updateGraph(data.nodes, data.edges, { bypassFilters: true });
    setDisplayNodes(data.nodes);
    setDisplayEdges(data.edges);
  }, [generateLegendFromData, updateGraph]);
  
  const loadFromServer = async () => {
    if (!fetchData || dataLoadedRef.current) return;
    
    dataLoadedRef.current = true;
    setLoading(true);
    setError(null);
    
    try {
      const data = await fetchData();
      loadData(data);
    } catch (err: any) {
      const errorMessage = `数据加载失败: ${err.message}`;
      setError(errorMessage);
      onError?.(errorMessage);
    } finally {
      setLoading(false);
    }
  };
  
  const handleClearData = async () => {
    if (!clearData) return;
    
    setLoading(true);
    setError(null);
    
    try {
      const success = await clearData();
      if (success) {
        setAllNodes([]);
        setAllEdges([]);
        allNodesRef.current = [];
        allEdgesRef.current = [];
        setDisplayNodes([]);
        setDisplayEdges([]);
        generateLegendFromData([], []);
        setActiveNodeFilters(new Set());
        setActiveEdgeFilters(new Set());
        updateGraph([], [], { bypassFilters: true });
        dataLoadedRef.current = false;
        message.success('图谱数据已清空');
      }
    } catch (err: any) {
      const errorMessage = `清空失败: ${err.message}`;
      setError(errorMessage);
      onError?.(errorMessage);
    } finally {
      setLoading(false);
    }
  };

  // 受控数据：父组件通过 initialData 更新图谱（含空数组）
  useEffect(() => {
    if (initialData === undefined || initialData === null) return;
    loadData({
      nodes: initialData.nodes ?? [],
      edges: initialData.edges ?? [],
    });
  }, [initialData]);

  // 非受控：仅通过 fetchData 拉取一次
  useEffect(() => {
    if (initialData != null) return;
    if (fetchData && networkRef.current && graphDataRef.current) {
      loadFromServer();
    }
  }, [initialData, network, graphData, fetchData]);
  
  // 过滤条件变化时更新图谱（数据用 ref，避免晚于 loadData 仍读到上一批 allNodes）
  useEffect(() => {
    if (!networkRef.current || !graphDataRef.current) return;
    const nodes = allNodesRef.current;
    const edges = allEdgesRef.current;
    updateGraph(nodes, edges);
  }, [activeNodeFilters, activeEdgeFilters, updateGraph]);
  
  // ==================== 节点探索 ====================
  
  const handleExplore = async () => {
    if (!exploreConfig.centerNodeId) {
      message.warning('请先选择一个中心节点');
      return;
    }
    
    setLoading(true);
    
    try {
      if (exploreNode) {
        // 使用外部提供的探索函数
        const newData = await exploreNode(exploreConfig.centerNodeId, exploreConfig);

        // 合并新数据到现有数据
        const existingNodeIds = new Set(allNodes.map((n) => String(n.id)));
        const newNodes = newData.nodes.filter((n) => !existingNodeIds.has(String(n.id)));
        const existingEdgeIds = new Set(allEdges.map((e) => String(e.id)));
        const newEdges = (newData.edges || []).filter((e) => !existingEdgeIds.has(String(e.id)));

        const mergedNodes = [...allNodes, ...newNodes];
        const mergedEdges = [...allEdges, ...newEdges];

        setAllNodes(mergedNodes);
        setAllEdges(mergedEdges);
        allNodesRef.current = mergedNodes;
        allEdgesRef.current = mergedEdges;

        generateLegendFromData(mergedNodes, mergedEdges);

        // 更新图谱
        updateGraph(mergedNodes, mergedEdges);

        message.success(`探索到 ${newNodes.length} 个新节点，${newEdges.length} 条新关系`);
      } else {
        // 本地探索（基于现有数据）
        localExplore();
      }
      
      setExploreModalVisible(false);
    } catch (err: any) {
      message.error(`探索失败: ${err.message}`);
    } finally {
      setLoading(false);
    }
  };
  
  const localExplore = () => {
    const { centerNodeId, targetNodeTypes, targetEdgeTypes, maxDepth } = exploreConfig;
    
    if (!centerNodeId) return;
    
    const visitedIds = new Set<string>();
    const resultNodes: GraphNode[] = [];
    const resultEdges: GraphEdge[] = [];
    
    // BFS 探索
    const queue: { id: string; depth: number }[] = [{ id: centerNodeId, depth: 0 }];
    visitedIds.add(centerNodeId);
    
    while (queue.length > 0) {
      const { id, depth } = queue.shift()!;
      
      if (depth > maxDepth) continue;
      
      // 查找当前节点
      const currentNode = allNodesRef.current.find(n => String(n.id) === String(id));
      if (currentNode) {
        resultNodes.push(currentNode);
      }
      
      // 查找邻居
      allEdgesRef.current.forEach(edge => {
        const fromId = String(edge.from);
        const toId = String(edge.to);
        let neighborId: string | null = null;
        
        if (fromId === id) neighborId = toId;
        else if (toId === id) neighborId = fromId;
        
        if (neighborId && !visitedIds.has(neighborId)) {
          // 检查关系类型过滤
          if (targetEdgeTypes.length > 0 && !targetEdgeTypes.includes(edge.label)) {
            return;
          }
          
          visitedIds.add(neighborId);
          queue.push({ id: neighborId, depth: depth + 1 });
          resultEdges.push(edge);
        }
      });
    }
    
    // 检查节点类型过滤
    let filteredNodes = resultNodes;
    let filteredEdges = resultEdges;
    if (targetNodeTypes.length > 0) {
      filteredNodes = resultNodes.filter(n => targetNodeTypes.includes(n.group));
      const filteredNodeIds = new Set(filteredNodes.map(n => n.id));
      filteredEdges = resultEdges.filter(e => 
        filteredNodeIds.has(String(e.from)) && filteredNodeIds.has(String(e.to))
      );
    }
    
    setAllNodes(resultNodes);
    setAllEdges(resultEdges);
    allNodesRef.current = resultNodes;
    allEdgesRef.current = resultEdges;

    updateGraph(filteredNodes, filteredEdges);
    setDisplayNodes(filteredNodes);
    setDisplayEdges(filteredEdges);

    message.success(`探索到 ${filteredNodes.length} 个节点，${filteredEdges.length} 条关系`);
  };

  const exploreFromNode = async (nodeId: string) => {
    if (exploreNode) {
      setLoading(true);
      try {
        const cfg: ExploreConfig = {
          centerNodeId: nodeId,
          targetNodeTypes: [],
          targetEdgeTypes: [],
          maxDepth: 1,
        };
        const newData = await exploreNode(String(nodeId), cfg);
        const centerId = String(nodeId);
        const viewNodeMap = new Map<string, GraphNode>();
        const centerNode = allNodesRef.current.find((n) => String(n.id) === centerId);
        if (centerNode) {
          viewNodeMap.set(centerId, centerNode);
        }
        for (const n of newData.nodes || []) {
          viewNodeMap.set(String(n.id), n);
        }
        const viewNodes = Array.from(viewNodeMap.values());
        const viewIds = new Set(viewNodes.map((n) => String(n.id)));
        const viewEdges = (newData.edges || []).filter(
          (e) => viewIds.has(String(e.from)) && viewIds.has(String(e.to)),
        );

        setAllNodes(viewNodes);
        setAllEdges(viewEdges);
        allNodesRef.current = viewNodes;
        allEdgesRef.current = viewEdges;
        generateLegendFromData(viewNodes, viewEdges);
        updateGraph(viewNodes, viewEdges, { bypassFilters: true });
        setDisplayNodes(viewNodes);
        setDisplayEdges(viewEdges);
        message.success(`已展示 ${viewNodes.length} 个节点、${viewEdges.length} 条关系`);
      } catch (err: any) {
        message.error(err?.message || '加载邻居失败');
      } finally {
        setLoading(false);
      }
      return;
    }

    if (allNodesRef.current.length === 0) {
      message.warning('数据尚未加载');
      return;
    }

    const centerNode = allNodesRef.current.find((n) => String(n.id) === String(nodeId));
    if (!centerNode) return;

    // 找出所有邻居
    const neighborNodeIds = new Set<string>();
    const neighborEdges: GraphEdge[] = [];

    allEdgesRef.current.forEach((edge) => {
      if (String(edge.from) === String(nodeId)) {
        neighborNodeIds.add(String(edge.to));
        neighborEdges.push(edge);
      } else if (String(edge.to) === String(nodeId)) {
        neighborNodeIds.add(String(edge.from));
        neighborEdges.push(edge);
      }
    });

    neighborNodeIds.add(String(nodeId)); // 包含中心节点

    const displayNodesLocal = allNodesRef.current.filter((n) => neighborNodeIds.has(String(n.id)));
    const displayEdgesLocal = allEdgesRef.current.filter(
      (e) => neighborNodeIds.has(String(e.from)) && neighborNodeIds.has(String(e.to)),
    );

    console.log('[exploreFromNode] centerNodeId:', nodeId);
    console.log('[exploreFromNode] allNodes:', allNodesRef.current.length, allNodesRef.current.map(n => `${n.id}(${n.group})`));
    console.log('[exploreFromNode] allEdges:', allEdgesRef.current.length, allEdgesRef.current.map(e => `${e.from}->${e.to}(${e.label})`));
    console.log('[exploreFromNode] neighborNodeIds:', Array.from(neighborNodeIds));
    console.log('[exploreFromNode] displayNodesLocal:', displayNodesLocal.map(n => `${n.id}(${n.group})`));
    console.log('[exploreFromNode] displayEdgesLocal:', displayEdgesLocal.map(e => `${e.from}->${e.to}(${e.label})`));

    updateGraph(displayNodesLocal, displayEdgesLocal, { bypassFilters: true });
    setDisplayNodes(displayNodesLocal);
    setDisplayEdges(displayEdgesLocal);
  };
  
  const resetToAll = () => {
    if (allNodes.length === 0) return;
    
    updateGraph(allNodes, allEdges);
    setDisplayNodes(allNodes);
    setDisplayEdges(allEdges);
    setActiveNodeFilters(new Set());
    setActiveEdgeFilters(new Set());
  };
  
  const handleRefresh = () => {
    dataLoadedRef.current = false;
    if (fetchData) {
      loadFromServer();
    } else if (initialData) {
      loadData(initialData);
      resetToAll();
    }
  };
  
  // ==================== 图例过滤 ====================
  
  const handleNodeFilter = (nodeType: string) => {
    const newFilters = new Set(activeNodeFilters);
    if (newFilters.has(nodeType)) {
      newFilters.delete(nodeType);
    } else {
      newFilters.add(nodeType);
    }
    setActiveNodeFilters(newFilters);
  };
  
  const handleEdgeFilter = (edgeType: string) => {
    const newFilters = new Set(activeEdgeFilters);
    if (newFilters.has(edgeType)) {
      newFilters.delete(edgeType);
    } else {
      newFilters.add(edgeType);
    }
    setActiveEdgeFilters(newFilters);
  };
  
  // ==================== 渲染函数 ====================
  
  const renderLegend = () => {
    if (!showLegend) return null;
    
    return (
      <Row gutter={16} style={{ marginTop: 16 }}>
        <Col span={12}>
          <Card title="节点类型" size="small">
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
              {availableNodeTypes.length > 0 ? (
                availableNodeTypes.map(type => (
                  <Tooltip key={type} title={`点击${activeNodeFilters.has(type) ? '取消' : ''}过滤`}>
                    <Tag
                      color={getNodeColor(type)}
                      style={{
                        cursor: 'pointer',
                        opacity: activeNodeFilters.size === 0 || activeNodeFilters.has(type) ? 1 : 0.4,
                        padding: '4px 8px',
                      }}
                      onClick={() => handleNodeFilter(type)}
                    >
                      {getNodeLabel(type)}
                    </Tag>
                  </Tooltip>
                ))
              ) : (
                <Text type="secondary">暂无数据</Text>
              )}
            </div>
          </Card>
        </Col>
        <Col span={12}>
          <Card title="关系类型" size="small">
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
              {availableEdgeTypes.length > 0 ? (
                availableEdgeTypes.map(type => (
                  <Tooltip key={type} title={`点击${activeEdgeFilters.has(type) ? '取消' : ''}过滤`}>
                    <Tag
                      color={getEdgeColor(type)}
                      style={{
                        cursor: 'pointer',
                        opacity: activeEdgeFilters.size === 0 || activeEdgeFilters.has(type) ? 1 : 0.4,
                        padding: '4px 8px',
                      }}
                      onClick={() => handleEdgeFilter(type)}
                    >
                      {getEdgeLabel(type)}
                    </Tag>
                  </Tooltip>
                ))
              ) : (
                <Text type="secondary">暂无数据</Text>
              )}
            </div>
          </Card>
        </Col>
      </Row>
    );
  };
  
  const renderExploreModal = () => {
    const allNodeTypes =
      exploreModalNodeTypes && exploreModalNodeTypes.length > 0
        ? exploreModalNodeTypes
        : Array.from(new Set(allNodes.map((n) => n.group)));
    const allEdgeTypes =
      exploreModalEdgeTypes && exploreModalEdgeTypes.length > 0
        ? exploreModalEdgeTypes
        : Array.from(new Set(allEdges.map((e) => e.label)));
    
    return (
      <Drawer
        title={
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <SettingOutlined />
            <span>探索配置</span>
          </div>
        }
        placement="right"
        onClose={() => setExploreModalVisible(false)}
        open={exploreModalVisible}
        size={400}
      >
        <div style={{ marginBottom: 24 }}>
          <Alert
            message="探索说明"
            description="选择中心节点、目标节点类型和步长后，系统将展示与该节点关联的指定类型节点。"
            type="info"
            showIcon
            style={{ marginBottom: 16 }}
          />
          
          <div style={{ marginBottom: 16 }}>
            <Text strong>中心节点：</Text>
            <Text>
              {exploreConfig.centerNodeId
                ? allNodes.find(n => String(n.id) === String(exploreConfig.centerNodeId))?.label || '未知节点'
                : '未选择'}
            </Text>
          </div>
          
          <Divider />
          
          <div style={{ marginBottom: 16 }}>
            <Text strong>目标节点类型：</Text>
            <Checkbox.Group
              style={{ width: '100%', marginTop: 8 }}
              value={exploreConfig.targetNodeTypes}
              onChange={(values) => setExploreConfig(prev => ({ ...prev, targetNodeTypes: values }))}
            >
              <Row gutter={[8, 8]}>
                {allNodeTypes.map(type => (
                  <Col span={12} key={type}>
                    <Checkbox value={type}>
                      <Tag color={getNodeColor(type)} style={{ border: 'none' }}>
                        {getNodeLabel(type)}
                      </Tag>
                    </Checkbox>
                  </Col>
                ))}
              </Row>
            </Checkbox.Group>
            <Text type="secondary" style={{ fontSize: 12, display: 'block', marginTop: 8 }}>
              留空表示显示所有类型
            </Text>
          </div>
          
          <div style={{ marginBottom: 16 }}>
            <Text strong>目标关系类型：</Text>
            <Checkbox.Group
              style={{ width: '100%', marginTop: 8 }}
              value={exploreConfig.targetEdgeTypes}
              onChange={(values) => setExploreConfig(prev => ({ ...prev, targetEdgeTypes: values }))}
            >
              <Row gutter={[8, 8]}>
                {allEdgeTypes.map(type => (
                  <Col span={12} key={type}>
                    <Checkbox value={type}>
                      <Tag color={getEdgeColor(type)} style={{ border: 'none' }}>
                        {getEdgeLabel(type)}
                      </Tag>
                    </Checkbox>
                  </Col>
                ))}
              </Row>
            </Checkbox.Group>
            <Text type="secondary" style={{ fontSize: 12, display: 'block', marginTop: 8 }}>
              留空表示显示所有关系类型
            </Text>
          </div>
          
          <div style={{ marginBottom: 16 }}>
            <Text strong>探索步长：{exploreConfig.maxDepth}</Text>
            <Slider
              min={1}
              max={5}
              value={exploreConfig.maxDepth}
              onChange={(value) => setExploreConfig(prev => ({ ...prev, maxDepth: value }))}
              marks={{ 1: '1层', 2: '2层', 3: '3层', 4: '4层', 5: '5层' }}
            />
            <Text type="secondary" style={{ fontSize: 12 }}>
              步长越大，探索范围越广（建议不超过3层）
            </Text>
          </div>
          
          <Button
            type="primary"
            icon={<ExpandAltOutlined />}
            block
            onClick={handleExplore}
            disabled={!exploreConfig.centerNodeId}
          >
            开始探索
          </Button>
        </div>
      </Drawer>
    );
  };
  
  const renderNodeDrawer = () => {
    const onViewNeighbors = () => {
      if (!selectedNode) return;
      void exploreFromNode(selectedNode.id);
      setDrawerVisible(false);
    };

    return (
      <Drawer
        title={renderNodeDrawerBody && selectedNode ? selectedNode.label : '节点详情'}
        placement="right"
        onClose={() => setDrawerVisible(false)}
        open={drawerVisible}
        size={renderNodeDrawerBody ? 560 : 500}
        styles={{ body: { maxHeight: 'calc(100vh - 108px)', overflowY: 'auto' } }}
      >
        {selectedNode &&
          (renderNodeDrawerBody ? (
            renderNodeDrawerBody({ node: selectedNode, onViewNeighbors })
          ) : (
            <div>
              <div style={{ marginBottom: 16 }}>
                <Tag color={getNodeColor(selectedNode.group)} style={{ fontSize: 12, padding: '4px 8px' }}>
                  {getNodeLabel(selectedNode.group)}
                </Tag>
              </div>

              <Title level={4}>{selectedNode.label}</Title>
              {selectedNode.title && <Paragraph type="secondary">{selectedNode.title}</Paragraph>}

              <Divider />

              <div style={{ marginBottom: 16 }}>
                <Text strong>节点 ID：</Text>
                <Text code style={{ marginLeft: 8 }}>
                  {selectedNode.id}
                </Text>
              </div>

              {selectedNode.properties && Object.keys(selectedNode.properties).length > 0 && (
                <div style={{ marginBottom: 16 }}>
                  <Text strong>属性信息：</Text>
                  <div style={{ marginTop: 8, background: '#f5f5f5', padding: 12, borderRadius: 4 }}>
                    {Object.entries(selectedNode.properties).map(([key, value]) => (
                      <div key={key} style={{ marginBottom: 8 }}>
                        <Text strong style={{ fontSize: 12 }}>
                          {key}：
                        </Text>
                        <Text style={{ fontSize: 12 }}>
                          {typeof value === 'object' ? JSON.stringify(value) : String(value)}
                        </Text>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              <Divider />

              <Space direction="vertical" style={{ width: '100%' }}>
                <Button
                  type="primary"
                  icon={<ExpandAltOutlined />}
                  block
                  onClick={() => {
                    setExploreConfig((prev) => ({ ...prev, centerNodeId: selectedNode.id }));
                    setExploreModalVisible(true);
                  }}
                >
                  探索此节点
                </Button>
                <Button icon={<NodeIndexOutlined />} block onClick={onViewNeighbors}>
                  查看邻居节点
                </Button>
              </Space>
            </div>
          ))}
      </Drawer>
    );
  };
  
  // ==================== 主渲染 ====================
  
  return (
    <div>
      {/* 标题栏 */}
      {title && (
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
          <Title level={4} style={{ margin: 0 }}>{title}</Title>
          {showControls && (
            <Space>
              {/* <Button
                icon={<ExpandAltOutlined />}
                onClick={() => setExploreModalVisible(true)}
                disabled={allNodes.length === 0}
              >
                探索
              </Button> */}
               {/* 内联探索按钮 */}
        {inlineExploreMode && clickedNodeId && clickedNodeDomPos && (
          <div
            // style={{
            //   position: 'absolute',
            //   left: `${clickedNodeDomPos.left}px`,
            //   top: `${clickedNodeDomPos.top}px`,
            //   zIndex: 10,
            //   pointerEvents: 'auto',
            // }}
          >
            <Button
              type="primary"
              icon={<ExpandAltOutlined />}
              size="small"
              onClick={(e) => {
                e.stopPropagation();
                if (exploreNode) {
                  handleExplore();
                } else {
                  exploreFromNode(clickedNodeId);
                }
                setClickedNodeId(null);
                setClickedNodeDomPos(null);
              }}
            >
              探索节点
            </Button>
          </div>
        )}
              <Button icon={<ReloadOutlined />} onClick={handleRefresh} loading={loading}>
                刷新
              </Button>
            </Space>
          )}
        </div>
      )}
      
      {/* 图谱容器 */}
      <div style={{ position: 'relative', marginBottom: showLegend ? 0 : 16 }}>
        <div
          ref={graphRef}
          style={{
            width: '100%',
            height: typeof height === 'string' ? height : `${height}px`,
            border: '1px solid #e8e8e8',
            borderRadius: '8px',
            backgroundColor: '#fafafa',
          }}
        />
        
       
        
        {/* 加载遮罩 */}
        {loading && (
          <div
            style={{
              position: 'absolute',
              top: 0,
              left: 0,
              right: 0,
              bottom: 0,
              display: 'flex',
              justifyContent: 'center',
              alignItems: 'center',
              background: 'rgba(255,255,255,0.7)',
              borderRadius: '8px',
            }}
          >
            <Spin size="large" tip="加载中..." />
          </div>
        )}
        
        {/* 错误提示 */}
        {error && (
          <div
            style={{
              position: 'absolute',
              top: 0,
              left: 0,
              right: 0,
              bottom: 0,
              display: 'flex',
              justifyContent: 'center',
              alignItems: 'center',
              flexDirection: 'column',
              borderRadius: '8px',
              background: 'rgba(255,255,255,0.9)',
            }}
          >
            <Text type="danger" style={{ marginBottom: 16 }}>{error}</Text>
            <Button type="primary" onClick={handleRefresh}>
              重试
            </Button>
          </div>
        )}
        
        {/* 空数据提示 */}
        {allNodes.length === 0 && !loading && !error && (
          <div
            style={{
              position: 'absolute',
              top: 0,
              left: 0,
              right: 0,
              bottom: 0,
              display: 'flex',
              justifyContent: 'center',
              alignItems: 'center',
              borderRadius: '8px',
            }}
          >
            <Empty description="暂无数据" image={Empty.PRESENTED_IMAGE_SIMPLE} />
          </div>
        )}
      </div>
      
      {/* 节点统计 */}
      {displayNodes.length > 0 && (
        <div style={{ marginBottom: 16 }}>
          <Space wrap>
            <Text type="secondary">当前显示：</Text>
            <Badge count={displayNodes.length} style={{ backgroundColor: '#1890ff' }} />
            <Text type="secondary">个节点</Text>
            <Badge count={displayEdges.length} style={{ backgroundColor: '#52c41a' }} />
            <Text type="secondary">条关系</Text>
          </Space>
        </div>
      )}
      
      {/* 图例 */}
      {renderLegend()}
      
      {/* 探索配置抽屉 */}
      {renderExploreModal()}
      
      {/* 节点详情抽屉 */}
      {renderNodeDrawer()}
    </div>
  );
};

export default KnowledgeGraph;
