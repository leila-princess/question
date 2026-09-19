"use client";

import React, { useState, useEffect } from 'react';
import { Card, Row, Col, Statistic, Spin, message, Radio } from 'antd';
import {
  DatabaseOutlined, FileOutlined, NodeIndexOutlined,
  ExperimentOutlined, ApiOutlined, BarChartOutlined,
} from '@ant-design/icons';
import { Column, Line } from '@ant-design/charts';

const NODE_TYPE_ORDER = ['EquipmentType', 'Equipment', 'Environment', 'TestProject', 'Indicator', 'TestMethod'];
const EDGE_TYPE_ORDER = ['HAS', 'CON', 'BELONGS_TO'];

const nodeColors: Record<string, string> = {
  EquipmentType: '#1890ff', Equipment: '#52c41a', Environment: '#722ed1',
  TestProject: '#fa8c16', Indicator: '#13c2c2', TestMethod: '#faad14',
};
const nodeLabels: Record<string, string> = {
  EquipmentType: '设备类型', Equipment: '具体设备', Environment: '环境',
  TestProject: '测试项目', Indicator: '指标', TestMethod: '试验方法',
};
const edgeLabels: Record<string, string> = {
  HAS: '包含', CON: '关联测试', BELONGS_TO: '属于',
};
const edgeColors: Record<string, string> = {
  HAS: '#52c41a', CON: '#13c2c2', BELONGS_TO: '#1890ff',
};

const KB_FILE_TYPE_ORDER = [
  "法规标准", "规范性文件", "指导性文件", "试验方案", "试验大纲", "试验报告", "专项评估报告",
] as const;
const KB_TYPE_COLORS: Record<string, string> = {
  法规标准: "#1d4ed8", 规范性文件: "#7c3aed", 指导性文件: "#0d9488",
  试验方案: "#059669", 试验大纲: "#d97706", 试验报告: "#dc2626", 专项评估报告: "#9333ea",
};

function buildNodeChartData(nodes: any[]) {
  const counts: Record<string, number> = {};
  NODE_TYPE_ORDER.forEach(t => { counts[t] = 0; });
  nodes.forEach(n => {
    const t = n.originalType || n.type;
    if (NODE_TYPE_ORDER.includes(t)) counts[t] += n.count || 0;
  });
  return NODE_TYPE_ORDER.map(t => ({ type: nodeLabels[t], count: counts[t], color: nodeColors[t] }));
}

function buildEdgeChartData(edges: any[]) {
  const counts: Record<string, number> = {};
  EDGE_TYPE_ORDER.forEach(t => { counts[t] = 0; });
  edges.forEach(e => {
    const t = e.originalType || e.type;
    if (EDGE_TYPE_ORDER.includes(t)) counts[t] += e.count || 0;
  });
  return EDGE_TYPE_ORDER.map(t => ({ type: edgeLabels[t], count: counts[t], color: edgeColors[t] }));
}

function buildFileChartData(fileStats: any[]) {
  const list = Array.isArray(fileStats) ? fileStats : [];
  return KB_FILE_TYPE_ORDER.map(type => {
    const row = list.find((x: any) => x?.originalCategory === type);
    return { type, count: row?.count ?? 0, color: KB_TYPE_COLORS[type] || "#3b82f6" };
  });
}

function formatSize(bytes: number): string {
  if (bytes === 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
}

const cardStyle = {
  borderRadius: '8px',
  boxShadow: '0 1px 2px -2px rgba(0,0,0,0.16), 0 3px 6px 0 rgba(0,0,0,0.12), 0 5px 12px 4px rgba(0,0,0,0.09)',
};

const chartCardStyle = {
  ...cardStyle,
  height: '100%',
};

export default function DashboardPage() {
  const [loading, setLoading] = useState(true);
  const [overview, setOverview] = useState({ neo4jNodes: 0, neo4jRelations: 0, files: 0, chromaVectors: 0, mysqlRows: 0, minioSize: 0, methods: 0 });
  const [nodeData, setNodeData] = useState<any[]>([]);
  const [edgeData, setEdgeData] = useState<any[]>([]);
  const [fileData, setFileData] = useState<any[]>([]);
  const [trendData, setTrendData] = useState<any[]>([]);
  const [graphChartType, setGraphChartType] = useState<'node' | 'edge'>('node');

  useEffect(() => { fetchData(); }, []);

  const fetchData = async () => {
    setLoading(true);
    try {
      const safe = (url: string, fallback: any) =>
        fetch(url).then(r => r.ok ? r.json() : fallback).catch(() => fallback);

      const [overviewRes, neo4jRes, fileRes, relationRes, trendRes] = await Promise.all([
        safe('/api/kshzs/overview', {}),
        safe('/api/kshzs/neo4j-stats', []),
        safe('/api/kshzs/file-stats', []),
        safe('/api/kshzs/relation-stats', []),
        safe('/api/kshzs/file-trend', []),
      ]);

      if (overviewRes && typeof overviewRes === 'object') {
        setOverview({
          neo4jNodes: overviewRes.neo4jNodes || 0,
          neo4jRelations: overviewRes.neo4jRelations || 0,
          files: overviewRes.files || 0,
          chromaVectors: overviewRes.chromaVectors || 0,
          mysqlRows: overviewRes.mysqlRows || 0,
          minioSize: overviewRes.minioSize || 0,
          methods: overviewRes.methods || 0,
        });
      }

      const neo4jArr = Array.isArray(neo4jRes) ? neo4jRes : [];
      const fileArr = Array.isArray(fileRes) ? fileRes : [];
      const relArr = Array.isArray(relationRes) ? relationRes : [];
      const trendArr = Array.isArray(trendRes) ? trendRes : [];

      setNodeData(buildNodeChartData(neo4jArr));
      setEdgeData(buildEdgeChartData(relArr));
      setFileData(buildFileChartData(fileArr));
      setTrendData(trendArr.map((t: any) => ({ month: t.month || '', count: t.count || 0 })));
    } catch (error) {
      console.error("Error fetching dashboard data:", error);
      message.error("获取数据失败，请稍后重试");
    } finally {
      setLoading(false);
    }
  };

  const columnLabelStyle = { fontSize: 12, fontWeight: 600, fill: '#1e40af' };
  const axisStyle = { x: { title: { text: '', style: { fontSize: 12 } }, label: { style: { fontSize: 10 }, autoRotate: true, autoHide: true } }, y: { title: { text: '', style: { fontSize: 12 } }, label: { style: { fontSize: 11 } } } };

  return (
    <div style={{ height: '100%', minHeight: 0, overflow: 'hidden', boxSizing: 'border-box', padding: '24px', backgroundColor: '#f0f2f5', display: 'flex', flexDirection: 'column' }}>
      <div style={{ flex: 1, minHeight: 0, overflowY: 'auto', overflowX: 'hidden' }}>
        <Spin spinning={loading}>
          <Row gutter={[16, 16]} style={{ marginBottom: '24px' }}>
            <Col xs={24} sm={12} md={6}>
              <Card bordered={false} style={cardStyle}>
                <Statistic title="文件总数" value={overview.files} prefix={<FileOutlined style={{ color: '#52c41a' }} />} valueStyle={{ color: '#52c41a', fontWeight: 'bold' }} />
              </Card>
            </Col>
            <Col xs={24} sm={12} md={6}>
              <Card bordered={false} style={cardStyle}>
                <Statistic title="图谱节点" value={overview.neo4jNodes} prefix={<NodeIndexOutlined style={{ color: '#1890ff' }} />} valueStyle={{ color: '#1890ff', fontWeight: 'bold' }} />
              </Card>
            </Col>
            <Col xs={24} sm={12} md={6}>
              <Card bordered={false} style={cardStyle}>
                <Statistic title="向量数据" value={overview.chromaVectors} prefix={<DatabaseOutlined style={{ color: '#722ed1' }} />} valueStyle={{ color: '#722ed1', fontWeight: 'bold' }} />
              </Card>
            </Col>
            <Col xs={24} sm={12} md={6}>
              <Card bordered={false} style={cardStyle}>
                <Statistic title="试验方法" value={overview.methods} prefix={<ExperimentOutlined style={{ color: '#fa8c16' }} />} valueStyle={{ color: '#fa8c16', fontWeight: 'bold' }} />
              </Card>
            </Col>
          </Row>

          <Row gutter={[16, 16]} style={{ marginBottom: '16px' }}>
            <Col xs={24} lg={12}>
              <Card title={<span><BarChartOutlined style={{ color: '#3b82f6', marginRight: 8 }} />文件分类分布</span>} bordered={false} style={chartCardStyle}>
                <div style={{ height: '350px' }}>
                  {fileData.some(d => d.count > 0) ? (
                    <Column data={fileData} xField="type" yField="count" colorField="color"
                      style={{ fillOpacity: 0.8, radiusTopLeft: 4, radiusTopRight: 4 }}
                      axis={axisStyle} legend={false}
                      tooltip={{ channel: 'y', valueFormatter: '.0f' }}
                      scale={{ y: { nice: true } }}
                      label={{ position: 'top', text: (item: any) => `${item.count}`, style: columnLabelStyle }}
                    />
                  ) : (
                    <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100%', color: '#999' }}>暂无数据</div>
                  )}
                </div>
              </Card>
            </Col>
            <Col xs={24} lg={12}>
              <Card
                title={<span><ApiOutlined style={{ color: '#1890ff', marginRight: 8 }} />图谱关系统计</span>}
                bordered={false} style={chartCardStyle}
                extra={
                  <Radio.Group value={graphChartType} onChange={(e) => setGraphChartType(e.target.value)} optionType="button" buttonStyle="solid" size="small"
                    options={[{ label: '节点', value: 'node' }, { label: '关系', value: 'edge' }]}
                  />
                }
              >
                <div style={{ height: '350px' }}>
                  {(graphChartType === 'node' ? nodeData : edgeData).some(d => d.count > 0) ? (
                    <Column
                      data={graphChartType === 'node' ? nodeData : edgeData}
                      xField="type" yField="count" colorField="color"
                      style={{ fillOpacity: 0.8, radiusTopLeft: 4, radiusTopRight: 4 }}
                      axis={axisStyle} legend={false}
                      tooltip={{ channel: 'y', valueFormatter: '.0f' }}
                      scale={{ y: { nice: true } }}
                      label={{ position: 'top', text: (item: any) => `${item.count}`, style: columnLabelStyle }}
                    />
                  ) : (
                    <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100%', color: '#999' }}>暂无数据</div>
                  )}
                </div>
              </Card>
            </Col>
          </Row>

          <Row gutter={[16, 16]} style={{ marginBottom: '16px' }}>
            <Col xs={24} lg={12}>
              <Card title={<span><DatabaseOutlined style={{ color: '#722ed1', marginRight: 8 }} />数据库存储概览</span>} bordered={false} style={chartCardStyle}>
                <div style={{ height: '350px' }}>
                  <Column
                    data={[
                      { type: 'Neo4j 节点', count: overview.neo4jNodes, color: '#1890ff' },
                      { type: 'Neo4j 关系', count: overview.neo4jRelations, color: '#52c41a' },
                      { type: 'Chroma 向量', count: overview.chromaVectors, color: '#722ed1' },
                      { type: 'MySQL 行数', count: overview.mysqlRows, color: '#fa8c16' },
                    ]}
                    xField="type" yField="count" colorField="color"
                    style={{ fillOpacity: 0.8, radiusTopLeft: 4, radiusTopRight: 4 }}
                    axis={axisStyle} legend={false}
                    tooltip={{ channel: 'y', valueFormatter: '.0f' }}
                    scale={{ y: { nice: true } }}
                    label={{ position: 'top', text: (item: any) => `${item.count}`, style: columnLabelStyle }}
                  />
                </div>
                <div style={{ textAlign: 'center', marginTop: 8, color: '#666', fontSize: 13 }}>
                  MinIO 存储: {formatSize(overview.minioSize)}
                </div>
              </Card>
            </Col>
            <Col xs={24} lg={12}>
              <Card title={<span><BarChartOutlined style={{ color: '#13c2c2', marginRight: 8 }} />上传趋势</span>} bordered={false} style={chartCardStyle}>
                <div style={{ height: '350px' }}>
                  {trendData.length > 0 ? (
                    <Line data={trendData} xField="month" yField="count"
                      point={{ size: 3, shape: 'circle' }}
                      axis={{ x: { title: { text: '月份', style: { fontSize: 12 } } }, y: { title: { text: '文件数', style: { fontSize: 12 } } } }}
                      tooltip={{ channel: 'y', valueFormatter: '.0f' }}
                      scale={{ y: { nice: true } }}
                    />
                  ) : (
                    <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100%', color: '#999' }}>暂无数据</div>
                  )}
                </div>
              </Card>
            </Col>
          </Row>
        </Spin>
      </div>
    </div>
  );
}
