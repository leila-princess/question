"use client";
import React, { useEffect, useRef, useState } from 'react';
import { Button, Card, Row, Col, Input, Select, Typography, Spin, Tooltip, Modal, message } from 'antd';
import { 
  RedoOutlined, 
  DownloadOutlined, 
  SettingOutlined, 
  FullscreenOutlined,
  FullscreenExitOutlined,
  ZoomInOutlined,
  ZoomOutOutlined,
  SearchOutlined
} from '@ant-design/icons';
import styles from './KnowledgeGraph.module.css';
import { fetchAllGraphData } from "@/app/apiService";

const { Title, Text } = Typography;
const { Option } = Select;

// 动态导入 vis-network
let Network: any, DataSet: any;
if (typeof window !== 'undefined') {
  Network = require('vis-network').Network;
  DataSet = require('vis-data').DataSet;
}

interface KnowledgeGraphProps {
  height?: number;
  onNodeClick?: (node: any) => void;
  showControls?: boolean;
  fullscreen?: boolean;
  autoLoad?: boolean;
}

interface GraphData {
  nodes: any;
  edges: any;
}

interface Node {
  id: number | string;
  label: string;
  group: string;
  value?: number;
  color?: string;
  properties?: any;
}

interface Edge {
  id?: string;
  from: number | string;
  to: number | string;
  label: string;
  color?: string;
  dashes?: boolean;
}

// 模拟数据（作为备份）
const getMockData = () => {
  return {
    success: true,
    nodes: [
      { id: 1, label: 'GJB 150.3A-2009', group: 'standard', color: '#722ed1', title: '军用设备环境试验方法标准' },
      { id: 2, label: '高温试验', group: 'test', color: '#52c41a', title: '温度适应性试验项目' },
      { id: 3, label: '温度范围', group: 'indicator', color: '#faad14', title: '工作温度范围指标' },
      { id: 4, label: '温度循环试验', group: 'method', color: '#13c2c2', title: '温度循环试验方法' },
      { id: 5, label: '航空装备', group: 'category', color: '#1890ff', title: '航空类装备' },
      { id: 6, label: 'GB/T 2423.2-2008', group: 'standard', color: '#722ed1', title: '电工电子产品环境试验标准' },
      { id: 7, label: '可靠性试验', group: 'test', color: '#52c41a', title: '设备可靠性验证试验' },
      { id: 8, label: '耐久性', group: 'indicator', color: '#faad14', title: '设备耐久性指标' },
      { id: 9, label: '地面装备', group: 'category', color: '#1890ff', title: '地面装备类别' },
      { id: 10, label: '振动试验', group: 'test', color: '#52c41a', title: '机械振动试验项目' },
    ],
    edges: [
      { id: 'e1', from: 1, to: 2, label: '包含', color: '#666' },
      { id: 'e2', from: 2, to: 3, label: '考核', color: '#666' },
      { id: 'e3', from: 2, to: 4, label: '使用', color: '#666' },
      { id: 'e4', from: 5, to: 2, label: '适用', color: '#666' },
      { id: 'e5', from: 6, to: 2, label: '引用', color: '#666' },
      { id: 'e6', from: 1, to: 7, label: '包含', color: '#666' },
      { id: 'e7', from: 7, to: 8, label: '考核', color: '#666' },
      { id: 'e8', from: 9, to: 10, label: '包含', color: '#666' },
      { id: 'e9', from: 1, to: 10, label: '参考', color: '#666' },
      { id: 'e10', from: 3, to: 8, label: '关联', color: '#666' },
    ]
  };
};

const KnowledgeGraph: React.FC<KnowledgeGraphProps> = ({ 
  height = 600,
  onNodeClick,
  showControls = true,
  fullscreen = true,
  autoLoad = true
}) => {
  const graphRef = useRef<HTMLDivElement>(null);
  const [network, setNetwork] = useState<any>(null);
  const [graphData, setGraphData] = useState<GraphData | null>(null);
  const [graphLoading, setGraphLoading] = useState(true);
  const [graphError, setGraphError] = useState<string | null>(null);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [zoomLevel, setZoomLevel] = useState(1);
  const containerRef = useRef<HTMLDivElement>(null);
  const [allGraphData, setAllGraphData] = useState<any>(null);
  const [networkInitialized, setNetworkInitialized] = useState(false);

  // 获取节点类型标签
  const getNodeTypeLabel = (group: string) => {
    const typeMap: Record<string, string> = {
      'category': '装备类别',
      'test': '试验项目',
      'indicator': '考核指标',
      'method': '试验方法',
      'standard': '标准规范',
      'equipment': '装备',
      'document': '文档',
      'requirement': '需求',
      'EquipmentType': '装备类型',
      'TestType': '试验类型',
      'IndicatorType': '指标类型',
      'Standard': '标准'
    };
    return typeMap[group] || group;
  };

  // 初始化网络实例
  const initNetwork = () => {
    if (!graphRef.current || !Network || !DataSet || networkInitialized) {
      return;
    }

    try {
      console.log('初始化网络实例...');
      
      const nodes = new DataSet([]);
      const edges = new DataSet([]);
      
      const newNetwork = new Network(
        graphRef.current,
        { nodes, edges },
        {
          nodes: {
            shape: 'dot',
            size: 30,
            font: { 
              size: 14,
              face: 'Tahoma',
              strokeWidth: 2,
              strokeColor: 'rgba(255,255,255,0.8)'
            },
            borderWidth: 2,
            color: {
              border: '#2B7CE9',
              background: '#97C2FC',
              highlight: {
                border: '#2B7CE9',
                background: '#D2E5FF'
              }
            },
            shadow: true,
            scaling: {
              min: 10,
              max: 30
            }
          },
          edges: {
            width: 2,
            color: {
              color: '#848484',
              highlight: '#FF0000'
            },
            smooth: {
              type: 'dynamic'
            },
            arrows: {
              to: {
                enabled: true,
                scaleFactor: 0.8
              }
            },
            font: {
              size: 11,
              align: 'middle'
            },
            scaling: {
              min: 1,
              max: 15
            }
          },
          physics: {
            enabled: true,
            solver: 'forceAtlas2Based',
            forceAtlas2Based: {
              gravitationalConstant: -50,
              centralGravity: 0.01,
              springLength: 100,
              springConstant: 0.08
            },
            minVelocity: 0.75,
            maxVelocity: 50,
            stabilization: {
              enabled: true,
              iterations: 1000,
              updateInterval: 25
            }
          },
          interaction: {
            dragNodes: true,
            dragView: true,
            hideEdgesOnDrag: false,
            hideNodesOnDrag: false,
            hover: true,
            hoverConnectedEdges: true,
            keyboard: {
              enabled: true,
              speed: { x: 10, y: 10, zoom: 0.02 },
              bindToWindow: true
            },
            multiselect: false,
            navigationButtons: false,
            selectable: true,
            selectConnectedEdges: true,
            tooltipDelay: 300,
            zoomView: true
          },
          layout: {
            improvedLayout: true,
            hierarchical: {
              enabled: false,
              levelSeparation: 150,
              nodeSpacing: 100,
              treeSpacing: 200,
              blockShifting: true,
              edgeMinimization: true,
              parentCentralization: true,
              direction: 'UD',
              sortMethod: 'hubsize'
            }
          }
        }
      );

      // 节点点击事件
      newNetwork.on('click', function(params: any) {
        if (params.nodes.length > 0) {
          const nodeId = params.nodes[0];
          const node = nodes.get(nodeId);
          if (node) {
            // 触发外部回调
            if (onNodeClick) {
              onNodeClick(node);
            }
            
            // 显示节点详情
            Modal.info({
              title: `节点详情: ${node.label || node.id}`,
              content: (
                <div>
                  <p><strong>节点类型:</strong> {getNodeTypeLabel(node.group) || '未知类型'}</p>
                  <p><strong>节点ID:</strong> {node.id}</p>
                  <p><strong>标签:</strong> {node.label || node.id}</p>
                  {node.title && <p><strong>描述:</strong> {node.title}</p>}
                  {node.value && <p><strong>权重:</strong> {node.value}</p>}
                  {node.properties && (
                    <div>
                      <p><strong>属性:</strong></p>
                      <pre style={{ fontSize: '12px', maxHeight: '200px', overflow: 'auto', backgroundColor: '#f5f5f5', padding: '8px' }}>
                        {JSON.stringify(node.properties, null, 2)}
                      </pre>
                    </div>
                  )}
                </div>
              ),
              okText: '关闭',
              width: 500,
              maskClosable: true
            });
          }
        }
      });

      // 缩放事件
      newNetwork.on('zoom', function(params: any) {
        setZoomLevel(params.scale || 1);
      });

      // 悬停事件
      newNetwork.on('hoverNode', function(params: any) {
        const nodeId = params.node;
        const node = nodes.get(nodeId);
        if (node) {
          // 可以在这里添加悬停提示
        }
      });

      setNetwork(newNetwork);
      setGraphData({ nodes, edges });
      setNetworkInitialized(true);
      console.log('网络实例初始化完成');
      
    } catch (error: any) {
      console.error('初始化网络失败:', error);
      setGraphError(`初始化失败: ${error.message}`);
      setGraphLoading(false);
    }
  };

  // 获取所有图谱数据
  const fetchAllGraph = async () => {
    if (!graphData) {
      console.error('图谱数据未初始化');
      setGraphError('图谱数据未初始化');
      return;
    }
    
    setGraphLoading(true);
    setGraphError(null);
    
    try {
      console.log('开始获取图谱数据...');
      const result = await fetchAllGraphData();
      console.log('获取到的图谱数据:', result);
      
      if (result.nodes && Array.isArray(result.nodes)) {
        setAllGraphData(result);
        
        // 清空现有数据
        graphData.nodes.clear();
        graphData.edges.clear();
        
        // 转换节点数据格式
        const formattedNodes = result.nodes.map((node: any) => {
          // 根据实际后端数据结构调整
          return {
            id: node.id || node.element_id || Math.random().toString(36).substr(2, 9),
            label: node.label || node.name || node.properties?.name || `节点${Math.random().toString(36).substr(2, 4)}`,
            group: node.group || node.labels?.[0] || node.type || 'unknown',
            title: node.title || node.properties?.description || node.description || '',
            color: node.color || getGroupColor(node.group || node.labels?.[0]),
            value: node.value || node.size || 1,
            properties: node.properties || node
          };
        });
        
        // 转换边数据格式
        const formattedEdges = result.edges?.map((edge: any, index: number) => {
          return {
            id: edge.id || `edge_${index}`,
            from: edge.from || edge.start || edge.source,
            to: edge.to || edge.end || edge.target,
            label: edge.label || edge.type || edge.relationship || '关联',
            color: edge.color || '#848484',
            dashes: edge.dashes || false,
            arrows: edge.arrows || 'to'
          };
        }) || [];
        
        // 添加数据到网络
        graphData.nodes.add(formattedNodes);
        graphData.edges.add(formattedEdges);
        
        // 如果需要，重新布局
        if (network) {
          setTimeout(() => {
            network.fit({
              animation: {
                duration: 1000,
                easingFunction: 'easeInOutQuad'
              }
            });
          }, 100);
        }
        
        message.success(`加载成功: ${formattedNodes.length} 个节点, ${formattedEdges.length} 条边`);
        console.log(`数据加载完成: ${formattedNodes.length} 个节点, ${formattedEdges.length} 条边`);
      } else {
        // 如果没有数据或数据格式不正确，使用模拟数据
        console.warn('后端数据格式不正确，使用模拟数据');
        const mockData = getMockData();
        setAllGraphData(mockData);
        
        graphData.nodes.clear();
        graphData.edges.clear();
        graphData.nodes.add(mockData.nodes);
        graphData.edges.add(mockData.edges);
        
        if (network) {
          setTimeout(() => {
            network.fit({
              animation: {
                duration: 1000,
                easingFunction: 'easeInOutQuad'
              }
            });
          }, 100);
        }
        
        message.success(`使用模拟数据: ${mockData.nodes.length} 个节点, ${mockData.edges.length} 条边`);
      }
    } catch (error: any) {
      console.error('获取图谱数据失败:', error);
      setGraphError(`数据加载失败: ${error.message}`);
      message.error('数据加载失败，使用模拟数据');
      
      // 使用模拟数据作为后备
      const mockData = getMockData();
      setAllGraphData(mockData);
      
      if (graphData) {
        graphData.nodes.clear();
        graphData.edges.clear();
        graphData.nodes.add(mockData.nodes);
        graphData.edges.add(mockData.edges);
        
        if (network) {
          setTimeout(() => {
            network.fit();
          }, 100);
        }
      }
    } finally {
      setGraphLoading(false);
    }
  };

  // 根据分组获取颜色
  const getGroupColor = (group: string): string => {
    const colorMap: Record<string, string> = {
      'category': '#1890ff',
      'test': '#52c41a',
      'indicator': '#faad14',
      'method': '#13c2c2',
      'standard': '#722ed1',
      'equipment': '#f5222d',
      'document': '#eb2f96',
      'EquipmentType': '#1890ff',
      'TestType': '#52c41a',
      'IndicatorType': '#faad14',
      'Standard': '#722ed1'
    };
    return colorMap[group] || '#999999';
  };

  // 根据关键词搜索图谱
  const searchGraph = async (keyword = "") => {
    if (!graphData || !network || !allGraphData) {
      setGraphError('图谱未初始化');
      return;
    }
    
    setGraphLoading(true);
    setGraphError(null);
    
    try {
      if (keyword.trim() === "") {
        // 显示所有数据
        graphData.nodes.clear();
        graphData.edges.clear();
        graphData.nodes.add(allGraphData.nodes || []);
        graphData.edges.add(allGraphData.edges || []);
      } else {
        // 过滤节点
        const filteredNodes = allGraphData.nodes.filter((node: any) => {
          const label = node.label || node.name || '';
          const title = node.title || node.description || '';
          return label.toLowerCase().includes(keyword.toLowerCase()) || 
                 title.toLowerCase().includes(keyword.toLowerCase());
        });
        const filteredNodeIds = filteredNodes.map((node: any) => node.id);
        
        // 过滤边（只保留与过滤节点相关的边）
        const filteredEdges = allGraphData.edges?.filter((edge: any) =>
          filteredNodeIds.includes(edge.from) || filteredNodeIds.includes(edge.to)
        ) || [];

        graphData.nodes.clear();
        graphData.edges.clear();
        graphData.nodes.add(filteredNodes);
        graphData.edges.add(filteredEdges);
      }
      
      network.fit({
        animation: {
          duration: 500,
          easingFunction: 'easeInOutQuad'
        }
      });
      
      const nodeCount = graphData.nodes.length;
      const edgeCount = graphData.edges.length;
      message.success(`搜索完成: 找到 ${nodeCount} 个节点, ${edgeCount} 条边`);
    } catch (error: any) {
      setGraphError(`搜索失败: ${error.message}`);
      message.error('搜索失败');
    } finally {
      setGraphLoading(false);
    }
  };

  // 处理搜索
  const handleSearch = (value: string) => {
    setSearchQuery(value);
    searchGraph(value);
  };

  // 处理刷新
  const handleRefresh = () => {
    setSearchQuery('');
    fetchAllGraph();
  };

  // 处理缩放
  const handleZoomIn = () => {
    if (network) {
      const currentScale = network.getScale();
      network.moveTo({
        scale: currentScale * 1.2,
        animation: {
          duration: 300,
          easingFunction: 'easeInOutQuad'
        }
      });
    }
  };

  const handleZoomOut = () => {
    if (network) {
      const currentScale = network.getScale();
      network.moveTo({
        scale: currentScale * 0.8,
        animation: {
          duration: 300,
          easingFunction: 'easeInOutQuad'
        }
      });
    }
  };

  // 处理全屏
  const handleFullscreen = async () => {
    if (!containerRef.current) return;

    try {
      if (!isFullscreen) {
        if (containerRef.current.requestFullscreen) {
          await containerRef.current.requestFullscreen();
        } else if ((containerRef.current as any).webkitRequestFullscreen) {
          await (containerRef.current as any).webkitRequestFullscreen();
        } else if ((containerRef.current as any).msRequestFullscreen) {
          await (containerRef.current as any).msRequestFullscreen();
        }
      } else {
        if (document.exitFullscreen) {
          await document.exitFullscreen();
        } else if ((document as any).webkitExitFullscreen) {
          await (document as any).webkitExitFullscreen();
        } else if ((document as any).msExitFullscreen) {
          await (document as any).msExitFullscreen();
        }
      }
    } catch (error) {
      console.error('全屏操作失败:', error);
      message.error('全屏操作失败');
    }
  };

  // 导出图谱
  const handleExport = () => {
    if (network && graphData) {
      try {
        const nodes = graphData.nodes.get();
        const edges = graphData.edges.get();
        const data = {
          nodes,
          edges,
          positions: network.getPositions(),
          scale: network.getScale(),
          viewPosition: network.getViewPosition()
        };
        
        const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `knowledge-graph-export-${new Date().toISOString().slice(0, 10)}.json`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
        message.success(`图谱已导出: ${nodes.length} 个节点, ${edges.length} 条边`);
      } catch (error) {
        console.error('导出失败:', error);
        message.error('导出失败');
      }
    } else {
      message.warning('图谱未初始化，无法导出');
    }
  };

  // 监听全屏变化
  useEffect(() => {
    const handleFullscreenChange = () => {
      setIsFullscreen(!!document.fullscreenElement);
    };

    document.addEventListener('fullscreenchange', handleFullscreenChange);
    document.addEventListener('webkitfullscreenchange', handleFullscreenChange);
    document.addEventListener('msfullscreenchange', handleFullscreenChange);

    return () => {
      document.removeEventListener('fullscreenchange', handleFullscreenChange);
      document.removeEventListener('webkitfullscreenchange', handleFullscreenChange);
      document.removeEventListener('msfullscreenchange', handleFullscreenChange);
    };
  }, []);

  // 初始化网络
  useEffect(() => {
    if (!networkInitialized && graphRef.current) {
      // 延迟初始化，确保DOM完全加载
      const timer = setTimeout(() => {
        initNetwork();
      }, 300);
      
      return () => clearTimeout(timer);
    }
  }, [graphRef.current, networkInitialized]);

  // 加载数据
  useEffect(() => {
    if (autoLoad && networkInitialized && graphData) {
      console.log('开始自动加载数据...');
      fetchAllGraph();
    }
  }, [autoLoad, networkInitialized, graphData]);

  // 清理网络实例
  useEffect(() => {
    return () => {
      if (network) {
        console.log('清理网络实例...');
        network.destroy();
      }
    };
  }, [network]);

  if (graphError && !graphLoading) {
    return (
      <div 
        ref={containerRef}
        className={`${styles.knowledgeGraphContainer} ${isFullscreen ? styles.fullscreen : ''}`}
        style={{ height: '100%', display: 'flex', flexDirection: 'column' }}
      >
        <div className={styles.errorContainer} style={{ flex: 1, display: 'flex', flexDirection: 'column', justifyContent: 'center', alignItems: 'center' }}>
          <Text type="danger">{graphError}</Text>
          <Button 
            type="primary" 
            onClick={() => {
              setNetworkInitialized(false);
              setGraphError(null);
              setGraphLoading(true);
              setTimeout(() => {
                initNetwork();
                setTimeout(() => fetchAllGraph(), 300);
              }, 100);
            }}
            style={{ marginTop: 16 }}
          >
            重新加载
          </Button>
        </div>
      </div>
    );
  }

// 修改 KnowledgeGraph 组件的容器部分
return (
  <div 
    ref={containerRef}
    className={`${styles.knowledgeGraphContainer} ${isFullscreen ? styles.fullscreen : ''}`}
    style={{ 
      height: '100%', 
      display: 'flex', 
      flexDirection: 'column',
      minHeight: height  // 确保最小高度
    }}
  >
    {showControls && (
      <div className={styles.controls} style={{ 
        marginBottom: 16, 
        padding: '12px 16px',
        backgroundColor: '#fff',
        borderBottom: '1px solid #f0f0f0'
      }}>
        <Row justify="space-between" align="middle" gutter={16}>
          <Col flex="auto">
            <div className={styles.controlGroup} style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
              <Input.Search
                placeholder="搜索节点名称或描述..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                onSearch={handleSearch}
                prefix={<SearchOutlined />}
                style={{ width: 280 }}
                allowClear
                enterButton="搜索"
                size="middle"
              />
              <Select defaultValue="force" style={{ width: 140 }}>
                <Option value="force">力导向布局</Option>
                <Option value="hierarchical">层次布局</Option>
                <Option value="circular">环形布局</Option>
              </Select>
            </div>
          </Col>
          <Col>
            <div className={styles.controlGroup} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <Tooltip title="放大">
                <Button icon={<ZoomInOutlined />} onClick={handleZoomIn} size="middle" />
              </Tooltip>
              <span style={{ margin: '0 8px', minWidth: 60, textAlign: 'center', fontSize: 14 }}>
                {Math.round(zoomLevel * 100)}%
              </span>
              <Tooltip title="缩小">
                <Button icon={<ZoomOutOutlined />} onClick={handleZoomOut} size="middle" />
              </Tooltip>
              <Tooltip title="刷新数据">
                <Button icon={<RedoOutlined />} onClick={handleRefresh} size="middle" />
              </Tooltip>
              <Tooltip title="导出数据">
                <Button icon={<DownloadOutlined />} onClick={handleExport} size="middle" />
              </Tooltip>
              {fullscreen && (
                <Tooltip title={isFullscreen ? "退出全屏" : "全屏查看"}>
                  <Button 
                    icon={isFullscreen ? <FullscreenExitOutlined /> : <FullscreenOutlined />} 
                    onClick={handleFullscreen}
                    size="middle"
                  />
                </Tooltip>
              )}
            </div>
          </Col>
        </Row>
      </div>
    )}

    <div className={styles.graphWrapper} style={{ 
      flex: 1, 
      position: 'relative',
      minHeight: 500,  // 增加最小高度
      height: 'calc(100% - 60px)'  // 动态计算高度，减去控制栏高度
    }}>
      <div
        ref={graphRef}
        className={styles.graphArea}
        style={{ 
          height: '100%', 
          width: '100%',
          border: '1px solid #d9d9d9',
          borderRadius: '4px',
          backgroundColor: '#fafafa'
        }}
      />
      
      {graphLoading && (
        <div style={{
          position: 'absolute',
          top: 0,
          left: 0,
          right: 0,
          bottom: 0,
          display: 'flex',
          justifyContent: 'center',
          alignItems: 'center',
          backgroundColor: 'rgba(255, 255, 255, 0.9)',
          zIndex: 10,
          borderRadius: '4px'
        }}>
          <Spin size="large" tip="知识图谱加载中..." />
        </div>
      )}
    </div>
  </div>
);
};

export default KnowledgeGraph;
