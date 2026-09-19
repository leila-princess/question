// mockGraphData.ts
export interface GraphNode {
  id: string;
  label: string;
  group: 'equipment' | 'testItem' | 'indicator' | 'method' | 'standard';
  title?: string;
  value?: number;
}

export interface GraphEdge {
  id: string;
  from: string;
  to: string;
  label: string;
  color?: string;
}

export interface GraphData {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

// 装备数据
const equipmentNodes: GraphNode[] = [
  { id: 'eq1', label: '战斗机雷达系统', group: 'equipment', title: '军用战斗机雷达设备', value: 15 },
  { id: 'eq2', label: '舰艇通信系统', group: 'equipment', title: '海军舰艇通信设备', value: 12 },
  { id: 'eq3', label: '导弹制导系统', group: 'equipment', title: '战术导弹制导设备', value: 18 },
  { id: 'eq4', label: '军用计算机', group: 'equipment', title: '军用加固计算机', value: 10 },
  { id: 'eq5', label: '无人机飞控系统', group: 'equipment', title: '无人机飞行控制系统', value: 14 },
];

// 试验项目数据
const testItemNodes: GraphNode[] = [
  { id: 'ti1', label: '高温存储试验', group: 'testItem', title: '高温环境下的存储测试', value: 8 },
  { id: 'ti2', label: '低温启动试验', group: 'testItem', title: '低温环境下的启动测试', value: 8 },
  { id: 'ti3', label: '温度循环试验', group: 'testItem', title: '温度循环变化测试', value: 10 },
  { id: 'ti4', label: '湿热试验', group: 'testItem', title: '高温高湿环境测试', value: 7 },
  { id: 'ti5', label: '振动试验', group: 'testItem', title: '机械振动环境测试', value: 12 },
  { id: 'ti6', label: '冲击试验', group: 'testItem', title: '机械冲击环境测试', value: 9 },
  { id: 'ti7', label: '盐雾试验', group: 'testItem', title: '盐雾腐蚀环境测试', value: 6 },
  { id: 'ti8', label: '霉菌试验', group: 'testItem', title: '霉菌生长环境测试', value: 5 },
  { id: 'ti9', label: '太阳辐射试验', group: 'testItem', title: '太阳辐射环境测试', value: 7 },
  { id: 'ti10', label: '淋雨试验', group: 'testItem', title: '淋雨防水测试', value: 6 },
];

// 考核指标数据
const indicatorNodes: GraphNode[] = [
  { id: 'ind1', label: '工作温度范围', group: 'indicator', title: '-40℃ ~ +85℃', value: 5 },
  { id: 'ind2', label: '存储温度范围', group: 'indicator', title: '-55℃ ~ +125℃', value: 5 },
  { id: 'ind3', label: '相对湿度', group: 'indicator', title: '95% RH', value: 4 },
  { id: 'ind4', label: '振动频率', group: 'indicator', title: '10Hz ~ 2000Hz', value: 6 },
  { id: 'ind5', label: '冲击加速度', group: 'indicator', title: '50g, 11ms', value: 5 },
  { id: 'ind6', label: '盐雾浓度', group: 'indicator', title: '5% NaCl', value: 3 },
  { id: 'ind7', label: '霉菌等级', group: 'indicator', title: '0级 ~ 4级', value: 3 },
  { id: 'ind8', label: '太阳辐射强度', group: 'indicator', title: '1120W/m²', value: 4 },
  { id: 'ind9', label: '防水等级', group: 'indicator', title: 'IPX4 ~ IPX8', value: 4 },
];

// 试验方法数据
const methodNodes: GraphNode[] = [
  { id: 'm1', label: '高温试验方法', group: 'method', title: 'GJB150.3A-2009', value: 7 },
  { id: 'm2', label: '低温试验方法', group: 'method', title: 'GJB150.4A-2009', value: 7 },
  { id: 'm3', label: '温度循环试验方法', group: 'method', title: 'GJB150.5A-2009', value: 8 },
  { id: 'm4', label: '湿热试验方法', group: 'method', title: 'GJB150.9A-2009', value: 6 },
  { id: 'm5', label: '振动试验方法', group: 'method', title: 'GJB150.16A-2009', value: 9 },
  { id: 'm6', label: '冲击试验方法', group: 'method', title: 'GJB150.18A-2009', value: 8 },
  { id: 'm7', label: '盐雾试验方法', group: 'method', title: 'GJB150.11A-2009', value: 5 },
  { id: 'm8', label: '霉菌试验方法', group: 'method', title: 'GJB150.10A-2009', value: 5 },
];

// 标准数据
const standardNodes: GraphNode[] = [
  { id: 'std1', label: 'GJB150.3A-2009', group: 'standard', title: '高温试验标准', value: 10 },
  { id: 'std2', label: 'GJB150.4A-2009', group: 'standard', title: '低温试验标准', value: 10 },
  { id: 'std3', label: 'GJB150.5A-2009', group: 'standard', title: '温度冲击试验标准', value: 10 },
  { id: 'std4', label: 'GJB150.9A-2009', group: 'standard', title: '湿热试验标准', value: 10 },
  { id: 'std5', label: 'GJB150.16A-2009', group: 'standard', title: '振动试验标准', value: 10 },
  { id: 'std6', label: 'GJB150.18A-2009', group: 'standard', title: '冲击试验标准', value: 10 },
  { id: 'std7', label: 'GJB150.11A-2009', group: 'standard', title: '盐雾试验标准', value: 10 },
  { id: 'std8', label: 'GJB150.10A-2009', group: 'standard', title: '霉菌试验标准', value: 10 },
  { id: 'std9', label: 'GB/T2423.1-2008', group: 'standard', title: '低温试验方法', value: 8 },
  { id: 'std10', label: 'GB/T2423.2-2008', group: 'standard', title: '高温试验方法', value: 8 },
];

// 所有节点
const nodes: GraphNode[] = [
  ...equipmentNodes,
  ...testItemNodes,
  ...indicatorNodes,
  ...methodNodes,
  ...standardNodes,
];

// 边（关系）数据 - 按照装备->试验项目->考核指标->试验方法->标准 的逻辑
const edges: GraphEdge[] = [
  // 装备包含试验项目
  { id: 'e1', from: 'eq1', to: 'ti1', label: '包含' },
  { id: 'e2', from: 'eq1', to: 'ti2', label: '包含' },
  { id: 'e3', from: 'eq1', to: 'ti5', label: '包含' },
  { id: 'e4', from: 'eq2', to: 'ti3', label: '包含' },
  { id: 'e5', from: 'eq2', to: 'ti4', label: '包含' },
  { id: 'e6', from: 'eq2', to: 'ti7', label: '包含' },
  { id: 'e7', from: 'eq3', to: 'ti6', label: '包含' },
  { id: 'e8', from: 'eq3', to: 'ti1', label: '包含' },
  { id: 'e9', from: 'eq4', to: 'ti9', label: '包含' },
  { id: 'e10', from: 'eq4', to: 'ti8', label: '包含' },
  { id: 'e11', from: 'eq5', to: 'ti10', label: '包含' },
  { id: 'e12', from: 'eq5', to: 'ti3', label: '包含' },
  
  // 试验项目考核指标
  { id: 'e13', from: 'ti1', to: 'ind1', label: '考核', color: '#ff4d4f' },
  { id: 'e14', from: 'ti1', to: 'ind2', label: '考核', color: '#ff4d4f' },
  { id: 'e15', from: 'ti2', to: 'ind1', label: '考核', color: '#ff4d4f' },
  { id: 'e16', from: 'ti3', to: 'ind1', label: '考核', color: '#ff4d4f' },
  { id: 'e17', from: 'ti3', to: 'ind2', label: '考核', color: '#ff4d4f' },
  { id: 'e18', from: 'ti4', to: 'ind3', label: '考核', color: '#ff4d4f' },
  { id: 'e19', from: 'ti5', to: 'ind4', label: '考核', color: '#ff4d4f' },
  { id: 'e20', from: 'ti6', to: 'ind5', label: '考核', color: '#ff4d4f' },
  { id: 'e21', from: 'ti7', to: 'ind6', label: '考核', color: '#ff4d4f' },
  { id: 'e22', from: 'ti8', to: 'ind7', label: '考核', color: '#ff4d4f' },
  { id: 'e23', from: 'ti9', to: 'ind8', label: '考核', color: '#ff4d4f' },
  { id: 'e24', from: 'ti10', to: 'ind9', label: '考核', color: '#ff4d4f' },
  
  // 考核指标使用试验方法
  { id: 'e25', from: 'ind1', to: 'm1', label: '使用', color: '#52c41a' },
  { id: 'e26', from: 'ind2', to: 'm2', label: '使用', color: '#52c41a' },
  { id: 'e27', from: 'ind3', to: 'm4', label: '使用', color: '#52c41a' },
  { id: 'e28', from: 'ind4', to: 'm5', label: '使用', color: '#52c41a' },
  { id: 'e29', from: 'ind5', to: 'm6', label: '使用', color: '#52c41a' },
  { id: 'e30', from: 'ind6', to: 'm7', label: '使用', color: '#52c41a' },
  { id: 'e31', from: 'ind7', to: 'm8', label: '使用', color: '#52c41a' },
  { id: 'e32', from: 'ind8', to: 'm1', label: '使用', color: '#52c41a' },
  { id: 'e33', from: 'ind9', to: 'm4', label: '使用', color: '#52c41a' },
  
  // 试验方法关联标准
  { id: 'e34', from: 'm1', to: 'std1', label: '参考', color: '#1890ff' },
  { id: 'e35', from: 'm2', to: 'std2', label: '参考', color: '#1890ff' },
  { id: 'e36', from: 'm3', to: 'std3', label: '参考', color: '#1890ff' },
  { id: 'e37', from: 'm4', to: 'std4', label: '参考', color: '#1890ff' },
  { id: 'e38', from: 'm5', to: 'std5', label: '参考', color: '#1890ff' },
  { id: 'e39', from: 'm6', to: 'std6', label: '参考', color: '#1890ff' },
  { id: 'e40', from: 'm7', to: 'std7', label: '参考', color: '#1890ff' },
  { id: 'e41', from: 'm8', to: 'std8', label: '参考', color: '#1890ff' },
  { id: 'e42', from: 'm1', to: 'std10', label: '参考', color: '#1890ff' },
  { id: 'e43', from: 'm2', to: 'std9', label: '参考', color: '#1890ff' },
];

export const mockGraphData: GraphData = {
  nodes,
  edges,
};

export const getMockGraphData = (): GraphData => mockGraphData;