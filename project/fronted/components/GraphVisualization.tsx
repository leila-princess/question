// src/components/GraphVisualization.tsx
import React, { useState, useEffect, useRef, useCallback } from 'react';
// @ts-ignore - vis-network 没有类型声明
import { Network, DataSet } from 'vis-network';
import { Spin, message } from 'antd';
import { GraphData, Node, Relationship } from '../app/types/graph';
import 'vis-network/styles/vis-network.min.css';

interface Props {
  graphData: GraphData;
  loading: boolean;
  onLoad?: () => void;
  onError?: (error: string) => void;
}

// 定义节点颜色映射类型
type NodeColorMap = {
  [key in 'Standard' | 'Regulation' | 'Guideline' | 'Product' | 'Organization' | 'Person']: string;
};

const GraphVisualization: React.FC<Props> = ({ graphData, loading, onLoad, onError }) => {
  const visRef = useRef<HTMLDivElement>(null);
  const [network, setNetwork] = useState<Network | null>(null);
  const [error, setError] = useState<string | null>(null);

  // 节点颜色映射
  const nodeColors: NodeColorMap = {
    'Standard': '#1890ff',
    'Regulation': '#52c41a',
    'Guideline': '#faad14',
    'Product': '#722ed1',
    'Organization': '#f5222d',
    'Person': '#eb8f00',
  };

  // 转换为vis.js数据格式
  const convertToVisData = useCallback((data: GraphData) => {
    const nodes = new DataSet(
      data.nodes.map(node => ({
        id: node.id,
        label: node.properties.name || node.id,
        title: JSON.stringify(node.properties, null, 2),
        shape: 'dot',
        size: 25,
        color: getNodeColor(node.labels[0])
      }))
    );
    
    const edges = new DataSet(
      data.relationships.map(rel => ({
        id: rel.id,
        from: rel.startNodeId,
        to: rel.endNodeId,
        label: rel.type,
        title: JSON.stringify(rel.properties, null, 2),
        width: 1,
        color: { inherit: 'from' }
      }))
    );
    
    return { nodes, edges };
  }, []);

  // 根据节点类型获取颜色 - 使用类型守卫
  const getNodeColor = (label?: string): string => {
    if (!label) return '#8c8c8c';
    
    // 检查label是否是有效的节点类型
    if (isValidNodeType(label)) {
      return nodeColors[label];
    }
    
    return '#8c8c8c';
  };

  // 类型守卫函数
  const isValidNodeType = (label: string): label is keyof NodeColorMap => {
    return ['Standard', 'Regulation', 'Guideline', 'Product', 'Organization', 'Person'].includes(label);
  };

  // 图谱配置
  const getVisOptions = useCallback(() => ({
    nodes: {
      shape: 'dot',
      size: 25,
      font: { size: 12, color: '#ffffff' },
      scaling: { min: 15, max: 30 }
    },
    edges: {
      width: 1,
      color: { inherit: 'from' },
      smooth: {
        enabled: true,
        type: 'continuous',
        forceDirection: 'none',
        roundness: 0.5
      }
    },
    interaction: {
      tooltipDelay: 200,
      hideEdgesOnDrag: true
    },
    physics: {
      enabled: true,
      stabilization: {
        enabled: true,
        iterations: 1000
      }
    }
  }), []);

  // 初始化图谱
  useEffect(() => {
    if (visRef.current && !network && graphData.nodes.length > 0) {
      try {
        const visData = convertToVisData(graphData);
        const options = getVisOptions();
        
        const net = new Network(visRef.current, visData, options);
        setNetwork(net);
        
        // 添加点击事件
        net.on('click', (params: { nodes: any; edges: any; }) => {
          console.log('Clicked on nodes:', params.nodes);
          console.log('Clicked on edges:', params.edges);
        });
        
        onLoad?.();
      } catch (err) {
        const errorMessage = err instanceof Error ? err.message : '图谱初始化失败';
        setError(errorMessage);
        onError?.(errorMessage);
      }
    }
  }, [visRef, network, graphData, convertToVisData, getVisOptions, onLoad, onError]);

  // 更新图谱数据
  useEffect(() => {
    if (network && graphData.nodes.length > 0) {
      try {
        const visData = convertToVisData(graphData);
        network.setData(visData);
        onLoad?.();
      } catch (err) {
        const errorMessage = err instanceof Error ? err.message : '图谱更新失败';
        setError(errorMessage);
        onError?.(errorMessage);
      }
    }
  }, [network, graphData, convertToVisData, onLoad, onError]);

  // 组件卸载时清理资源
  useEffect(() => {
    return () => {
      if (network) {
        network.destroy();
      }
    };
  }, [network]);

  return (
    <div className="graph-container">
      {loading && (
        <div className="loading-overlay">
          <Spin size="large" />
        </div>
      )}
      {error && <div className="error-message">{error}</div>}
      <div ref={visRef} id="graph-container" style={{ width: '100%', height: '600px' }} />
    </div>
  );
};

export default GraphVisualization;