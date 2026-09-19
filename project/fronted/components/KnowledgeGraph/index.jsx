// [file name]: index.jsx
// [file content begin]
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
let Network, DataSet;
if (typeof window !== 'undefined') {
  Network = require('vis-network').Network;
  DataSet = require('vis-data').DataSet;
}

const KnowledgeGraph = ({ 
  height = 500,
  onNodeClick,
  showControls = true,
  fullscreen = true,
  autoLoad = true  // 新增参数：是否自动加载数据
}) => {
  const graphRef = useRef(null);
  const [network, setNetwork] = useState(null);
  const [graphData, setGraphData] = useState({ nodes: null, edges: null });
  const [graphLoading, setGraphLoading] = useState(true);
  const [graphError, setGraphError] = useState(null);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [zoomLevel, setZoomLevel] = useState(1);
  const containerRef = useRef(null);

  // 初始化知识图谱
  const initGraph = () => {
    if (!graphRef.current || !Network || !DataSet) return;
    
    setGraphLoading(true);
    setGraphError(null);

    try {
      const nodes = new DataSet([]);
      const edges = new DataSet([]);
      
      const newNetwork = new Network(
        graphRef.current,
        { nodes, edges },
        {
          nodes: {
            shape: 'dot',
            size: 20,
            font: { 
              size: 12, 
              face: 'Tahoma',
              strokeWidth: 0,
              strokeColor: '#ffffff'
            },
            borderWidth: 2,
            color: {
              border: '#666666',
              background: '#1890ff',
              highlight: {
                border: '#1890ff',
                background: '#40a9ff'
              }
            }
          },
          edges: {
            width: 2,
            smooth: {
              type: 'continuous',
              roundness: 0.5
            },
            arrows: { 
              to: { 
                enabled: true, 
                scaleFactor: 0.8,
                type: 'arrow'
              } 
            },
            font: {
              size: 10,
              align: 'middle'
            },
            color: {
              color: '#999999',
              highlight: '#1890ff'
            }
          },
          physics: {
            enabled: true,
            stabilization: {
              enabled: true,
              iterations: 1000,
              updateInterval: 100
            },
            barnesHut: {
              gravitationalConstant: -2000,
              centralGravity: 0.3,
              springLength: 200,
              springConstant: 0.04,
              damping: 0.09,
              avoidOverlap: 1
            }
          },
          interaction: {
            hover: true,
            tooltipDelay: 200,
            hideEdgesOnDrag: true,
            hideNodesOnDrag: false
          }
        }
      );

      // 节点点击事件
      newNetwork.on('click', function(params) {
        if (params.nodes.length > 0) {
          const nodeId = params.nodes[0];
          const node = nodes.get(nodeId);
          if (node && onNodeClick) {
            onNodeClick(node);
            
            // 显示节点详情
            Modal.info({
              title: `节点详情: ${node.label || node.id}`,
              content: (
                <div>
                  <p><strong>节点类型:</strong> {getNodeTypeLabel(node.group) || '未知类型'}</p>
                  <p><strong>节点ID:</strong> {node.id}</p>
                  <p><strong>标签:</strong> {node.label || node.id}</p>
                  {node.value && <p><strong>关联度:</strong> {node.value}</p>}
                  {node.description && <p><strong>描述:</strong> {node.description}</p>}
                </div>
              ),
              okText: '关闭'
            });
          }
        }
      });

      // 缩放事件
      newNetwork.on('zoom', function(params) {
        setZoomLevel(params.scale);
      });

      setNetwork(newNetwork);
      setGraphData({ nodes, edges });
      setGraphLoading(false);

    } catch (error) {
      setGraphLoading(false);
      setGraphError(`初始化失败: ${error.message}`);
    }
  };

  // 获取所有图谱数据（从后端API）
  const fetchAllGraph = async () => {
    if (!graphData || !network) {
      setGraphError('图谱未初始化');
      return;
    }
    
    setGraphLoading(true);
    setGraphError(null);
    
    try {
      const result = await fetchAllGraphData();
      if (result.success && graphData) {
        graphData.nodes.clear();
        graphData.edges.clear();
        graphData.nodes.add(result.nodes || []);
        graphData.edges.add(result.edges || []);
        network.fit();
        message.success('图谱数据加载成功');
      } else {
        throw new Error(result.message || '获取图谱数据失败');
      }
    } catch (error) {
      setGraphError(`查询失败: ${error.message}`);
      message.error('加载图谱数据失败');
    } finally {
      setGraphLoading(false);
    }
  };

  // 根据关键词搜索图谱
  const searchGraph = async (keyword = "") => {
    if (!graphData || !network) {
      setGraphError('图谱未初始化');
      return;
    }
    
    setGraphLoading(true);
    setGraphError(null);
    
    try {
      // 这里可以调用后端搜索API
      // 暂时使用前端过滤
      const result = await fetchAllGraphData();
      if (result.success && result.nodes && result.edges) {
        if (keyword.trim() === "") {
          // 显示所有数据
          graphData.nodes.clear();
          graphData.edges.clear();
          graphData.nodes.add(result.nodes);
          graphData.edges.add(result.edges);
        } else {
          // 过滤节点
          const filteredNodes = result.nodes.filter(node => 
            node.label && node.label.toLowerCase().includes(keyword.toLowerCase())
          );
          const filteredNodeIds = filteredNodes.map(node => node.id);
          
          // 过滤边（只保留与过滤节点相关的边）
          const filteredEdges = result.edges.filter(edge =>
            filteredNodeIds.includes(edge.from) || filteredNodeIds.includes(edge.to)
          );

          graphData.nodes.clear();
          graphData.edges.clear();
          graphData.nodes.add(filteredNodes);
          graphData.edges.add(filteredEdges);
        }
        network.fit();
        message.success(`找到 ${graphData.nodes.length} 个节点`);
      } else {
        throw new Error('获取图谱数据失败');
      }
    } catch (error) {
      setGraphError(`搜索失败: ${error.message}`);
      message.error('搜索失败');
    } finally {
      setGraphLoading(false);
    }
  };

  // 获取节点类型标签
  const getNodeTypeLabel = (group) => {
    const typeMap = {
      'category': '装备类别',
      'test': '试验项目',
      'indicator': '考核指标',
      'method': '试验方法',
      'standard': '标准规范',
      'equipment': '装备',
      'document': '文档',
      'requirement': '需求'
    };
    return typeMap[group] || group;
  };

  // 处理搜索
  const handleSearch = (value) => {
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
      network.moveTo({
        scale: zoomLevel * 1.2,
        animation: true
      });
    }
  };

  const handleZoomOut = () => {
    if (network) {
      network.moveTo({
        scale: zoomLevel * 0.8,
        animation: true
      });
    }
  };

  // 处理全屏
  const handleFullscreen = () => {
    if (!containerRef.current) return;

    if (!isFullscreen) {
      if (containerRef.current.requestFullscreen) {
        containerRef.current.requestFullscreen();
      } else if (containerRef.current.webkitRequestFullscreen) {
        containerRef.current.webkitRequestFullscreen();
      } else if (containerRef.current.msRequestFullscreen) {
        containerRef.current.msRequestFullscreen();
      }
    } else {
      if (document.exitFullscreen) {
        document.exitFullscreen();
      } else if (document.webkitExitFullscreen) {
        document.webkitExitFullscreen();
      } else if (document.msExitFullscreen) {
        document.msExitFullscreen();
      }
    }
  };

  // 导出图谱
  const handleExport = () => {
    if (network) {
      const data = network.getPositions();
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'knowledge-graph-export.json';
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
      message.success('图谱已导出');
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

  // 初始化图表
  useEffect(() => {
    if (autoLoad) {
      initGraph();
      // 延迟加载数据，确保网络已初始化
      setTimeout(() => {
        fetchAllGraph();
      }, 100);
    }

    return () => {
      if (network) {
        network.destroy();
      }
    };
  }, [autoLoad]);

  if (graphError) {
    return (
      <div 
        ref={containerRef}
        className={`${styles.knowledgeGraphContainer} ${isFullscreen ? styles.fullscreen : ''}`}
      >
        <div className={styles.errorContainer}>
          <Text type="danger">{graphError}</Text>
          <Button 
            type="primary" 
            onClick={() => {
              initGraph();
              setTimeout(() => fetchAllGraph(), 100);
            }}
            style={{ marginTop: 16 }}
          >
            重新加载
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div 
      ref={containerRef}
      className={`${styles.knowledgeGraphContainer} ${isFullscreen ? styles.fullscreen : ''}`}
    >
      {showControls && (
        <div className={styles.controls}>
          <div className={styles.controlGroup}>
            <Input.Search
              placeholder="搜索节点..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              onSearch={handleSearch}
              prefix={<SearchOutlined />}
              style={{ width: 250 }}
              allowClear
            />
            <Select defaultValue="force" style={{ width: 140 }}>
              <Option value="force">力导向布局</Option>
              <Option value="hierarchical">层次布局</Option>
              <Option value="circular">环形布局</Option>
            </Select>
          </div>
          
          <div className={styles.controlGroup}>
            <Tooltip title="放大">
              <Button icon={<ZoomInOutlined />} onClick={handleZoomIn} />
            </Tooltip>
            <span className={styles.zoomLevel}>{Math.round(zoomLevel * 100)}%</span>
            <Tooltip title="缩小">
              <Button icon={<ZoomOutOutlined />} onClick={handleZoomOut} />
            </Tooltip>
            <Tooltip title="刷新">
              <Button icon={<RedoOutlined />} onClick={handleRefresh} />
            </Tooltip>
            <Tooltip title="设置">
              <Button icon={<SettingOutlined />} />
            </Tooltip>
            <Tooltip title="导出">
              <Button icon={<DownloadOutlined />} onClick={handleExport} />
            </Tooltip>
            {fullscreen && (
              <Tooltip title={isFullscreen ? "退出全屏" : "全屏查看"}>
                <Button 
                  icon={isFullscreen ? <FullscreenExitOutlined /> : <FullscreenOutlined />} 
                  onClick={handleFullscreen}
                />
              </Tooltip>
            )}
          </div>
        </div>
      )}

      <div className={styles.graphWrapper}>
        <div
          ref={graphRef}
          className={styles.graphArea}
          style={{ height }}
        />
        
        {graphLoading && (
          <div className={styles.loadingOverlay}>
            <Spin size="large" tip="知识图谱加载中..." />
          </div>
        )}
      </div>

      {/* 图例 */}
      <div className={styles.legend}>
        <Title level={5} className={styles.legendTitle}>图例</Title>
        <div className={styles.legendItems}>
          <div className={styles.legendItem}>
            <span className={styles.legendDot} style={{ backgroundColor: '#1890ff' }}></span>
            <span>装备类别</span>
          </div>
          <div className={styles.legendItem}>
            <span className={styles.legendDot} style={{ backgroundColor: '#52c41a' }}></span>
            <span>试验项目</span>
          </div>
          <div className={styles.legendItem}>
            <span className={styles.legendDot} style={{ backgroundColor: '#faad14' }}></span>
            <span>考核指标</span>
          </div>
          <div className={styles.legendItem}>
            <span className={styles.legendDot} style={{ backgroundColor: '#13c2c2' }}></span>
            <span>试验方法</span>
          </div>
          <div className={styles.legendItem}>
            <span className={styles.legendDot} style={{ backgroundColor: '#722ed1' }}></span>
            <span>标准规范</span>
          </div>
          <div className={styles.legendItem}>
            <span className={styles.legendDot} style={{ backgroundColor: '#f5222d' }}></span>
            <span>文档</span>
          </div>
        </div>
      </div>
    </div>
  );
};

export default KnowledgeGraph;
// [file content end]