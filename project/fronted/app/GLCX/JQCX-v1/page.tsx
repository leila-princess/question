"use client";

import React, { useState, useEffect, useMemo } from "react";
import {
  Card,
  Row,
  Col,
  Typography,
  Tree,
  Input,
  Checkbox,
  Button,
  Space,
  Divider,
  Tag,
  List,
  Empty,
  message,
  Tooltip,
  Badge,
  Collapse,
  Select,
} from "antd";
import {
  SearchOutlined,
  ApartmentOutlined,
  DatabaseOutlined,
  EnvironmentOutlined,
  ExperimentOutlined,
  BarChartOutlined,
  CheckCircleOutlined,
  FilterOutlined,
  FileTextOutlined,
  LinkOutlined,
  CloudOutlined,
  AppstoreOutlined,
  SafetyCertificateOutlined,
  ReloadOutlined,
  ClearOutlined,
} from "@ant-design/icons";
import GlobalNav from "@/components/GlobalNav";
import { useRouter } from "next/navigation";

const { Title, Text, Paragraph } = Typography;
const { Panel } = Collapse;
const { Option } = Select;

/**
 * 关联查询页面
 * 
 * 页面布局：
 * - 左侧：设备树状数据（三层：设备分类 → 设备类型 → 具体设备）
 * - 中间：三层选择区域（环境、项目、指标）
 * - 右侧：方法列表（卡片形式）
 * 
 * 数据关联关系：
 * - 项目依据于设备+环境
 * - 指标依据于设备
 * - 方法依据于项目+指标
 */

// ==================== 类型定义 ====================

/**
 * 设备信息接口
 */
interface Equipment {
  id: string;
  name: string;
  type: string; // 设备类型
  category: string; // 设备分类
  description?: string;
}

/**
 * 环境选项接口
 */
interface Environment {
  value: string;
  label: string;
  color: string;
  icon: string;
  description: string;
}

/**
 * 测试项目接口
 */
interface TestProject {
  id: string;
  name: string;
  code: string;
  description: string;
  environment: string;
  environmentLabel: string;
  equipment?: string; // 所属设备类型
}

/**
 * 项目树节点接口
 */
interface ProjectTreeNode {
  title: string;
  key: string;
  children?: ProjectTreeNode[];
  isLeaf?: boolean;
  project?: TestProject;
}

/**
 * 考核指标接口
 */
interface AssessmentIndicator {
  id: string;
  name: string;
  category: string;
  description: string;
  unit?: string;
  standard?: string;
  equipment?: string; // 所属设备类型
}

/**
 * 试验方法接口
 */
interface TestMethod {
  id: string;
  name: string;
  description: string;
  applicableIndicators: string[];
  applicableProjects: string[];
  standard: string;
  difficulty: string;
  duration: string;
  equipment: string[];
  procedure: string[];
  category: string;
  tags: string[];
}

/**
 * 设备树节点接口
 */
interface EquipmentTreeNode {
  title: string;
  key: string;
  children?: EquipmentTreeNode[];
  isLeaf?: boolean;
  equipment?: Equipment;
}

// ==================== 模拟数据 ====================

/**
 * 设备分类数据
 * 从 select_XM 和 select_ZB 页面提取
 */
const equipmentCategories = [
  { value: "aviation", label: "航空装备", icon: <CloudOutlined />, color: "#3b82f6" },
  { value: "ground", label: "地面装备", icon: <AppstoreOutlined />, color: "#8b5cf6" },
  { value: "naval", label: "舰艇装备", icon: <DatabaseOutlined />, color: "#10b981" },
  { value: "electronic", label: "电子装备", icon: <SafetyCertificateOutlined />, color: "#f59e0b" },
];

/**
 * 设备类型数据
 * 从 select_XM 页面的 equipmentTypes 提取
 */
const equipmentTypes = [
  { value: "drone", label: "无人机", category: "aviation", icon: <CloudOutlined />, color: "#3b82f6", description: "各类无人机系统测试" },
  { value: "tank", label: "坦克", category: "ground", icon: <AppstoreOutlined />, color: "#8b5cf6", description: "坦克系统及组件测试" },
  { value: "radar", label: "雷达", category: "electronic", icon: <SafetyCertificateOutlined />, color: "#10b981", description: "雷达探测系统测试" },
  { value: "communication", label: "通信设备", category: "electronic", icon: <DatabaseOutlined />, color: "#f59e0b", description: "通信系统设备测试" },
];

/**
 * 具体设备数据
 * 模拟每个设备类型下的具体设备
 */
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

/**
 * 环境选项数据
 * 从 select_XM 页面的 environmentOptions 提取
 */
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

/**
 * 测试项目数据（树状结构）
 * 从 select_XM 页面的 testProjectsData 提取
 * 关联关系：项目依据于设备+环境
 * 二级结构：父项目 → 子项目
 */
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
    vibration: [
      { 
        id: "d-v-cat", 
        name: "振动环境测试项目",
        children: [
          { id: "d-v-1", name: "运输振动测试", code: "GJB 150.16A", description: "模拟运输过程中的振动测试", environment: "vibration", environmentLabel: "复杂水声环境", equipment: "drone" },
          { id: "d-v-2", name: "飞行振动测试", code: "GJB 150.16A", description: "飞行过程中的振动影响测试", environment: "vibration", environmentLabel: "复杂水声环境", equipment: "drone" },
        ]
      },
    ],
    sand_dust: [
      { 
        id: "d-t-cat", 
        name: "沙尘环境测试项目",
        children: [
          { id: "d-t-1", name: "沙尘防护测试", code: "GJB 150.12A", description: "沙尘环境下防护性能测试", environment: "sand_dust", environmentLabel: "高原综合环境", equipment: "drone" },
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
          { id: "t-h-2", name: "抗干扰试验", code: "GJB XXXXX", description: "抗干扰试验（定位导航干扰等）", environment: "high_temperature", environmentLabel: "复杂电磁环境", equipment: "tank" },
        ]
      },
    ],
    low_temperature: [
      { 
        id: "t-l-cat", 
        name: "低温环境测试项目",
        children: [
          { id: "t-l-1", name: "机动性能试验", code: "GJB XXXXX", description: "机动性能试验（平均越野速度等）", environment: "low_temperature", environmentLabel: "复杂地理环境", equipment: "tank" },
          { id: "t-l-2", name: "通信性能试验", code: "GJB XXXXX", description: "通信性能试验（数传等）", environment: "low_temperature", environmentLabel: "复杂地理环境", equipment: "tank" },
        ]
      },
    ],
    humidity: [
      { 
        id: "t-hu-cat", 
        name: "湿热环境测试项目",
        children: [
          { id: "t-hu-1", name: "机动性能试验", code: "GJB XXXXX", description: "机动性能试验（持续行驶等）", environment: "humidity", environmentLabel: "复杂气象环境", equipment: "tank" },
          { id: "t-hu-2", name: "侦察性能试验", code: "GJB XXXXX", description: "通信性能试验（识别距离等）", environment: "humidity", environmentLabel: "复杂气象环境", equipment: "tank" },
        ]
      },
    ],
    high_altitude: [],
    vibration: [],
    sand_dust: [
      { 
        id: "t-t-cat", 
        name: "沙尘环境测试项目",
        children: [
          { id: "t-t-1", name: "机动性能试验", code: "GJB XXXXX", description: "机动性能试验（起动试验等）", environment: "sand_dust", environmentLabel: "高原综合环境", equipment: "tank" },
          { id: "t-t-2", name: "火力性能试验", code: "GJB XXXXX", description: "通信性能试验（命中概率等）", environment: "sand_dust", environmentLabel: "高原综合环境", equipment: "tank" },
        ]
      },
    ],
    rain: [],
    salt_fog: [],
  },
  radar: {
    high_temperature: [
      { 
        id: "r-h-cat", 
        name: "高温环境测试项目",
        children: [
          { id: "r-h-1", name: "高温工作测试", code: "GJB 74A", description: "高温环境下雷达工作性能", environment: "high_temperature", environmentLabel: "复杂电磁环境", equipment: "radar" },
        ]
      },
    ],
    low_temperature: [
      { 
        id: "r-l-cat", 
        name: "低温环境测试项目",
        children: [
          { id: "r-l-1", name: "低温工作测试", code: "GJB 74A", description: "低温环境下雷达工作性能", environment: "low_temperature", environmentLabel: "复杂地理环境", equipment: "radar" },
        ]
      },
    ],
    humidity: [
      { 
        id: "r-hu-cat", 
        name: "湿热环境测试项目",
        children: [
          { id: "r-hu-1", name: "湿热环境测试", code: "GJB 74A", description: "湿热环境下雷达性能测试", environment: "humidity", environmentLabel: "复杂气象环境", equipment: "radar" },
        ]
      },
    ],
    vibration: [],
    sand_dust: [],
    rain: [
      { 
        id: "r-ra-cat", 
        name: "淋雨环境测试项目",
        children: [
          { id: "r-ra-1", name: "淋雨环境测试", code: "GJB 74A", description: "淋雨环境下雷达防护测试", environment: "rain", environmentLabel: "海上综合环境", equipment: "radar" },
        ]
      },
    ],
  },
  communication: {
    high_temperature: [
      { 
        id: "c-h-cat", 
        name: "高温环境测试项目",
        children: [
          { id: "c-h-1", name: "高温工作测试", code: "GJB 367A", description: "高温环境下通信设备工作测试", environment: "high_temperature", environmentLabel: "复杂电磁环境", equipment: "communication" },
        ]
      },
    ],
    low_temperature: [
      { 
        id: "c-l-cat", 
        name: "低温环境测试项目",
        children: [
          { id: "c-l-1", name: "低温工作测试", code: "GJB 367A", description: "低温环境下通信设备工作测试", environment: "low_temperature", environmentLabel: "复杂地理环境", equipment: "communication" },
        ]
      },
    ],
    humidity: [],
    vibration: [],
    sand_dust: [],
    salt_fog: [
      { 
        id: "c-sf-cat", 
        name: "盐雾环境测试项目",
        children: [
          { id: "c-sf-1", name: "盐雾腐蚀测试", code: "GJB 150.11A", description: "盐雾环境下耐腐蚀测试", environment: "salt_fog", environmentLabel: "其他环节", equipment: "communication" },
        ]
      },
    ],
    high_altitude: [
      { 
        id: "c-ha-cat", 
        name: "高海拔环境测试项目",
        children: [
          { id: "c-ha-1", name: "高海拔工作测试", code: "GJB 150.2A", description: "高海拔低压环境测试", environment: "high_altitude", environmentLabel: "复杂水文环境", equipment: "communication" },
        ]
      },
    ],
  },
};

/**
 * 考核指标数据
 * 从 select_ZB 页面的 assessmentIndicatorsData 提取
 * 关联关系：指标依据于设备
 */
const assessmentIndicatorsData: Record<string, AssessmentIndicator[]> = {
  drone: [
    { id: "d-1", name: "飞行性能指标", category: "性能类", description: "包括最大飞行高度、最大飞行速度、续航时间等", unit: "米/秒/分钟", standard: "GJB 5434-2005", equipment: "drone" },
    { id: "d-2", name: "载荷能力指标", category: "能力类", description: "最大载荷重量、载荷种类适应性", unit: "千克", standard: "GJB 5434-2005", equipment: "drone" },
    { id: "d-3", name: "环境适应性指标", category: "适应性类", description: "高低温、湿度、抗风等级等环境适应性", unit: "级", standard: "GJB 150A-2009", equipment: "drone" },
    { id: "d-4", name: "可靠性指标", category: "可靠性类", description: "平均故障间隔时间、任务可靠度", unit: "小时", standard: "GJB 899A-2009", equipment: "drone" },
    { id: "d-5", name: "安全性指标", category: "安全类", description: "防撞、防坠落、数据加密等安全性能", unit: "级", standard: "GJB 9001C-2017", equipment: "drone" },
  ],
  tank: [
    { id: "t-1", name: "最大速度", category: "性能类", description: "最大速度", unit: "公里/小时", standard: "GJB XXXXX", equipment: "tank" },
    { id: "t-2", name: "平均越野速度", category: "性能类", description: "平均越野速度", unit: "公里/小时", standard: "GJB XXXXX", equipment: "tank" },
    { id: "t-3", name: "通信距离", category: "能力类", description: "通信距离", unit: "%/米/毫米", standard: "GJB XXXXX", equipment: "tank" },
    { id: "t-4", name: "最大行程", category: "能力类", description: "最大行程", unit: "%/米/毫米", standard: "GJB XXXXX", equipment: "tank" },
    { id: "t-5", name: "命中率", category: "能力类", description: "命中率", unit: "%/100%", standard: "GJB XXXXX", equipment: "tank" },
    { id: "t-6", name: "逃生时间", category: "能力类", description: "逃生时间", unit: "秒/千米", standard: "GJB XXXXX", equipment: "tank" },
    { id: "t-7", name: "防护性能试验", category: "防护类", description: "装甲防护、三防能力、隐身性能", unit: "级", standard: "GJB 59.3-2004", equipment: "tank" },
  ],
  radar: [
    { id: "r-1", name: "探测性能指标", category: "性能类", description: "探测距离、探测精度、多目标处理能力", unit: "公里/米", standard: "GJB 74A-1998", equipment: "radar" },
    { id: "r-2", name: "跟踪性能指标", category: "性能类", description: "跟踪精度、跟踪距离、同时跟踪目标数", unit: "米/公里", standard: "GJB 74A-1998", equipment: "radar" },
    { id: "r-3", name: "抗干扰指标", category: "适应性类", description: "抗噪声干扰、抗欺骗干扰能力", unit: "dB", standard: "GJB 74A-1998", equipment: "radar" },
    { id: "r-4", name: "可靠性指标", category: "可靠性类", description: "平均故障间隔时间、平均修复时间", unit: "小时", standard: "GJB 899A-2009", equipment: "radar" },
    { id: "r-5", name: "环境适应性指标", category: "适应性类", description: "温度、湿度、振动等环境适应性", unit: "级", standard: "GJB 150A-2009", equipment: "radar" },
  ],
  communication: [
    { id: "c-1", name: "通信性能指标", category: "性能类", description: "通信距离、通信速率、误码率", unit: "公里/Mbps", standard: "GJB 367A-2001", equipment: "communication" },
    { id: "c-2", name: "抗干扰指标", category: "适应性类", description: "抗干扰能力、保密通信能力", unit: "dB", standard: "GJB 367A-2001", equipment: "communication" },
    { id: "c-3", name: "网络性能指标", category: "性能类", description: "网络容量、接入时间、切换成功率", unit: "个/秒/%", standard: "GJB 367A-2001", equipment: "communication" },
    { id: "c-4", name: "可靠性指标", category: "可靠性类", description: "平均故障间隔时间、可用性", unit: "小时/%", standard: "GJB 899A-2009", equipment: "communication" },
    { id: "c-5", name: "环境适应性指标", category: "适应性类", description: "温度、湿度、盐雾等环境适应性", unit: "级", standard: "GJB 150A-2009", equipment: "communication" },
  ],
};

/**
 * 试验方法数据库
 * 从 select_FF 页面的 testMethodsDatabase 提取
 * 关联关系：方法依据于项目+指标
 */
const testMethodsDatabase: TestMethod[] = [
  // ==================== 坦克相关方法 ====================
  // 最大速度相关方法
  {
    id: "method-t-1",
    name: "直线跑道法",
    description: "在标准直线跑道上测试坦克最大速度",
    applicableIndicators: ["t-1", "t-2"],
    applicableProjects: ["t-l-1", "t-hu-1", "t-t-1"],
    standard: "GJB 59.18-2008",
    difficulty: "中等",
    duration: "2小时",
    equipment: ["GPS测速仪", "计时器", "平整跑道"],
    procedure: [
      "准备标准直线测试跑道（长度≥1000米）",
      "坦克预热至正常工作温度",
      "加速至最大速度并保持稳定",
      "使用GPS和计时器记录速度数据",
      "重复3次取平均值",
    ],
    category: "性能测试",
    tags: ["速度测试", "机动性", "基础测试"],
  },
  {
    id: "method-t-2",
    name: "车载测速仪法",
    description: "使用车载测速设备实时监测坦克速度",
    applicableIndicators: ["t-1", "t-2"],
    applicableProjects: ["t-l-1", "t-hu-1", "t-t-1"],
    standard: "GJB 59.18-2008",
    difficulty: "简单",
    duration: "1小时",
    equipment: ["车载测速系统", "数据记录仪"],
    procedure: [
      "安装并校准车载测速系统",
      "在指定路段进行多次测试",
      "记录不同档位下的最高速度",
      "分析加速度和稳定速度曲线",
    ],
    category: "性能测试",
    tags: ["实时监测", "车载设备"],
  },
  // 通信距离相关方法
  {
    id: "method-t-3",
    name: "固定距离通信测试法",
    description: "在固定距离上测试通信系统的有效距离",
    applicableIndicators: ["t-3"],
    applicableProjects: ["t-h-1", "t-l-2", "t-hu-2"],
    standard: "GJB 3841-1999",
    difficulty: "中等",
    duration: "3小时",
    equipment: ["信号发生器", "频谱分析仪", "测试天线"],
    procedure: [
      "设置标准测试距离（1km, 5km, 10km）",
      "发送标准测试信号",
      "接收端测量信号强度和误码率",
      "确定最大有效通信距离",
    ],
    category: "通信测试",
    tags: ["通信距离", "信号测试"],
  },
  {
    id: "method-t-4",
    name: "移动通信测试法",
    description: "在两台移动坦克间测试通信性能",
    applicableIndicators: ["t-3"],
    applicableProjects: ["t-h-1", "t-l-2"],
    standard: "GJB 3841-1999",
    difficulty: "复杂",
    duration: "4小时",
    equipment: ["两辆测试坦克", "GPS定位系统", "数据记录器"],
    procedure: [
      "两辆坦克保持相对运动",
      "测试不同距离下的通信质量",
      "记录地形对通信的影响",
      "分析最大稳定通信距离",
    ],
    category: "通信测试",
    tags: ["移动通信", "实战模拟"],
  },
  // 命中率相关方法
  {
    id: "method-t-5",
    name: "固定靶标射击法",
    description: "对固定靶标进行射击测试命中率",
    applicableIndicators: ["t-5"],
    applicableProjects: ["t-l-2", "t-t-2"],
    standard: "GJB 59.1-2004",
    difficulty: "中等",
    duration: "2.5小时",
    equipment: ["标准靶标", "弹着点分析系统", "射击场"],
    procedure: [
      "设置标准距离的固定靶标",
      "进行多轮射击（每轮10发）",
      "记录弹着点位置",
      "计算命中率和散布精度",
    ],
    category: "火力测试",
    tags: ["射击精度", "固定靶"],
  },
  {
    id: "method-t-6",
    name: "移动靶标射击法",
    description: "对移动靶标进行射击测试动态命中率",
    applicableIndicators: ["t-5"],
    applicableProjects: ["t-l-2", "t-t-2"],
    standard: "GJB 59.1-2004",
    difficulty: "复杂",
    duration: "3小时",
    equipment: ["移动靶标系统", "高速摄像机", "弹道分析仪"],
    procedure: [
      "设置不同速度的移动靶标",
      "测试静止和运动状态下的射击精度",
      "分析瞄准时间和首发命中率",
      "评估火控系统动态性能",
    ],
    category: "火力测试",
    tags: ["动态射击", "实战模拟"],
  },
  // 防护性能相关方法
  {
    id: "method-t-7",
    name: "实弹射击防护测试法",
    description: "使用实弹测试坦克装甲防护能力",
    applicableIndicators: ["t-7"],
    applicableProjects: ["t-t-2"],
    standard: "GJB 59.3-2004",
    difficulty: "复杂",
    duration: "6小时",
    equipment: ["标准弹药", "高速摄像机", "压力传感器"],
    procedure: [
      "按照标准设置射击距离和角度",
      "使用不同口径弹药进行射击",
      "记录装甲变形和穿透情况",
      "评估防护等级",
    ],
    category: "防护测试",
    tags: ["实弹测试", "防护等级"],
  },
  {
    id: "method-t-8",
    name: "非破坏性检测法",
    description: "使用无损检测技术评估防护性能",
    applicableIndicators: ["t-7"],
    applicableProjects: ["t-h-1", "t-l-1", "t-hu-1"],
    standard: "GJB 59.3-2004",
    difficulty: "中等",
    duration: "4小时",
    equipment: ["超声波检测仪", "X射线设备", "红外热像仪"],
    procedure: [
      "使用超声波检测装甲内部结构",
      "X射线扫描检查制造缺陷",
      "红外热像分析材料均匀性",
      "综合评估防护能力",
    ],
    category: "防护测试",
    tags: ["无损检测", "材料分析"],
  },
  // 综合测试方法
  {
    id: "method-t-9",
    name: "复杂环境综合测试法",
    description: "在复杂环境下进行多指标综合测试",
    applicableIndicators: ["t-1", "t-2", "t-3", "t-5"],
    applicableProjects: ["t-h-1", "t-hu-1", "t-t-1"],
    standard: "GJB 150A-2009",
    difficulty: "复杂",
    duration: "8小时",
    equipment: ["综合测试系统", "环境模拟设备", "数据采集中心"],
    procedure: [
      "模拟高温、高湿、沙尘等复合环境",
      "同步测试机动性、通信、火力性能",
      "分析环境因素对各项指标的影响",
      "评估综合环境适应性",
    ],
    category: "综合测试",
    tags: ["复合环境", "综合评估"],
  },
  {
    id: "method-t-10",
    name: "实战化训练测试法",
    description: "在模拟实战环境下测试各项性能指标",
    applicableIndicators: ["t-1", "t-2", "t-3", "t-5", "t-6"],
    applicableProjects: ["t-h-1", "t-l-1", "t-hu-1", "t-t-1"],
    standard: "GJB 59.1-2004",
    difficulty: "复杂",
    duration: "10小时",
    equipment: ["训练场", "对抗系统", "数据记录器"],
    procedure: [
      "设置实战化训练场景",
      "测试机动规避、火力打击、通信联络",
      "记录各项指标的实战表现",
      "评估战斗效能",
    ],
    category: "实战测试",
    tags: ["实战模拟", "综合效能"],
  },
  
  // ==================== 无人机相关方法 ====================
  {
    id: "method-d-1",
    name: "飞行性能测试法",
    description: "测试无人机的最大飞行高度、速度和续航时间",
    applicableIndicators: ["d-1", "d-4"],
    applicableProjects: ["d-h-1", "d-h-2", "d-l-1", "d-l-2"],
    standard: "GJB 5434-2005",
    difficulty: "中等",
    duration: "3小时",
    equipment: ["飞行控制系统", "GPS定位", "遥测设备"],
    procedure: [
      "设置标准测试环境",
      "执行预设飞行任务",
      "记录飞行高度、速度和续航数据",
      "分析飞行性能曲线",
    ],
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
    difficulty: "中等",
    duration: "2小时",
    equipment: ["标准载荷", "称重系统", "平衡测试仪"],
    procedure: [
      "安装不同重量的载荷",
      "测试飞行稳定性和操控性",
      "记录最大载荷限制",
      "评估载荷适应性",
    ],
    category: "能力测试",
    tags: ["载荷能力", "适应性测试"],
  },
  {
    id: "method-d-3",
    name: "环境适应性测试法",
    description: "测试无人机在高低温和湿度环境下的性能",
    applicableIndicators: ["d-3"],
    applicableProjects: ["d-h-1", "d-hu-1", "d-v-1"],
    standard: "GJB 150A-2009",
    difficulty: "复杂",
    duration: "6小时",
    equipment: ["环境模拟箱", "温湿度控制", "性能监测"],
    procedure: [
      "设置目标环境参数",
      "无人机在环境中适应",
      "执行标准测试任务",
      "记录性能变化数据",
    ],
    category: "适应性测试",
    tags: ["环境适应", "可靠性测试"],
  },
  {
    id: "method-d-4",
    name: "安全性能测试法",
    description: "测试无人机的防撞、防坠落和数据加密功能",
    applicableIndicators: ["d-5"],
    applicableProjects: ["d-h-1", "d-l-1", "d-v-1"],
    standard: "GJB 9001C-2017",
    difficulty: "复杂",
    duration: "4小时",
    equipment: ["碰撞测试装置", "坠落测试台", "数据加密测试"],
    procedure: [
      "测试障碍物检测功能",
      "模拟坠落场景",
      "验证数据加密强度",
      "评估安全防护等级",
    ],
    category: "安全测试",
    tags: ["安全性能", "防护测试"],
  },
  {
    id: "method-d-5",
    name: "电池高温测试法",
    description: "测试无人机电池在高温环境下的性能",
    applicableIndicators: ["d-1", "d-4"],
    applicableProjects: ["d-h-3"],
    standard: "GJB 150.3A",
    difficulty: "简单",
    duration: "2小时",
    equipment: ["高温试验箱", "电池测试仪", "数据记录器"],
    procedure: [
      "将电池置于高温环境",
      "测试充放电性能",
      "记录温度对电池的影响",
      "评估电池可靠性",
    ],
    category: "性能测试",
    tags: ["电池测试", "高温测试"],
  },
  
  // ==================== 雷达相关方法 ====================
  {
    id: "method-r-1",
    name: "探测性能测试法",
    description: "测试雷达的探测距离、精度和多目标处理能力",
    applicableIndicators: ["r-1", "r-2"],
    applicableProjects: ["r-h-1", "r-l-1"],
    standard: "GJB 74A-1998",
    difficulty: "复杂",
    duration: "5小时",
    equipment: ["目标模拟器", "信号分析仪", "测试靶标"],
    procedure: [
      "设置不同距离的目标",
      "测试探测概率",
      "分析探测精度",
      "评估多目标处理能力",
    ],
    category: "性能测试",
    tags: ["探测性能", "精度测试"],
  },
  {
    id: "method-r-2",
    name: "抗干扰测试法",
    description: "测试雷达在干扰环境下的抗干扰能力",
    applicableIndicators: ["r-3"],
    applicableProjects: ["r-h-1", "r-l-1"],
    standard: "GJB 74A-1998",
    difficulty: "复杂",
    duration: "4小时",
    equipment: ["干扰发生器", "频谱分析仪", "信号处理器"],
    procedure: [
      "生成各种干扰信号",
      "测试雷达抗干扰性能",
      "分析干扰抑制效果",
      "评估抗干扰等级",
    ],
    category: "适应性测试",
    tags: ["抗干扰", "电子对抗"],
  },
  {
    id: "method-r-3",
    name: "环境适应性测试法",
    description: "测试雷达在温湿度和振动环境下的性能",
    applicableIndicators: ["r-5"],
    applicableProjects: ["r-h-1", "r-hu-1", "r-ra-1"],
    standard: "GJB 150A-2009",
    difficulty: "复杂",
    duration: "8小时",
    equipment: ["环境模拟系统", "振动台", "性能测试仪"],
    procedure: [
      "设置目标环境条件",
      "执行环境适应性测试",
      "记录性能参数变化",
      "评估环境适应能力",
    ],
    category: "适应性测试",
    tags: ["环境适应", "可靠性测试"],
  },
  {
    id: "method-r-4",
    name: "可靠性测试法",
    description: "测试雷达的平均故障间隔时间和修复时间",
    applicableIndicators: ["r-4"],
    applicableProjects: ["r-h-1", "r-l-1", "r-hu-1"],
    standard: "GJB 899A-2009",
    difficulty: "中等",
    duration: "24小时",
    equipment: ["可靠性测试系统", "故障记录仪", "维修工具"],
    procedure: [
      "连续运行雷达系统",
      "记录故障发生时间",
      "统计故障间隔",
      "计算可靠性指标",
    ],
    category: "可靠性测试",
    tags: ["可靠性", "MTBF测试"],
  },
  
  // ==================== 通信设备相关方法 ====================
  {
    id: "method-c-1",
    name: "通信性能测试法",
    description: "测试通信设备的通信距离、速率和误码率",
    applicableIndicators: ["c-1", "c-3"],
    applicableProjects: ["c-h-1", "c-l-1"],
    standard: "GJB 367A-2001",
    difficulty: "中等",
    duration: "4小时",
    equipment: ["信号发生器", "误码仪", "频谱分析仪"],
    procedure: [
      "设置不同通信距离",
      "测试通信质量",
      "记录误码率",
      "评估通信性能",
    ],
    category: "性能测试",
    tags: ["通信性能", "误码率测试"],
  },
  {
    id: "method-c-2",
    name: "抗干扰测试法",
    description: "测试通信设备的抗干扰和保密通信能力",
    applicableIndicators: ["c-2"],
    applicableProjects: ["c-h-1", "c-l-1"],
    standard: "GJB 367A-2001",
    difficulty: "复杂",
    duration: "3小时",
    equipment: ["干扰发生器", "加密测试仪", "信号分析仪"],
    procedure: [
      "生成干扰信号",
      "测试抗干扰性能",
      "验证加密强度",
      "评估保密通信能力",
    ],
    category: "适应性测试",
    tags: ["抗干扰", "保密通信"],
  },
  {
    id: "method-c-3",
    name: "盐雾腐蚀测试法",
    description: "测试通信设备在盐雾环境下的耐腐蚀性",
    applicableIndicators: ["c-5"],
    applicableProjects: ["c-sf-1"],
    standard: "GJB 150.11A",
    difficulty: "中等",
    duration: "72小时",
    equipment: ["盐雾试验箱", "腐蚀检测仪", "外观检查"],
    procedure: [
      "设置盐雾环境参数",
      "放置通信设备",
      "定期检查腐蚀情况",
      "评估耐腐蚀等级",
    ],
    category: "适应性测试",
    tags: ["盐雾测试", "防腐蚀"],
  },
  {
    id: "method-c-4",
    name: "高海拔测试法",
    description: "测试通信设备在低气压高海拔环境下的性能",
    applicableIndicators: ["c-5"],
    applicableProjects: ["c-ha-1"],
    standard: "GJB 150.2A",
    difficulty: "复杂",
    duration: "6小时",
    equipment: ["低压试验箱", "性能测试仪", "数据记录"],
    procedure: [
      "设置低气压环境",
      "测试通信性能",
      "记录设备运行状态",
      "评估高海拔适应性",
    ],
    category: "适应性测试",
    tags: ["高海拔", "低气压测试"],
  },
  {
    id: "method-c-5",
    name: "可靠性测试法",
    description: "测试通信设备的平均故障间隔时间和可用性",
    applicableIndicators: ["c-4"],
    applicableProjects: ["c-h-1", "c-l-1", "c-sf-1"],
    standard: "GJB 899A-2009",
    difficulty: "中等",
    duration: "24小时",
    equipment: ["可靠性测试系统", "故障记录仪", "维修工具"],
    procedure: [
      "连续运行通信设备",
      "记录故障发生情况",
      "统计故障间隔时间",
      "计算可用性指标",
    ],
    category: "可靠性测试",
    tags: ["可靠性", "可用性测试"],
  },
];

/**
 * 考核指标类别颜色
 */
const categoryColors: Record<string, string> = {
  "性能类": "#3b82f6",
  "能力类": "#8b5cf6",
  "适应性类": "#10b981",
  "防护类": "#f59e0b",
  "信息化类": "#6366f1",
  "可靠性类": "#ef4444",
  "安全类": "#d97706",
};

/**
 * 方法分类
 */
const methodCategories = [
  { value: "all", label: "全部方法", color: "#3b82f6" },
  { value: "性能测试", label: "性能测试", color: "#10b981" },
  { value: "通信测试", label: "通信测试", color: "#8b5cf6" },
  { value: "火力测试", label: "火力测试", color: "#ef4444" },
  { value: "防护测试", label: "防护测试", color: "#f59e0b" },
  { value: "综合测试", label: "综合测试", color: "#6366f1" },
  { value: "实战测试", label: "实战测试", color: "#d97706" },
];

// ==================== 主组件 ====================

export default function AssociationQueryPage() {
  const router = useRouter();
  
  // ==================== 状态定义 ====================
  
  /**
   * 左侧设备树相关状态
   */
  const [equipmentTreeData, setEquipmentTreeData] = useState<EquipmentTreeNode[]>([]);
  const [selectedEquipmentKeys, setSelectedEquipmentKeys] = useState<string[]>([]);
  const [equipmentSearchText, setEquipmentSearchText] = useState("");
  
  /**
   * 中间选择区域相关状态
   */
  // 环境选择
  const [selectedEnvironments, setSelectedEnvironments] = useState<string[]>([]);
  const [environmentSearchText, setEnvironmentSearchText] = useState("");
  
  // 项目选择（树状结构）
  const [availableProjects, setAvailableProjects] = useState<ProjectTreeNode[]>([]);
  const [selectedProjects, setSelectedProjects] = useState<string[]>([]);
  const [projectSearchText, setProjectSearchText] = useState("");
  
  // 指标选择
  const [availableIndicators, setAvailableIndicators] = useState<AssessmentIndicator[]>([]);
  const [selectedIndicators, setSelectedIndicators] = useState<string[]>([]);
  const [indicatorSearchText, setIndicatorSearchText] = useState("");
  
  /**
   * 右侧方法列表相关状态
   */
  const [filteredMethods, setFilteredMethods] = useState<TestMethod[]>(testMethodsDatabase);
  const [selectedMethods, setSelectedMethods] = useState<string[]>([]);
  const [methodSearchText, setMethodSearchText] = useState("");
  const [selectedMethodCategory, setSelectedMethodCategory] = useState("all");
  
  /**
   * 确认选择状态
   */
  const [environmentsConfirmed, setEnvironmentsConfirmed] = useState(false);
  const [projectsConfirmed, setProjectsConfirmed] = useState(false);
  const [indicatorsConfirmed, setIndicatorsConfirmed] = useState(false);
  
  // ==================== 初始化 ====================
  
  /**
   * 初始化设备树数据
   * 构建三层树结构：设备分类 → 设备类型 → 具体设备
   */
  useEffect(() => {
    buildEquipmentTree();
  }, []);
  
  /**
   * 根据选择的设备类型更新可用的环境、项目和指标
   */
  useEffect(() => {
    if (selectedEquipmentKeys.length > 0) {
      // 获取选中的设备类型
      const selectedTypes = new Set<string>();
      
      // 遍历所有选中的keys，提取设备类型
      selectedEquipmentKeys.forEach(key => {
        // 首先检查是否是设备分类节点
        const categoryNode = equipmentCategories.find(cat => cat.value === key);
        if (categoryNode) {
          // 如果是分类节点，添加该分类下的所有设备类型
          equipmentTypes
            .filter(type => type.category === key)
            .forEach(type => selectedTypes.add(type.value));
          return;
        }
        
        // 检查是否是设备类型节点
        const typeNode = equipmentTypes.find(type => type.value === key);
        if (typeNode) {
          selectedTypes.add(key);
          return;
        }
        
        // 如果是具体设备节点，找到其对应的设备类型
        const equipmentType = equipmentTypes.find(et => 
          specificEquipment[et.value]?.some(e => e.id === key)
        );
        if (equipmentType) {
          selectedTypes.add(equipmentType.value);
        }
      });
      
      console.log('选中的设备类型:', Array.from(selectedTypes));
      
      // 更新可用环境（所有环境都可用）
      setSelectedEnvironments([]);
      setEnvironmentsConfirmed(false);
      
      // 更新可用指标（根据选中的设备类型）
      const allIndicators: AssessmentIndicator[] = [];
      selectedTypes.forEach(type => {
        const indicators = assessmentIndicatorsData[type] || [];
        allIndicators.push(...indicators);
      });
      console.log('可用指标数量:', allIndicators.length);
      setAvailableIndicators(allIndicators);
      setSelectedIndicators([]);
      setIndicatorsConfirmed(false);
      
      // 更新可用项目（需要设备+环境组合）
      setAvailableProjects([]);
      setSelectedProjects([]);
      setProjectsConfirmed(false);
    } else {
      setAvailableIndicators([]);
      setSelectedIndicators([]);
      setAvailableProjects([]);
      setSelectedProjects([]);
    }
  }, [selectedEquipmentKeys]);
  
  /**
   * 根据选择的环境更新可用项目
   */
  useEffect(() => {
    if (selectedEquipmentKeys.length > 0 && selectedEnvironments.length > 0 && environmentsConfirmed) {
      const allProjects: TestProject[] = [];
      
      // 获取选中的设备类型
      const selectedTypes = new Set<string>();
      selectedEquipmentKeys.forEach(key => {
        // 首先检查是否是设备分类节点
        const categoryNode = equipmentCategories.find(cat => cat.value === key);
        if (categoryNode) {
          equipmentTypes
            .filter(type => type.category === key)
            .forEach(type => selectedTypes.add(type.value));
          return;
        }
        
        // 检查是否是设备类型节点
        const typeNode = equipmentTypes.find(type => type.value === key);
        if (typeNode) {
          selectedTypes.add(key);
          return;
        }
        
        // 如果是具体设备节点，找到其对应的设备类型
        const equipmentType = equipmentTypes.find(et => 
          specificEquipment[et.value]?.some(e => e.id === key)
        );
        if (equipmentType) {
          selectedTypes.add(equipmentType.value);
        }
      });
      
      console.log('选中的设备类型用于项目:', Array.from(selectedTypes));
      console.log('选中的环境:', selectedEnvironments);
      
      // 根据设备类型和环境获取项目（树状结构）
      const projectTreeNodes: ProjectTreeNode[] = [];
      selectedTypes.forEach(type => {
        selectedEnvironments.forEach(env => {
          const projects = testProjectsData[type]?.[env] || [];
          projects.forEach((categoryNode: any) => {
            // 转换为 ProjectTreeNode 格式
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
      
      console.log('项目树节点数:', projectTreeNodes.length);
      setAvailableProjects(projectTreeNodes);
      setSelectedProjects([]);
      setProjectsConfirmed(false);
    } else {
      setAvailableProjects([]);
      setSelectedProjects([]);
    }
  }, [selectedEnvironments, selectedEquipmentKeys, environmentsConfirmed]);
  
  /**
   * 根据选择筛选方法
   */
  useEffect(() => {
    filterMethods();
  }, [selectedIndicators, selectedProjects, selectedMethodCategory, methodSearchText, indicatorsConfirmed, projectsConfirmed]);
  
  // ==================== 核心函数 ====================
  
  /**
   * 构建设备树数据结构
   */
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
  
  /**
   * 筛选方法
   * 关联关系：方法依据于项目+指标
   * 最开始方法列表为空，只有选择完项目和指标后才显示关联的方法
   */
  const filterMethods = () => {
    // 如果项目或指标没有确认选择，方法列表为空
    if (!projectsConfirmed || !indicatorsConfirmed) {
      setFilteredMethods([]);
      return;
    }
    
    // 如果项目和指标都确认了，但没有选择任何项目或指标，方法列表也为空
    if (selectedProjects.length === 0 && selectedIndicators.length === 0) {
      setFilteredMethods([]);
      return;
    }
    
    let filtered = [...testMethodsDatabase];
    
    // 根据指标和项目筛选（匹配任一条件即可显示）
    if (selectedIndicators.length > 0 || selectedProjects.length > 0) {
      filtered = filtered.filter(method => {
        // 如果选择了指标，检查方法是否匹配任一选中的指标
        const matchesIndicator = selectedIndicators.length === 0 || 
          method.applicableIndicators.some(indicator =>
            selectedIndicators.includes(indicator)
          );
        
        // 如果选择了项目，检查方法是否匹配任一选中的项目
        const matchesProject = selectedProjects.length === 0 || 
          method.applicableProjects.some(project =>
            selectedProjects.includes(project)
          );
        
        // 方法匹配任一条件即可显示
        return matchesIndicator || matchesProject;
      });
    }
    
    // 根据类别筛选
    if (selectedMethodCategory !== "all") {
      filtered = filtered.filter(method =>
        method.category === selectedMethodCategory
      );
    }
    
    // 根据关键词筛选
    if (methodSearchText) {
      const keyword = methodSearchText.toLowerCase();
      filtered = filtered.filter(method =>
        method.name.toLowerCase().includes(keyword) ||
        method.description.toLowerCase().includes(keyword) ||
        method.tags.some(tag => tag.toLowerCase().includes(keyword))
      );
    }
    
    setFilteredMethods(filtered);
  };
  
  /**
   * 处理设备树选择
   * 实现选父级自动选中所有子级功能
   */
  const handleEquipmentSelect = (selectedKeys: React.Key[], info: any) => {
    // 安全地获取 info 对象的属性
    const checked = info?.checked;
    const node = info?.node;
    
    // 如果 info 结构不正确或没有 node，直接使用 selectedKeys
    if (!node || !node.key) {
      setSelectedEquipmentKeys(selectedKeys as string[]);
      return;
    }
    
    // 递归获取所有子节点key
    const getAllChildKeys = (nodeData: any): string[] => {
      const keys: string[] = [];
      if (nodeData && nodeData.children && Array.isArray(nodeData.children)) {
        for (const child of nodeData.children) {
          if (child && child.key) {
            keys.push(child.key as string);
            // 递归获取子节点的子节点
            const childKeys = getAllChildKeys(child);
            if (Array.isArray(childKeys)) {
              keys.push(...childKeys);
            }
          }
        }
      }
      return keys;
    };
    
    // 找到节点
    const findNodeByKey = (treeData: any[], key: string): any => {
      if (!treeData || !Array.isArray(treeData)) return null;
      for (const treeNode of treeData) {
        if (treeNode && treeNode.key === key) return treeNode;
        if (treeNode && treeNode.children && Array.isArray(treeNode.children)) {
          const found = findNodeByKey(treeNode.children, key);
          if (found) return found;
        }
      }
      return null;
    };
    
    // 如果是选中操作
    if (checked) {
      const allKeys = new Set<string>(selectedKeys as string[]);
      
      // 获取当前选中节点的所有子节点
      const currentNode = findNodeByKey(equipmentTreeData, node.key as string);
      if (currentNode) {
        const childKeys = getAllChildKeys(currentNode);
        if (Array.isArray(childKeys)) {
          for (const key of childKeys) {
            allKeys.add(key);
          }
        }
      }
      
      setSelectedEquipmentKeys(Array.from(allKeys));
    } else {
      // 如果是取消选中操作
      const allKeys = new Set<string>(selectedKeys as string[]);
      
      // 获取当前节点的所有子节点并移除
      const currentNode = findNodeByKey(equipmentTreeData, node.key as string);
      if (currentNode) {
        const childKeys = getAllChildKeys(currentNode);
        if (Array.isArray(childKeys)) {
          for (const key of childKeys) {
            allKeys.delete(key);
          }
        }
      }
      
      setSelectedEquipmentKeys(Array.from(allKeys));
    }
  };
  
  /**
   * 处理设备树搜索
   */
  const handleEquipmentSearch = (value: string) => {
    setEquipmentSearchText(value);
    
    if (!value) {
      buildEquipmentTree();
      return;
    }
    
    // 模糊搜索设备
    const searchText = value.toLowerCase();
    const filteredTree: EquipmentTreeNode[] = [];
    
    equipmentCategories.forEach(category => {
      const categoryNode: EquipmentTreeNode = {
        title: category.label,
        key: category.value,
        children: [],
      };
      
      equipmentTypes
        .filter(type => type.category === category.value)
        .forEach(type => {
          const equipmentList = specificEquipment[type.value] || [];
          const filteredEquipment = equipmentList.filter(eq =>
            eq.name.toLowerCase().includes(searchText) ||
            eq.description?.toLowerCase().includes(searchText)
          );
          
          if (filteredEquipment.length > 0 || type.label.toLowerCase().includes(searchText)) {
            categoryNode.children?.push({
              title: type.label,
              key: type.value,
              children: filteredEquipment.map(eq => ({
                title: eq.name,
                key: eq.id,
                isLeaf: true,
                equipment: eq,
              })),
            });
          }
        });
      
      if (categoryNode.children && categoryNode.children.length > 0) {
        filteredTree.push(categoryNode);
      }
    });
    
    setEquipmentTreeData(filteredTree);
  };
  
  /**
   * 处理环境选择
   */
  const handleEnvironmentSelect = (values: string[]) => {
    setSelectedEnvironments(values);
    setEnvironmentsConfirmed(false);
  };
  
  /**
   * 确认环境选择
   */
  const confirmEnvironmentSelection = () => {
    if (selectedEnvironments.length === 0) {
      message.warning("请至少选择一个环境");
      return;
    }
    setEnvironmentsConfirmed(true);
    message.success(`已确认选择 ${selectedEnvironments.length} 个环境`);
  };
  
  /**
   * 处理项目选择
   */
  const handleProjectSelect = (values: string[]) => {
    setSelectedProjects(values);
    setProjectsConfirmed(false);
  };
  
  /**
   * 确认项目选择
   */
  const confirmProjectSelection = () => {
    if (selectedProjects.length === 0) {
      message.warning("请至少选择一个项目");
      return;
    }
    setProjectsConfirmed(true);
    message.success(`已确认选择 ${selectedProjects.length} 个项目`);
  };
  
  /**
   * 处理指标选择
   */
  const handleIndicatorSelect = (values: string[]) => {
    setSelectedIndicators(values);
    setIndicatorsConfirmed(false);
  };
  
  /**
   * 确认指标选择
   */
  const confirmIndicatorSelection = () => {
    if (selectedIndicators.length === 0) {
      message.warning("请至少选择一个指标");
      return;
    }
    setIndicatorsConfirmed(true);
    message.success(`已确认选择 ${selectedIndicators.length} 个指标`);
  };
  
  /**
   * 保存选择的方法
   */
  const handleSaveSelectedMethods = () => {
    if (selectedMethods.length === 0) {
      message.warning("请至少选择一个试验方法");
      return;
    }
    
    // 获取选中的方法详情
    const selectedMethodDetails = testMethodsDatabase.filter(method =>
      selectedMethods.includes(method.id)
    );
    
    // 构建保存的数据
    const selectionData = {
      equipment: selectedEquipmentKeys,
      environments: selectedEnvironments,
      projects: selectedProjects,
      indicators: selectedIndicators,
      methods: selectedMethods,
      methodDetails: selectedMethodDetails,
      timestamp: new Date().toISOString(),
    };
    
    // 保存到sessionStorage
    sessionStorage.setItem('associationQuerySelection', JSON.stringify(selectionData));
    
    message.success(`已成功保存 ${selectedMethods.length} 个试验方法！`);
  };
  
  /**
   * 重置所有选择
   */
  const handleResetAll = () => {
    setSelectedEquipmentKeys([]);
    setSelectedEnvironments([]);
    setSelectedProjects([]);
    setSelectedIndicators([]);
    setSelectedMethods([]);
    setEnvironmentsConfirmed(false);
    setProjectsConfirmed(false);
    setIndicatorsConfirmed(false);
    setEquipmentSearchText("");
    setEnvironmentSearchText("");
    setProjectSearchText("");
    setIndicatorSearchText("");
    setMethodSearchText("");
    setSelectedMethodCategory("all");
    buildEquipmentTree();
    message.success("已重置所有选择");
  };
  
  /**
   * 全选/取消全选指标
   */
  const handleSelectAllIndicators = () => {
    if (selectedIndicators.length === availableIndicators.length) {
      setSelectedIndicators([]);
    } else {
      setSelectedIndicators(availableIndicators.map(i => i.id));
    }
  };
  
  /**
   * 全选/取消全选项目
   */
  const handleSelectAllProjects = () => {
    // 获取所有叶子节点的 key
    const getAllLeafKeys = (nodes: ProjectTreeNode[]): string[] => {
      const keys: string[] = [];
      nodes.forEach(node => {
        if (node.isLeaf) {
          keys.push(node.key);
        } else if (node.children) {
          keys.push(...getAllLeafKeys(node.children));
        }
      });
      return keys;
    };
    
    const allLeafKeys = getAllLeafKeys(availableProjects);
    if (selectedProjects.length === allLeafKeys.length) {
      setSelectedProjects([]);
    } else {
      setSelectedProjects(allLeafKeys);
    }
  };
  
  /**
   * 全选/取消全选环境
   */
  const handleSelectAllEnvironments = () => {
    if (selectedEnvironments.length === environmentOptions.length) {
      setSelectedEnvironments([]);
    } else {
      setSelectedEnvironments(environmentOptions.map(e => e.value));
    }
  };
  
  // ==================== 渲染函数 ====================
  
  /**
   * 渲染左侧设备树区域
   */
  const renderEquipmentTreePanel = () => (
    <Card
      title={
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <ApartmentOutlined style={{ color: "#3b82f6" }} />
          <span>设备选择</span>
          <Badge count={selectedEquipmentKeys.length} style={{ backgroundColor: "#3b82f6" }} />
        </div>
      }
      size="small"
      style={{ height: "100%" }}
      extra={
        <Button
          size="small"
          icon={<ClearOutlined />}
          onClick={() => {
            setSelectedEquipmentKeys([]);
            setEquipmentSearchText("");
            buildEquipmentTree();
          }}
        >
          清除
        </Button>
      }
    >
      {/* 搜索框 */}
      <Input
        placeholder="搜索设备（模糊查询）"
        value={equipmentSearchText}
        onChange={(e) => handleEquipmentSearch(e.target.value)}
        prefix={<SearchOutlined />}
        allowClear
        style={{ marginBottom: 12 }}
      />
      
      {/* 设备树 */}
      <div style={{ height: "calc(100vh - 280px)", overflow: "auto" }}>
        {equipmentTreeData.length > 0 ? (
          <Tree
            checkable
            checkedKeys={selectedEquipmentKeys}
            onCheck={(keys: any) => {
              // 当不使用 checkStrictly 时，keys 直接是选中的 key 数组
              setSelectedEquipmentKeys(keys as string[]);
            }}
            treeData={equipmentTreeData}
            defaultExpandAll
            showLine={{ showLeafIcon: false }}
            style={{ fontSize: 13 }}
          />
        ) : (
          <Empty description="未找到匹配的设备" />
        )}
      </div>
    </Card>
  );
  
  /**
   * 渲染中间选择区域
   */
  const renderMiddlePanel = () => (
    <div style={{ display: "flex", flexDirection: "column", gap: 16, height: "100%" }}>
      {/* 第一层：环境列表 */}
      <Card
        title={
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <EnvironmentOutlined style={{ color: "#10b981" }} />
            <span>环境选择</span>
            <Tag color={environmentsConfirmed ? "green" : "orange"}>
              {environmentsConfirmed ? "已确认" : "未确认"}
            </Tag>
            <Badge count={selectedEnvironments.length} style={{ backgroundColor: "#10b981" }} />
          </div>
        }
        size="small"
        extra={
          <Space>
            <Button
              size="small"
              onClick={handleSelectAllEnvironments}
            >
              {selectedEnvironments.length === environmentOptions.length ? "取消全选" : "全选"}
            </Button>
            <Button
              size="small"
              type="primary"
              onClick={confirmEnvironmentSelection}
              disabled={selectedEnvironments.length === 0}
              icon={<CheckCircleOutlined />}
            >
              确认
            </Button>
          </Space>
        }
        style={{ flex: 1 }}
      >
        <Input
          placeholder="搜索环境"
          value={environmentSearchText}
          onChange={(e) => setEnvironmentSearchText(e.target.value)}
          prefix={<SearchOutlined />}
          allowClear
          style={{ marginBottom: 8 }}
        />
        
        <Checkbox.Group
          style={{ width: "100%" }}
          value={selectedEnvironments}
          onChange={handleEnvironmentSelect}
        >
          <Row gutter={[8, 8]}>
            {environmentOptions
              .filter(env => 
                !environmentSearchText || 
                env.label.toLowerCase().includes(environmentSearchText.toLowerCase())
              )
              .map(env => (
                <Col xs={24} sm={12} key={env.value}>
                  <Checkbox value={env.value}>
                    <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
                      <span>{env.icon}</span>
                      <Text style={{ fontSize: 12 }}>{env.label}</Text>
                    </div>
                  </Checkbox>
                </Col>
              ))}
          </Row>
        </Checkbox.Group>
      </Card>
      
      {/* 第二层：项目 */}
      <Card
        title={
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <ExperimentOutlined style={{ color: "#8b5cf6" }} />
            <span>项目选择</span>
            <Tag color={projectsConfirmed ? "green" : "orange"}>
              {projectsConfirmed ? "已确认" : "未确认"}
            </Tag>
            <Badge count={selectedProjects.length} style={{ backgroundColor: "#8b5cf6" }} />
          </div>
        }
        size="small"
        extra={
          <Space>
            <Button
              size="small"
              onClick={handleSelectAllProjects}
              disabled={availableProjects.length === 0}
            >
              {selectedProjects.length === availableProjects.length ? "取消全选" : "全选"}
            </Button>
            <Button
              size="small"
              type="primary"
              onClick={confirmProjectSelection}
              disabled={selectedProjects.length === 0}
              icon={<CheckCircleOutlined />}
            >
              确认
            </Button>
          </Space>
        }
        style={{ flex: 1 }}
      >
        <Input
          placeholder="搜索项目"
          value={projectSearchText}
          onChange={(e) => setProjectSearchText(e.target.value)}
          prefix={<SearchOutlined />}
          allowClear
          style={{ marginBottom: 8 }}
          disabled={!environmentsConfirmed}
        />
        
        {!environmentsConfirmed ? (
          <div style={{ textAlign: "center", padding: 20, color: "#999" }}>
            请先确认环境选择
          </div>
        ) : availableProjects.length === 0 ? (
          <Empty description="暂无可用项目" />
        ) : (
          <Tree
            checkable
            defaultExpandAll
            checkedKeys={selectedProjects}
            onCheck={(keys: any) => {
              setSelectedProjects(keys);
              setProjectsConfirmed(false);
            }}
            treeData={availableProjects}
            style={{ fontSize: 12 }}
          />
        )}
      </Card>
      
      {/* 第三层：指标 */}
      <Card
        title={
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <BarChartOutlined style={{ color: "#f59e0b" }} />
            <span>指标选择</span>
            <Tag color={indicatorsConfirmed ? "green" : "orange"}>
              {indicatorsConfirmed ? "已确认" : "未确认"}
            </Tag>
            <Badge count={selectedIndicators.length} style={{ backgroundColor: "#f59e0b" }} />
          </div>
        }
        size="small"
        extra={
          <Space>
            <Button
              size="small"
              onClick={handleSelectAllIndicators}
              disabled={availableIndicators.length === 0}
            >
              {selectedIndicators.length === availableIndicators.length ? "取消全选" : "全选"}
            </Button>
            <Button
              size="small"
              type="primary"
              onClick={confirmIndicatorSelection}
              disabled={selectedIndicators.length === 0}
              icon={<CheckCircleOutlined />}
            >
              确认
            </Button>
          </Space>
        }
        style={{ flex: 1 }}
      >
        <Input
          placeholder="搜索指标"
          value={indicatorSearchText}
          onChange={(e) => setIndicatorSearchText(e.target.value)}
          prefix={<SearchOutlined />}
          allowClear
          style={{ marginBottom: 8 }}
        />
        
        {availableIndicators.length === 0 ? (
          <Empty description="请先选择设备" />
        ) : (
          <Checkbox.Group
            style={{ width: "100%" }}
            value={selectedIndicators}
            onChange={handleIndicatorSelect}
          >
            <Row gutter={[8, 8]}>
              {availableIndicators
                .filter(indicator =>
                  !indicatorSearchText ||
                  indicator.name.toLowerCase().includes(indicatorSearchText.toLowerCase())
                )
                .map(indicator => (
                  <Col xs={24} key={indicator.id}>
                    <Checkbox value={indicator.id}>
                      <div>
                        <Text strong style={{ fontSize: 12 }}>{indicator.name}</Text>
                        <div>
                          <Tag color={categoryColors[indicator.category] || "#6b7280"} style={{ fontSize: 10 }}>
                            {indicator.category}
                          </Tag>
                          {indicator.unit && (
                            <Tag color="blue" style={{ fontSize: 10 }}>单位: {indicator.unit}</Tag>
                          )}
                        </div>
                      </div>
                    </Checkbox>
                  </Col>
                ))}
            </Row>
          </Checkbox.Group>
        )}
      </Card>
    </div>
  );
  
  /**
   * 渲染右侧方法列表区域
   */
  const renderMethodPanel = () => (
    <Card
      title={
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <FileTextOutlined style={{ color: "#6366f1" }} />
          <span>关联方法列表</span>
          <Badge count={filteredMethods.length} style={{ backgroundColor: "#6366f1" }} />
        </div>
      }
      size="small"
      extra={
        <Button
          type="primary"
          onClick={handleSaveSelectedMethods}
          disabled={selectedMethods.length === 0}
          icon={<CheckCircleOutlined />}
        >
          保存选择 ({selectedMethods.length})
        </Button>
      }
      style={{ height: "100%", display: "flex", flexDirection: "column" }}
      bodyStyle={{ flex: 1, overflow: "hidden", display: "flex", flexDirection: "column" }}
    >
      {/* 筛选区域 */}
      <div style={{ marginBottom: 12 }}>
        <Row gutter={[8, 8]}>
          <Col span={16}>
            <Input
              placeholder="搜索方法名称、描述或标签"
              value={methodSearchText}
              onChange={(e) => setMethodSearchText(e.target.value)}
              prefix={<SearchOutlined />}
              allowClear
            />
          </Col>
          <Col span={8}>
            <Select
              value={selectedMethodCategory}
              onChange={setSelectedMethodCategory}
              style={{ width: "100%" }}
            >
              {methodCategories.map(cat => (
                <Option key={cat.value} value={cat.value}>
                  <Tag color={cat.color} style={{ border: "none" }}>{cat.label}</Tag>
                </Option>
              ))}
            </Select>
          </Col>
        </Row>
      </div>
      
      <Divider style={{ margin: "12px 0" }} />
      
      {/* 方法列表 - 填满剩余空间 */}
      <div style={{ flex: 1, overflow: "auto" }}>
        {!projectsConfirmed || !indicatorsConfirmed ? (
          <Empty 
            description={
              <div style={{ textAlign: "center" }}>
                <div style={{ fontSize: 16, color: "#666", marginBottom: 8 }}>
                  请先完成以下选择
                </div>
                <div style={{ fontSize: 14, color: "#999" }}>
                  <div>1. 选择设备</div>
                  <div>2. 选择环境并确认</div>
                  <div>3. 选择项目并确认</div>
                  <div>4. 选择指标并确认</div>
                </div>
                <div style={{ marginTop: 16, fontSize: 14, color: "#666" }}>
                  完成后将自动显示关联的试验方法
                </div>
              </div>
            }
            image={Empty.PRESENTED_IMAGE_SIMPLE}
          />
        ) : filteredMethods.length === 0 ? (
          <Empty 
            description="未找到匹配的试验方法"
            image={Empty.PRESENTED_IMAGE_SIMPLE}
          >
            <Button onClick={handleResetAll}>重置所有选择</Button>
          </Empty>
        ) : (
          <List
            dataSource={filteredMethods}
            renderItem={method => (
              <List.Item
                key={method.id}
                style={{
                  border: selectedMethods.includes(method.id)
                    ? "2px solid #3b82f6"
                    : "1px solid #e5e7eb",
                  borderRadius: 8,
                  padding: 12,
                  marginBottom: 8,
                  background: selectedMethods.includes(method.id)
                    ? "rgba(59, 130, 246, 0.05)"
                    : "#fff",
                }}
              >
                <div style={{ width: "100%" }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
                    <div style={{ flex: 1 }}>
                      <Checkbox
                        checked={selectedMethods.includes(method.id)}
                        onChange={(e) => {
                          if (e.target.checked) {
                            setSelectedMethods([...selectedMethods, method.id]);
                          } else {
                            setSelectedMethods(selectedMethods.filter(id => id !== method.id));
                          }
                        }}
                      >
                        <Text strong style={{ fontSize: 14 }}>{method.name}</Text>
                      </Checkbox>
                      <Tag color={methodCategories.find(c => c.value === method.category)?.color} style={{ marginLeft: 8 }}>
                        {method.category}
                      </Tag>
                    </div>
                  </div>
                  
                  <Paragraph
                    type="secondary"
                    style={{ fontSize: 12, margin: "8px 0", lineHeight: 1.4 }}
                    ellipsis={{ rows: 2 }}
                  >
                    {method.description}
                  </Paragraph>
                  
                  {/* 标准链接 */}
                  <div style={{ marginBottom: 8 }}>
                    <Text strong style={{ fontSize: 11, color: "#666" }}>标准来源：</Text>
                    <a
                      href={`/standard/${method.standard}`}
                      onClick={(e) => {
                        e.preventDefault();
                        router.push(`/standard/${method.standard}`);
                      }}
                      style={{ fontSize: 11 }}
                    >
                      <LinkOutlined style={{ marginRight: 4 }} />
                      {method.standard}
                    </a>
                  </div>
                  
                  {/* 标签 */}
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
                    {method.tags.slice(0, 3).map(tag => (
                      <Tag key={tag} style={{ fontSize: 10, marginBottom: 0 }}>{tag}</Tag>
                    ))}
                  </div>
                </div>
              </List.Item>
            )}
          />
        )}
      </div>
    </Card>
  );
  
  // ==================== 主渲染 ====================
  
  return (
    <div style={{
      display: "flex",
      minHeight: "100vh",
      background: "linear-gradient(135deg, #f0f7ff 0%, #e6f0ff 100%)",
      position: "relative",
      overflow: "hidden",
    }}>
      {/* 主内容区 */}
      <div
        style={{
          flex: 1,
          padding: "24px",
          position: "relative",
          zIndex: 5,
          maxWidth: "calc(100vw - 280px)",
        }}
      >
        <div style={{
          display: "flex",
          flexDirection: "column",
          gap: 20,
          maxWidth: 1800,
          margin: "0 auto",
          width: "100%",
        }}>
          
          
          {/* 重置按钮 - 横跨三栏 */}
          <Button
            icon={<ReloadOutlined />}
            onClick={handleResetAll}
            size="large"
            style={{ 
              width: "100%", 
              height: 56,
              fontSize: 16,
              fontWeight: 600,
              marginBottom: 8
            }}
          >
            重置所有选择
          </Button>
          
          {/* 三栏布局 - 中间最宽 */}
          <Row gutter={[16, 16]} style={{ flex: 1 }}>
            {/* 左侧：设备树 */}
            <Col xs={24} lg={6}>
              {renderEquipmentTreePanel()}
            </Col>
            
            {/* 中间：三层选择 */}
            <Col xs={24} lg={12}>
              {renderMiddlePanel()}
            </Col>
            
            {/* 右侧：方法列表 */}
            <Col xs={24} lg={6}>
              {renderMethodPanel()}
            </Col>
          </Row>
        </div>
      </div>
    </div>
  );
}