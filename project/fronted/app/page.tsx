"use client";
import { Card, Col, Row, Typography, Button, Space, Progress, Tag, List, Badge, Statistic, Table } from "antd";
import { useRouter } from "next/navigation";
import { 
  SearchOutlined, MessageOutlined, ExperimentOutlined, DatabaseOutlined, 
  RocketOutlined, ThunderboltOutlined, CloudOutlined, 
  SafetyCertificateOutlined, ClockCircleOutlined, FileTextOutlined,
  UserOutlined, TeamOutlined, BulbOutlined, AppstoreOutlined,
  RightOutlined, CalendarOutlined, BellOutlined, BarChartOutlined,
  HistoryOutlined, CheckCircleOutlined, WarningOutlined, QuestionCircleOutlined
} from "@ant-design/icons";
import { useState, useEffect } from "react";
import GlobalNav from "@/components/GlobalNav";
import { Column } from "@ant-design/charts";

const { Title, Text, Paragraph } = Typography;

/** 与 backend/blueprints/SY/routes.py 中 TYPE_ORDER 一致，用于知识库柱状图顺序与补全缺项 */
const KB_FILE_TYPE_ORDER = [
  "法规标准",
  "规范性文件",
  "指导性文件",
  "试验方案",
  "试验大纲",
  "试验报告",
  "专项评估报告",
] as const;

const KB_TYPE_DEFAULT_COLOR: Record<string, string> = {
  法规标准: "#1d4ed8",
  规范性文件: "#7c3aed",
  指导性文件: "#0d9488",
  试验方案: "#059669",
  试验大纲: "#d97706",
  试验报告: "#dc2626",
  专项评估报告: "#9333ea",
};

function buildOrderedFileChartData(fileStats: any[]) {
  const list = Array.isArray(fileStats) ? fileStats : [];
  return KB_FILE_TYPE_ORDER.map((type) => {
    const row = list.find((x: any) => x?.type === type);
    return {
      type,
      count: row?.count ?? 0,
      color: row?.color || KB_TYPE_DEFAULT_COLOR[type] || "#3b82f6",
    };
  });
}

export default function HomePage() {
  const router = useRouter();
  const [stats, setStats] = useState({
    fileStats: [],
    recentSchemes: { jygz: [], jyllm: [] },
    mysqlStats: { tableCount: 0, rowCount: 0 },
    problems: []
  });
  const [loading, setLoading] = useState(false);

  const fetchStats = async () => {
    try {
      setLoading(true);
      
      const fileRes = await fetch('/api/sy/file_stats');
      const fileData = fileRes.ok ? await fileRes.json() : { data: [] };
      
      const schemesRes = await fetch('/api/sy/recent_schemes?limit=5');
      const schemesData = schemesRes.ok ? await schemesRes.json() : { data: { jygz: [], jyllm: [] } };
      
      const mysqlRes = await fetch('/api/xtgl/sjkgl/db/mysql/tables');
      const mysqlData = mysqlRes.ok ? await mysqlRes.json() : { data: [] };
      
      const tableCount = mysqlData.data?.length || 0;
      const rowCount = mysqlData.data?.reduce((sum: number, table: any) => sum + (table.row_count || 0), 0) || 0;
      
      const problemsRes = await fetch('/api/sy/problems?limit=5');
      const problemsData = problemsRes.ok ? await problemsRes.json() : { data: [] };
      
      setStats({
        fileStats: fileData.data || [],
        recentSchemes: schemesData.data || { jygz: [], jyllm: [] },
        mysqlStats: { tableCount, rowCount },
        problems: problemsData.data || []
      });
    } catch (error) {
      console.error('获取统计数据失败', error);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchStats();
  }, []);

  const features = [
    { 
      title: "关联查询", 
      description: "基于知识图谱的标准规范关联查询", 
      icon: <SearchOutlined style={{ fontSize: 24, color: "#3b82f6" }} />,
      path: "/GLCX/JQCX",
      color: "#3b82f6",
      gradient: "linear-gradient(135deg, #3b82f6 0%, #1d4ed8 100%)",
      heightPercent: 100
    },
    { 
      title: "智能问答", 
      description: "基于大模型的知识问答系统", 
      icon: <MessageOutlined style={{ fontSize: 24, color: "#8b5cf6" }} />,
      path: "/GLCX/ZNWD",
      color: "#8b5cf6",
      gradient: "linear-gradient(135deg, #8b5cf6 0%, #7c3aed 100%)",
      heightPercent: 100
    },
    { 
      title: "基于规则的辅助设计", 
      description: "基于规则的试验方案生成", 
      icon: <ExperimentOutlined style={{ fontSize: 24, color: "#10b981" }} />,
      path: "/FZSJ/JYGZ",
      color: "#10b981",
      gradient: "linear-gradient(135deg, #10b981 0%, #059669 100%)",
      // tag: "基于规则",
      tagColor: "green",
      heightPercent: 100
    },
    { 
      title: "基于大模型的辅助设计", 
      description: "基于大模型的智能方案生成", 
      icon: <RocketOutlined style={{ fontSize: 24, color: "#f59e0b" }} />,
      path: "/FZSJ/JYLLM",
      color: "#f59e0b",
      gradient: "linear-gradient(135deg, #f59e0b 0%, #d97706 100%)",
      // tag: "基于LLM",
      tagColor: "blue",
      heightPercent: 100
    },
  ];

  const systemFeatures = [
    { 
      title: "数据库管理", 
      description: "管理系统内数据库", 
      icon: <DatabaseOutlined style={{ fontSize: 20, color: "#3b82f6" }} />,
      path: "/XTGL/SJKGL",
      color: "#3b82f6"
    },
    { 
      title: "模型管理", 
      description: "管理AI模型配置和本地模型", 
      icon: <SafetyCertificateOutlined style={{ fontSize: 20, color: "#8b5cf6" }} />,
      path: "/XTGL/MXGL",
      color: "#8b5cf6"
    },
    { 
      title: "知识管理", 
      description: "管理文件、标签、图谱", 
      icon: <BulbOutlined style={{ fontSize: 20, color: "#10b981" }} />,
      path: "/ZSGL/WJGL",
      color: "#10b981"
    },
    { 
      title: "分类管理", 
      description: "管理标准规范分类", 
      icon: <AppstoreOutlined style={{ fontSize: 20, color: "#f59e0b" }} />,
      path: "/FLGL",
      color: "#f59e0b"
    },
  ];

  return (
    <div style={{
      display: "flex",
      minHeight: "100vh",
      background: "linear-gradient(135deg, #f0f7ff 0%, #e6f0ff 100%)",
      position: "relative",
      overflow: "hidden"
    }}>
      <div style={{
        position: "absolute",
        top: "-50%",
        right: "-50%",
        width: "100%",
        height: "200%",
        background: "radial-gradient(circle, rgba(173, 216, 230, 0.2) 1px, transparent 1px)",
        backgroundSize: "50px 50px",
        transform: "rotate(30deg)",
        opacity: 0.2,
        zIndex: 0
      }} />
      
      <div style={{
        flex: 1,
        padding: "24px",
        position: "relative",
        zIndex: 5,
        display: "flex",
        justifyContent: "center",
        alignItems: "flex-start"
      }}>
        <div style={{ 
          display: "flex", 
          flexDirection: "column", 
          gap: 32,
          maxWidth: 1300,
          width: "100%",
          textAlign: "center"
        }}>
          
          <div>
            <Row gutter={[24, 24]}>
              <Col xs={24} lg={12}>
                <Card 
                  style={{
                    borderRadius: 20,
                    boxShadow: "0 4px 20px rgba(0, 0, 0, 0.06)",
                    border: "none",
                    height: 300
                  }}
                  title={
                    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                      <BarChartOutlined style={{ color: "#3b82f6" }} />
                      <span style={{ color: "#1e40af", fontWeight: 600 }}>知识库统计</span>
                    </div>
                  }
                >
                  <div style={{ height: 200, textAlign: "left" }}>
                    <Column
                      data={buildOrderedFileChartData(stats.fileStats)}
                      xField="type"
                      yField="count"
                      colorField="color"
                      style={{
                        fillOpacity: 0.8,
                        radiusTopLeft: 4,
                        radiusTopRight: 4
                      }}
                      axis={{
                        x: {
                          title: { text: '文件类型', style: { fontSize: 12 } },
                          label: { style: { fontSize: 10 }, autoRotate: true, autoHide: true }
                        },
                        y: {
                          title: { text: '文件数量', style: { fontSize: 12 } },
                          label: { style: { fontSize: 11 } }
                        }
                      }}
                      legend={false}
                      tooltip={{ channel: 'y', valueFormatter: '.0f' }}
                      scale={{ y: { nice: true } }}
                      label={{
                        position: 'top',
                        text: (item: any) => `${item.count}`,
                        style: {
                          fontSize: 12,
                          fontWeight: 600,
                          fill: '#1e40af'
                        }
                      }}
                    />
                  </div>
                </Card>
              </Col>
              <Col xs={24} lg={12}>
                <Card 
                  style={{
                    borderRadius: 20,
                    boxShadow: "0 4px 20px rgba(0, 0, 0, 0.06)",
                    border: "none",
                    height: 300
                  }}
                  title={
                    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                      <HistoryOutlined style={{ color: "#10b981" }} />
                      <span style={{ color: "#1e40af", fontWeight: 600 }}>最近方案</span>
                    </div>
                  }
                  extra={
                    <Button type="link" onClick={() => router.push('/FZSJ/JYGZ')} style={{ color: "#3b82f6", fontWeight: 600 }}>
                      查看更多 <RightOutlined />
                    </Button>
                  }
                >
                  <div style={{ height: 220, overflow: "auto", textAlign: "left" }}>
                    <List
                      dataSource={[
                        ...(stats.recentSchemes.jygz || []).map((item: any) => ({ ...item, type: 'jygz' })),
                        ...(stats.recentSchemes.jyllm || []).map((item: any) => ({ ...item, type: 'jyllm' }))
                      ].sort((a: any, b: any) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime()).slice(0, 5)}
                      renderItem={(item: any) => {
                        const done = item.status === "completed" || item.status === "complete";
                        return (
                        <List.Item
                          key={item.task_id}
                          style={{ border: 'none', padding: '8px 0' }}
                          actions={[
                            <Button 
                              type="link" 
                              size="small"
                              onClick={(e) => {
                                e.stopPropagation();
                                if (item.type === 'jygz') {
                                  router.push(`/FZSJ/JYGZ?task_id=${item.task_id}&load_history=true`);
                                } else {
                                  router.push(`/FZSJ/JYLLM?task_id=${item.task_id}&load_history=true`);
                                }
                              }}
                              style={{ color: "#3b82f6", fontWeight: 500 }}
                            >
                              {done ? '查看方案' : '继续编辑'}
                            </Button>
                          ]}
                        >
                          <div style={{ width: "100%", minWidth: 0 }}>
                            <Space size={8} wrap align="center" style={{ width: "100%" }}>
                              <Text strong style={{ color: "#1f2937" }} ellipsis={{ tooltip: item.name }}>
                                {item.name}
                              </Text>
                              {item.description ? (
                                <Text type="secondary" style={{ fontSize: 12 }} ellipsis={{ tooltip: item.description }}>
                                  {item.description}
                                </Text>
                              ) : null}
                              <Tag color={item.type === 'jygz' ? 'green' : 'blue'}>
                                {item.type === 'jygz' ? '基于规则' : '基于LLM'}
                              </Tag>
                              <Tag color={done ? "green" : "orange"}>
                                {done ? "已完成" : "进行中"}
                              </Tag>
                              <Text type="secondary" style={{ fontSize: 12, whiteSpace: "nowrap" }}>
                                {item.updated_at}
                              </Text>
                            </Space>
                          </div>
                        </List.Item>
                        );
                      }}
                      locale={{ emptyText: "暂无方案" }}
                    />
                  </div>
                </Card>
              </Col>
            </Row>
          </div>
          
          <div>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 24 }}>
              <Title level={3} style={{ color: "#1e40af", margin: 0 }}>
                核心功能
              </Title>
            </div>
            <Row gutter={[24, 24]}>
              {features.map((feature, index) => (
                <Col key={index} xs={24} sm={12} lg={6}>
                  <Card 
                    hoverable
                    style={{ 
                      borderRadius: 20,
                      boxShadow: "0 4px 20px rgba(0, 0, 0, 0.06)",
                      border: "none",
                      height: "100%",
                      transition: "all 0.3s",
                      overflow: "hidden"
                    }}
                    styles={{ 
                      body: {
                        padding: 0,
                        height: "100%"
                      }
                    }}
                    onClick={() => router.push(feature.path)}
                  >
                    <div style={{ 
                      padding: 20, 
                      textAlign: "center",
                      background: feature.gradient,
                      color: "#fff",
                      display: "flex",
                      flexDirection: "column",
                      justifyContent: "center",
                      alignItems: "center"
                    }}>
                      <Title level={4} style={{ color: "#fff", marginBottom: 8 }}>
                        {feature.title}
                      </Title>
                      <Paragraph style={{ color: "rgba(255, 255, 255, 0.9)", marginBottom: 0, fontSize: 12 }}>
                        {feature.description}
                      </Paragraph>
                    </div>
                  </Card>
                </Col>
              ))}
            </Row>
          </div>
          
          <div>
            <Row gutter={[16, 16]}>
              {systemFeatures.map((feature, index) => (
                <Col key={index} xs={24} sm={12} lg={6}>
                  <Card 
                    hoverable
                    style={{ 
                      borderRadius: 16,
                      boxShadow: "0 2px 12px rgba(0, 0, 0, 0.04)",
                      border: "1px solid #e5e7eb"
                    }}
                    onClick={() => router.push(feature.path)}
                  >
                    <div style={{ display: "flex", alignItems: "center", gap: 16, justifyContent: "center", textAlign: "center" }}>
                      <div style={{ 
                        width: 48, 
                        height: 48, 
                        background: `${feature.color}15`, 
                        borderRadius: 12,
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "center"
                      }}>
                        {feature.icon}
                      </div>
                      <div>
                        <Title level={5} style={{ margin: 0 }}>{feature.title}</Title>
                        <Text type="secondary" style={{ fontSize: 12 }}>{feature.description}</Text>
                      </div>
                    </div>
                  </Card>
                </Col>
              ))}
            </Row>
          </div>
          
          {/* <div>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 24 }}>
              <Title level={3} style={{ color: "#1e40af", margin: 0 }}>
                问题管理
              </Title>
              <Button type="link" onClick={() => router.push('/ZSGL/WJGL')} style={{ color: "#3b82f6", fontWeight: 600 }}>
                查看详情 <RightOutlined />
              </Button>
            </div>
            <Card 
              style={{
                borderRadius: 20,
                boxShadow: "0 4px 20px rgba(0, 0, 0, 0.06)",
                border: "none"
              }}
            >
              <Table
                dataSource={stats.problems}
                columns={[
                  {
                    title: '问题标题',
                    dataIndex: 'title',
                    key: 'title',
                    ellipsis: true,
                    width: 200,
                  },
                  {
                    title: '问题内容',
                    dataIndex: 'content',
                    key: 'content',
                    ellipsis: true,
                    width: 250,
                  },
                  {
                    title: '回复内容',
                    dataIndex: 'reply_content',
                    key: 'reply_content',
                    ellipsis: true,
                    width: 200,
                    render: (text: string) => text || '未回复',
                  },
                  {
                    title: '提问人',
                    dataIndex: 'user_name',
                    key: 'user_name',
                    width: 100,
                  },
                  {
                    title: '提问时间',
                    dataIndex: 'created_at',
                    key: 'created_at',
                    width: 150,
                    render: (text: string) => text ? new Date(text).toLocaleString() : '',
                  },
                  {
                    title: '回复人',
                    dataIndex: 'reply_user_name',
                    key: 'reply_user_name',
                    width: 100,
                    render: (text: string) => text || '-',
                  },
                  {
                    title: '回复时间',
                    dataIndex: 'reply_at',
                    key: 'reply_at',
                    width: 150,
                    render: (text: string) => text ? new Date(text).toLocaleString() : '-',
                  },
                ]}
                rowKey="id"
                pagination={false}
                size="small"
                scroll={{ x: 1200 }}
              />
            </Card>
          </div> */}
        </div>
      </div>
    </div>
  );
}