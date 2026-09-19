'use client';

import React, { useState, useEffect } from 'react';
import {
  Card, Row, Col, Typography, Tree, Input, Button, Space, Tag, message, Empty
} from 'antd';
import {
  SearchOutlined, ApartmentOutlined, DatabaseOutlined,
  ExperimentOutlined, BarChartOutlined, CheckCircleOutlined,
  SaveOutlined, ArrowLeftOutlined, CloudOutlined,
  AppstoreOutlined, SafetyCertificateOutlined
} from '@ant-design/icons';
import { useRouter } from 'next/navigation';

const { Title, Text } = Typography;

interface Equipment {
  id: string;
  name: string;
  type: string;
  category: string;
  description?: string;
}

interface Environment {
  value: string;
  label: string;
  color: string;
  icon: string;
  description: string;
}

interface TestProject {
  id: string;
  name: string;
  code: string;
  description: string;
  environment: string;
  environmentLabel: string;
  equipment?: string;
}

interface AssessmentIndicator {
  id: string;
  name: string;
  category: string;
  description: string;
  unit?: string;
  standard?: string;
  equipment?: string;
}

interface TestMethod {
  id: string;
  name: string;
  description: string;
  applicableIndicators: string[];
  applicableProjects: string[];
  standard: string;
  category: string;
  tags: string[];
}

interface EquipmentTreeNode {
  title: string;
  key: string;
  children?: EquipmentTreeNode[];
  isLeaf?: boolean;
  equipment?: Equipment;
}

interface ProjectTreeNode {
  title: string;
  key: string;
  children?: ProjectTreeNode[];
  isLeaf?: boolean;
  project?: TestProject;
}

const equipmentCategories = [
  { value: "aviation", label: "航空装备", icon: <CloudOutlined />, color: "#3b82f6" },
  { value: "ground", label: "地面装备", icon: <AppstoreOutlined />, color: "#8b5cf6" },
  { value: "naval", label: "舰艇装备", icon: <DatabaseOutlined />, color: "#10b981" },
  { value: "electronic", label: "电子装备", icon: <SafetyCertificateOutlined />, color: "#f59e0b" },
];

const equipmentTypes = [
  { value: "drone", label: "无人机", category: "aviation", icon: <CloudOutlined />, color: "#3b82f6", description: "各类无人机系统测试" },
  { value: "tank", label: "坦克", category: "ground", icon: <AppstoreOutlined />, color: "#8b5cf6", description: "坦克系统及组件测试" },
  { value: "radar", label: "雷达", category: "electronic", icon: <SafetyCertificateOutlined />, color: "#10b981", description: "雷达探测系统测试" },
  { value: "communication", label: "通信设备", category: "electronic", icon: <DatabaseOutlined />, color: "#f59e0b", description: "通信系统设备测试" },
];

const specificEquipment: Record<string, Equipment[]> = {
  drone: [
    { id: "drone-001", name: "无人机A型", type: "drone", category: "aviation", description: "固定翼无人机" },
    { id: "drone-002", name: "无人机B型", type: "drone", category: "aviation", description: "多旋翼无人机" },
    { id: "drone-003", name: "无人机C型", type: "drone", category: "aviation", description: "直升机无人机" },
  ],
  tank: [
    { id: "tank-001", name: "坦克99A", type: "tank", category: "ground", description: "主战坦克" },
    { id: "tank-002", name: "坦克96B", type: "tank", category: "ground", description: "主战坦克" },
    { id: "tank-003", name: "步兵战车", type: "tank", category: "ground", description: "装甲步兵战车" },
  ],
  radar: [
    { id: "radar-001", name: "雷达X型", type: "radar", category: "electronic", description: "远程预警雷达" },
    { id: "radar-002", name: "雷达Y型", type: "radar", category: "electronic", description: "火控雷达" },
  ],
  communication: [
    { id: "comm-001", name: "通信设备A型", type: "communication", category: "electronic", description: "战术通信系统" },
    { id: "comm-002", name: "通信设备B型", type: "communication", category: "electronic", description: "卫星通信设备" },
  ],
};

const environmentOptions: Environment[] = [
  { value: "high_temperature", label: "复杂电磁环境", color: "#ef4444", icon: "🌡️", description: "复杂电磁环境适应性测试" },
  { value: "low_temperature", label: "复杂地理环境", color: "#3b82f6", icon: "❄️", description: "复杂地理环境适应性测试" },
  { value: "humidity", label: "复杂气象环境", color: "#10b981", icon: "🌪️", description: "复杂气象环境适应性测试" },
  { value: "high_altitude", label: "复杂水文环境", color: "#6366f1", icon: "💧", description: "复杂水文环境测试" },
  { value: "vibration", label: "复杂水声环境", color: "#f59e0b", icon: "💧", description: "复杂水声环境适应性测试" },
  { value: "sand_dust", label: "高原综合环境", color: "#d97706", icon: "🌪️", description: "高原综合环境防护测试" },
  { value: "rain", label: "海上综合环境", color: "#0ea5e9", icon: "🌧️", description: "海上综合环境防护测试" },
  { value: "salt_fog", label: "其他环节", color: "#8b5cf6", icon: "🌊", description: "其他环节测试" },
];

const testProjectsData: Record<string, Record<string, any[]>> = {
  drone: {
    high_temperature: [
      {
        id: "d-h-cat",
        name: "高温环境测试项目",
        children: [
          { id: "d-h-1", name: "高温启动测试", code: "GJB 150.3A", description: "在高温环境下测试无人机启动性能", environment: "high_temperature", environmentLabel: "复杂电磁环境", equipment: "drone" },
          { id: "d-h-2", name: "高温飞行测试", code: "GJB 150.3A", description: "高温环境下飞行稳定性测试", environment: "high_temperature", environmentLabel: "复杂电磁环境", equipment: "drone" },
          { id: "d-h-3", name: "电池高温测试", code: "GJB 150.3A", description: "高温环境下电池性能测试", environment: "high_temperature", environmentLabel: "复杂电磁环境", equipment: "drone" },
        ]
      },
    ],
    low_temperature: [
      {
        id: "d-l-cat",
        name: "低温环境测试项目",
        children: [
          { id: "d-l-1", name: "低温启动测试", code: "GJB 150.4A", description: "低温环境下无人机启动测试", environment: "low_temperature", environmentLabel: "复杂地理环境", equipment: "drone" },
          { id: "d-l-2", name: "低温飞行测试", code: "GJB 150.4A", description: "低温环境下飞行性能测试", environment: "low_temperature", environmentLabel: "复杂地理环境", equipment: "drone" },
        ]
      },
    ],
    humidity: [
      {
        id: "d-hu-cat",
        name: "湿热环境测试项目",
        children: [
          { id: "d-hu-1", name: "湿热环境测试", code: "GJB 150.9A", description: "高湿度环境下无人机性能测试", environment: "humidity", environmentLabel: "复杂气象环境", equipment: "drone" },
        ]
      },
    ],
  },
  tank: {
    high_temperature: [
      {
        id: "t-h-cat",
        name: "高温环境测试项目",
        children: [
          { id: "t-h-1", name: "系统间电磁兼容性试验", code: "GJB XXXXX", description: "系统间电磁兼容性试验", environment: "high_temperature", environmentLabel: "复杂电磁环境", equipment: "tank" },
          { id: "t-h-2", name: "抗干扰试验", code: "GJB XXXXX", description: "抗干扰试验", environment: "high_temperature", environmentLabel: "复杂电磁环境", equipment: "tank" },
        ]
      },
    ],
    low_temperature: [
      {
        id: "t-l-cat",
        name: "低温环境测试项目",
        children: [
          { id: "t-l-1", name: "机动性能试验", code: "GJB XXXXX", description: "机动性能试验", environment: "low_temperature", environmentLabel: "复杂地理环境", equipment: "tank" },
          { id: "t-l-2", name: "通信性能试验", code: "GJB XXXXX", description: "通信性能试验", environment: "low_temperature", environmentLabel: "复杂地理环境", equipment: "tank" },
        ]
      },
    ],
  },
};

const assessmentIndicatorsData: Record<string, AssessmentIndicator[]> = {
  drone: [
    { id: "d-1", name: "飞行性能指标", category: "性能类", description: "包括最大飞行高度、最大飞行速度、续航时间等", unit: "米/秒/分钟", standard: "GJB 5434-2005", equipment: "drone" },
    { id: "d-2", name: "载荷能力指标", category: "能力类", description: "最大载荷重量、载荷种类适应性", unit: "千克", standard: "GJB 5434-2005", equipment: "drone" },
    { id: "d-3", name: "环境适应性指标", category: "适应性类", description: "高低温、湿度、抗风等级等环境适应性", unit: "级", standard: "GJB 150A-2009", equipment: "drone" },
    { id: "d-4", name: "可靠性指标", category: "可靠性类", description: "平均故障间隔时间、任务可靠度", unit: "小时", standard: "GJB 899A-2009", equipment: "drone" },
  ],
  tank: [
    { id: "t-1", name: "最大速度", category: "性能类", description: "最大速度", unit: "公里/小时", standard: "GJB XXXXX", equipment: "tank" },
    { id: "t-2", name: "平均越野速度", category: "性能类", description: "平均越野速度", unit: "公里/小时", standard: "GJB XXXXX", equipment: "tank" },
    { id: "t-3", name: "通信距离", category: "能力类", description: "通信距离", unit: "米", standard: "GJB XXXXX", equipment: "tank" },
  ],
  radar: [
    { id: "r-1", name: "探测性能指标", category: "性能类", description: "探测距离、探测精度", unit: "公里", standard: "GJB 74A-1998", equipment: "radar" },
    { id: "r-2", name: "跟踪性能指标", category: "性能类", description: "跟踪精度、跟踪距离", unit: "米", standard: "GJB 74A-1998", equipment: "radar" },
  ],
  communication: [
    { id: "c-1", name: "通信性能指标", category: "性能类", description: "通信距离、通信速率", unit: "公里/Mbps", standard: "GJB 367A-2001", equipment: "communication" },
    { id: "c-2", name: "抗干扰指标", category: "适应性类", description: "抗干扰能力", unit: "dB", standard: "GJB 367A-2001", equipment: "communication" },
  ],
};

const testMethodsDatabase: TestMethod[] = [
  {
    id: "method-d-1",
    name: "飞行性能测试法",
    description: "测试无人机的最大飞行高度、速度和续航时间",
    applicableIndicators: ["d-1", "d-4"],
    applicableProjects: ["d-h-1", "d-h-2", "d-l-1", "d-l-2"],
    standard: "GJB 5434-2005",
    category: "性能测试",
    tags: ["飞行性能", "续航测试"],
  },
  {
    id: "method-d-2",
    name: "载荷能力测试法",
    description: "测试无人机的最大载荷重量和种类适应性",
    applicableIndicators: ["d-2"],
    applicableProjects: ["d-h-2", "d-l-2"],
    standard: "GJB 5434-2005",
    category: "能力测试",
    tags: ["载荷能力", "适应性测试"],
  },
  {
    id: "method-t-1",
    name: "直线跑道法",
    description: "在标准直线跑道上测试坦克最大速度",
    applicableIndicators: ["t-1", "t-2"],
    applicableProjects: ["t-l-1"],
    standard: "GJB 59.18-2008",
    category: "性能测试",
    tags: ["速度测试", "机动性"],
  },
];

export default function TemplateSelectionPage() {
  const router = useRouter();

  const [equipmentTreeData, setEquipmentTreeData] = useState<EquipmentTreeNode[]>([]);
  const [selectedEquipmentKeys, setSelectedEquipmentKeys] = useState<string[]>([]);
  const [equipmentSearchText, setEquipmentSearchText] = useState("");

  const [selectedEnvironments, setSelectedEnvironments] = useState<string[]>([]);
  const [environmentsConfirmed, setEnvironmentsConfirmed] = useState(false);

  const [availableProjects, setAvailableProjects] = useState<ProjectTreeNode[]>([]);
  const [selectedProjects, setSelectedProjects] = useState<string[]>([]);
  const [projectsConfirmed, setProjectsConfirmed] = useState(false);

  const [availableIndicators, setAvailableIndicators] = useState<AssessmentIndicator[]>([]);
  const [selectedIndicators, setSelectedIndicators] = useState<string[]>([]);
  const [indicatorsConfirmed, setIndicatorsConfirmed] = useState(false);

  const [filteredMethods, setFilteredMethods] = useState<TestMethod[]>([]);
  const [selectedMethods, setSelectedMethods] = useState<string[]>([]);

  useEffect(() => {
    buildEquipmentTree();
  }, []);

  useEffect(() => {
    if (selectedEquipmentKeys.length > 0) {
      const selectedTypes = new Set<string>();
      selectedEquipmentKeys.forEach(key => {
        const categoryNode = equipmentCategories.find(cat => cat.value === key);
        if (categoryNode) {
          equipmentTypes.filter(type => type.category === key).forEach(type => selectedTypes.add(type.value));
          return;
        }
        const typeNode = equipmentTypes.find(type => type.value === key);
        if (typeNode) {
          selectedTypes.add(key);
          return;
        }
        const equipmentType = equipmentTypes.find(et =>
          specificEquipment[et.value]?.some(e => e.id === key)
        );
        if (equipmentType) {
          selectedTypes.add(equipmentType.value);
        }
      });

      const allIndicators: AssessmentIndicator[] = [];
      selectedTypes.forEach(type => {
        const indicators = assessmentIndicatorsData[type] || [];
        allIndicators.push(...indicators);
      });
      setAvailableIndicators(allIndicators);
      setSelectedIndicators([]);
      setIndicatorsConfirmed(false);

      setAvailableProjects([]);
      setSelectedProjects([]);
      setProjectsConfirmed(false);
      setSelectedEnvironments([]);
      setEnvironmentsConfirmed(false);
    } else {
      setAvailableIndicators([]);
      setSelectedIndicators([]);
      setAvailableProjects([]);
      setSelectedProjects([]);
    }
  }, [selectedEquipmentKeys]);

  useEffect(() => {
    if (selectedEquipmentKeys.length > 0 && selectedEnvironments.length > 0 && environmentsConfirmed) {
      const selectedTypes = new Set<string>();
      selectedEquipmentKeys.forEach(key => {
        const categoryNode = equipmentCategories.find(cat => cat.value === key);
        if (categoryNode) {
          equipmentTypes.filter(type => type.category === key).forEach(type => selectedTypes.add(type.value));
          return;
        }
        const typeNode = equipmentTypes.find(type => type.value === key);
        if (typeNode) {
          selectedTypes.add(key);
          return;
        }
        const equipmentType = equipmentTypes.find(et =>
          specificEquipment[et.value]?.some(e => e.id === key)
        );
        if (equipmentType) {
          selectedTypes.add(equipmentType.value);
        }
      });

      const projectTreeNodes: ProjectTreeNode[] = [];
      selectedTypes.forEach(type => {
        selectedEnvironments.forEach(env => {
          const projects = testProjectsData[type]?.[env] || [];
          projects.forEach((categoryNode: any) => {
            const treeNode: ProjectTreeNode = {
              title: categoryNode.name,
              key: categoryNode.id,
              children: categoryNode.children?.map((child: TestProject) => ({
                title: child.name,
                key: child.id,
                isLeaf: true,
                project: child,
              })) || [],
            };
            projectTreeNodes.push(treeNode);
          });
        });
      });

      setAvailableProjects(projectTreeNodes);
      setSelectedProjects([]);
      setProjectsConfirmed(false);
    } else {
      setAvailableProjects([]);
      setSelectedProjects([]);
    }
  }, [selectedEnvironments, selectedEquipmentKeys, environmentsConfirmed]);

  useEffect(() => {
    filterMethods();
  }, [selectedIndicators, selectedProjects, indicatorsConfirmed, projectsConfirmed]);

  const buildEquipmentTree = () => {
    const treeData: EquipmentTreeNode[] = equipmentCategories.map(category => ({
      title: category.label,
      key: category.value,
      children: equipmentTypes
        .filter(type => type.category === category.value)
        .map(type => ({
          title: type.label,
          key: type.value,
          children: (specificEquipment[type.value] || []).map(equipment => ({
            title: equipment.name,
            key: equipment.id,
            isLeaf: true,
            equipment,
          })),
        })),
    }));
    setEquipmentTreeData(treeData);
  };

  const filterMethods = () => {
    if (!projectsConfirmed || !indicatorsConfirmed) {
      setFilteredMethods([]);
      return;
    }

    if (selectedProjects.length === 0 && selectedIndicators.length === 0) {
      setFilteredMethods([]);
      return;
    }

    let filtered = [...testMethodsDatabase];

    if (selectedIndicators.length > 0 || selectedProjects.length > 0) {
      filtered = filtered.filter(method => {
        const matchesIndicator = selectedIndicators.length === 0 ||
          method.applicableIndicators.some(indicator => selectedIndicators.includes(indicator));
        const matchesProject = selectedProjects.length === 0 ||
          method.applicableProjects.some(project => selectedProjects.includes(project));
        return matchesIndicator || matchesProject;
      });
    }

    setFilteredMethods(filtered);
  };

  const handleEquipmentSelect = (checked: any) => {
    const keys = Array.isArray(checked) ? checked : checked.checked;
    setSelectedEquipmentKeys(keys as string[]);
  };

  const confirmEnvironmentSelection = () => {
    if (selectedEnvironments.length === 0) {
      message.warning("请至少选择一个环境");
      return;
    }
    setEnvironmentsConfirmed(true);
    message.success(`已确认选择 ${selectedEnvironments.length} 个环境`);
  };

  const confirmProjectSelection = () => {
    if (selectedProjects.length === 0) {
      message.warning("请至少选择一个项目");
      return;
    }
    setProjectsConfirmed(true);
    message.success(`已确认选择 ${selectedProjects.length} 个项目`);
  };

  const confirmIndicatorSelection = () => {
    if (selectedIndicators.length === 0) {
      message.warning("请至少选择一个指标");
      return;
    }
    setIndicatorsConfirmed(true);
    message.success(`已确认选择 ${selectedIndicators.length} 个指标`);
  };

  const handleSaveTemplate = () => {
    if (selectedEquipmentKeys.length === 0) {
      message.warning("请至少选择一个设备");
      return;
    }

    const templateData = {
      equipment: selectedEquipmentKeys,
      environments: selectedEnvironments,
      projects: selectedProjects,
      indicators: selectedIndicators,
      methods: selectedMethods,
      timestamp: new Date().toISOString(),
    };

    localStorage.setItem('selectedTemplate', JSON.stringify(templateData));
    message.success("模板保存成功");
    router.push('/FZSJ/JYLLM');
  };

  const handleMethodSelect = (methodId: string) => {
    setSelectedMethods(prev =>
      prev.includes(methodId)
        ? prev.filter(id => id !== methodId)
        : [...prev, methodId]
    );
  };

  return (
    <div style={{ padding: '24px', background: '#f5f5f5', minHeight: '100vh' }}>
      <Card
        style={{
          borderRadius: 12,
          boxShadow: '0 2px 8px rgba(0,0,0,0.06)',
        }}
      >
        <div style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          marginBottom: 24,
          paddingBottom: 16,
          borderBottom: '1px solid #f0f0f0'
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
            <Button
              icon={<ArrowLeftOutlined />}
              onClick={() => router.push('/FZSJ/JYLLM')}
            >
              返回
            </Button>
            <Title level={4} style={{ margin: 0 }}>选择试验模板</Title>
          </div>
          <Button
            type="primary"
            icon={<SaveOutlined />}
            onClick={handleSaveTemplate}
            disabled={
              selectedEquipmentKeys.length === 0 ||
              selectedEnvironments.length === 0 ||
              selectedProjects.length === 0 ||
              selectedIndicators.length === 0
            }
          >
            保存模板
          </Button>
        </div>

        <Row gutter={24}>
          <Col span={6}>
            <Card
              title={
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <ApartmentOutlined />
                  <span>设备选择</span>
                </div>
              }
              size="small"
              style={{ height: '100%' }}
            >
              <Input
                placeholder="搜索设备"
                prefix={<SearchOutlined />}
                value={equipmentSearchText}
                onChange={e => setEquipmentSearchText(e.target.value)}
                style={{ marginBottom: 12 }}
                allowClear
              />
              <div style={{ maxHeight: 400, overflow: 'auto' }}>
                <Tree
                  checkable
                  treeData={equipmentTreeData}
                  checkedKeys={selectedEquipmentKeys}
                  onCheck={handleEquipmentSelect}
                  height={380}
                />
              </div>
              {selectedEquipmentKeys.length > 0 && (
                <div style={{ marginTop: 12, padding: '8px', background: '#f0f5ff', borderRadius: 4 }}>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    已选择 {selectedEquipmentKeys.length} 项
                  </Text>
                </div>
              )}
            </Card>
          </Col>

          <Col span={6}>
            <Card
              title={
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <DatabaseOutlined />
                  <span>环境选择</span>
                </div>
              }
              size="small"
              style={{ height: '100%' }}
            >
              {selectedEquipmentKeys.length > 0 ? (
                <>
                  <div style={{ maxHeight: 400, overflow: 'auto' }}>
                    {environmentOptions.map(env => (
                      <div
                        key={env.value}
                        onClick={() => {
                          if (environmentsConfirmed) return;
                          setSelectedEnvironments(prev =>
                            prev.includes(env.value)
                              ? prev.filter(v => v !== env.value)
                              : [...prev, env.value]
                          );
                        }}
                        style={{
                          padding: '12px',
                          marginBottom: 8,
                          background: selectedEnvironments.includes(env.value) ? `${env.color}15` : '#fff',
                          border: `1px solid ${selectedEnvironments.includes(env.value) ? env.color : '#e5e7eb'}`,
                          borderRadius: 8,
                          cursor: environmentsConfirmed ? 'default' : 'pointer',
                          opacity: environmentsConfirmed && !selectedEnvironments.includes(env.value) ? 0.5 : 1,
                        }}
                      >
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                          <span style={{ fontSize: 16 }}>{env.icon}</span>
                          <Text strong style={{ color: selectedEnvironments.includes(env.value) ? env.color : '#374151' }}>
                            {env.label}
                          </Text>
                          {selectedEnvironments.includes(env.value) && (
                            <CheckCircleOutlined style={{ color: env.color, marginLeft: 'auto' }} />
                          )}
                        </div>
                        <Text type="secondary" style={{ fontSize: 12, marginTop: 4, display: 'block' }}>
                          {env.description}
                        </Text>
                      </div>
                    ))}
                  </div>
                  <div style={{ marginTop: 12 }}>
                    <Button
                      type="primary"
                      block
                      onClick={confirmEnvironmentSelection}
                      disabled={selectedEnvironments.length === 0 || environmentsConfirmed}
                    >
                      {environmentsConfirmed ? '已确认' : '确认选择'}
                    </Button>
                  </div>
                </>
              ) : (
                <Empty description="请先选择设备" />
              )}
            </Card>
          </Col>

          <Col span={6}>
            <Card
              title={
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <ExperimentOutlined />
                  <span>测试项目</span>
                </div>
              }
              size="small"
              style={{ height: '100%' }}
            >
              {environmentsConfirmed ? (
                <>
                  <div style={{ maxHeight: 400, overflow: 'auto' }}>
                    {availableProjects.length > 0 ? (
                      <Tree
                        checkable
                        treeData={availableProjects}
                        checkedKeys={selectedProjects}
                        onCheck={(checked) => setSelectedProjects(checked as string[])}
                        height={380}
                      />
                    ) : (
                      <Empty description="暂无相关项目" />
                    )}
                  </div>
                  <div style={{ marginTop: 12 }}>
                    <Button
                      type="primary"
                      block
                      onClick={confirmProjectSelection}
                      disabled={selectedProjects.length === 0 || projectsConfirmed}
                    >
                      {projectsConfirmed ? '已确认' : '确认选择'}
                    </Button>
                  </div>
                </>
              ) : (
                <Empty description="请先确认环境选择" />
              )}
            </Card>
          </Col>

          <Col span={6}>
            <Card
              title={
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <BarChartOutlined />
                  <span>考核指标</span>
                </div>
              }
              size="small"
              style={{ height: '100%' }}
            >
              {selectedEquipmentKeys.length > 0 ? (
                <>
                  <div style={{ maxHeight: 400, overflow: 'auto' }}>
                    {availableIndicators.length > 0 ? (
                      availableIndicators.map(indicator => (
                        <div
                          key={indicator.id}
                          onClick={() => {
                            if (indicatorsConfirmed) return;
                            setSelectedIndicators(prev =>
                              prev.includes(indicator.id)
                                ? prev.filter(id => id !== indicator.id)
                                : [...prev, indicator.id]
                            );
                          }}
                          style={{
                            padding: '12px',
                            marginBottom: 8,
                            background: selectedIndicators.includes(indicator.id) ? '#e6f7ff' : '#fff',
                            border: `1px solid ${selectedIndicators.includes(indicator.id) ? '#1890ff' : '#e5e7eb'}`,
                            borderRadius: 8,
                            cursor: indicatorsConfirmed ? 'default' : 'pointer',
                            opacity: indicatorsConfirmed && !selectedIndicators.includes(indicator.id) ? 0.5 : 1,
                          }}
                        >
                          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                            <Text strong style={{ color: selectedIndicators.includes(indicator.id) ? '#1890ff' : '#374151' }}>
                              {indicator.name}
                            </Text>
                            {selectedIndicators.includes(indicator.id) && (
                              <CheckCircleOutlined style={{ color: '#1890ff', marginLeft: 'auto' }} />
                            )}
                          </div>
                          <Text type="secondary" style={{ fontSize: 12, marginTop: 4, display: 'block' }}>
                            {indicator.description}
                          </Text>
                          <Tag color="blue" style={{ marginTop: 4 }}>{indicator.category}</Tag>
                        </div>
                      ))
                    ) : (
                      <Empty description="暂无相关指标" />
                    )}
                  </div>
                  <div style={{ marginTop: 12 }}>
                    <Button
                      type="primary"
                      block
                      onClick={confirmIndicatorSelection}
                      disabled={selectedIndicators.length === 0 || indicatorsConfirmed}
                    >
                      {indicatorsConfirmed ? '已确认' : '确认选择'}
                    </Button>
                  </div>
                </>
              ) : (
                <Empty description="请先选择设备" />
              )}
            </Card>
          </Col>
        </Row>

        {projectsConfirmed && indicatorsConfirmed && filteredMethods.length > 0 && (
          <Card
            title={
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <ExperimentOutlined />
                <span>相关试验方法</span>
                <Tag color="blue">{filteredMethods.length} 个</Tag>
              </div>
            }
            size="small"
            style={{ marginTop: 24 }}
          >
            <Row gutter={[16, 16]}>
              {filteredMethods.map(method => (
                <Col span={8} key={method.id}>
                  <Card
                    size="small"
                    hoverable
                    onClick={() => handleMethodSelect(method.id)}
                    style={{
                      border: selectedMethods.includes(method.id) ? '2px solid #1890ff' : '1px solid #e5e7eb',
                      background: selectedMethods.includes(method.id) ? '#e6f7ff' : '#fff',
                    }}
                  >
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                      <div>
                        <Text strong>{method.name}</Text>
                        <Text type="secondary" style={{ fontSize: 12, display: 'block', marginTop: 4 }}>
                          {method.description}
                        </Text>
                        <div style={{ marginTop: 8 }}>
                          <Tag color="green">{method.category}</Tag>
                          <Tag color="orange">{method.standard}</Tag>
                        </div>
                      </div>
                      {selectedMethods.includes(method.id) && (
                        <CheckCircleOutlined style={{ color: '#1890ff' }} />
                      )}
                    </div>
                  </Card>
                </Col>
              ))}
            </Row>
          </Card>
        )}
      </Card>
    </div>
  );
}
