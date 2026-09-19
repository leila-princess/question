import type { MenuProps } from 'antd';
import {
  HomeOutlined,
  SearchOutlined,
  MessageOutlined,
  ExperimentOutlined,
  BookOutlined,
  DatabaseOutlined,
  DashboardOutlined,
  TeamOutlined,
  SettingOutlined,
  AimOutlined,
  ToolOutlined,
  RobotOutlined,
  ScanOutlined,
  PieChartOutlined,
  CloudServerOutlined,
  TagsOutlined,
  CloudUploadOutlined,
  QuestionCircleOutlined,
  FileTextOutlined,
} from '@ant-design/icons';

export interface PageInfo {
  key: string;
  path: string;
  title: string;
  description: string;
  menuLabel: string;
  icon?: string;
  showInMenu: boolean;
}

export const pageConfig: PageInfo[] = [
  {
    key: 'home',
    path: '/',
    title: '首页',
    description: '系统概览与快速入口',
    menuLabel: '首页',
    icon: 'HomeOutlined',
    showInMenu: true,
  },
  {
    key: 'GLCX',
    path: '/GLCX',
    title: '关联查询',
    description: '多维度关联查询与数据检索',
    menuLabel: '关联查询',
    icon: 'SearchOutlined',
    showInMenu: true,
  },
  {
    key: 'JQCX',
    path: '/GLCX/JQCX',
    title: '精确查询',
    description: '基于知识图谱的精确条件数据查询',
    menuLabel: '精确查询',
    icon: 'AimOutlined',
    showInMenu: false,
  },
  {
    key: 'SSYQ',
    path: '/GLCX/SSYQ',
    title: '搜索引擎',
    description: '全文检索与智能搜索',
    menuLabel: '搜索引擎',
    icon: 'SearchOutlined',
    showInMenu: false,
  },
  {
    key: 'TPCX',
    path: '/GLCX/TPCX',
    title: '图谱查询',
    description: '知识图谱可视化查询与探索',
    menuLabel: '图谱查询',
    icon: 'ApartmentOutlined',
    showInMenu: false,
  },
  {
    key: 'ZNWD',
    path: '/GLCX/ZNWD',
    title: '智能问答',
    description: '基于图数据库与知识库双路检索的 AI 智能问答',
    menuLabel: '智能问答',
    icon: 'MessageOutlined',
    showInMenu: false,
  },
  {
    key: 'FZSJ',
    path: '/FZSJ',
    title: '辅助设计',
    description: '智能化试验方案辅助设计',
    menuLabel: '辅助设计',
    icon: 'ExperimentOutlined',
    showInMenu: true,
  },
  {
    key: 'JYGZ',
    path: '/FZSJ/JYGZ',
    title: '基于规则的辅助设计',
    description: '基于预定义规则的试验方案生成',
    menuLabel: '基于规则的辅助设计',
    icon: 'ToolOutlined',
    showInMenu: false,
  },
  {
    key: 'JYLLM',
    path: '/FZSJ/JYLLM',
    title: '基于大模型的辅助设计',
    description: 'AI 大模型驱动的智能方案设计',
    menuLabel: '基于大模型的辅助设计',
    icon: 'RobotOutlined',
    showInMenu: false,
  },
  {
    key: 'FLGL',
    path: '/FLGL',
    title: '分类管理',
    description: '试验标准与项目分类管理',
    menuLabel: '分类管理',
    icon: 'BookOutlined',
    showInMenu: true,
  },
  {
    key: 'ZSGL',
    path: '/ZSGL',
    title: '知识管理',
    description: '知识图谱构建、维护与可视化管理',
    menuLabel: '知识管理',
    icon: 'DatabaseOutlined',
    showInMenu: true,
  },
  {
    key: 'WJGL',
    path: '/ZSGL/WJGL',
    title: '文件管理',
    description: '系统内文件管理',
    menuLabel: '文件管理',
    icon: 'DatabaseOutlined',
    showInMenu: true,
  },
  {
    key: 'WJGL1',
    path: '/ZSGL/WJGL1',
    title: '文件管理（增强版）',
    description: '支持 PDF 增强提取，含表格图片',
    menuLabel: '文件管理（增强版）',
    icon: 'ExperimentOutlined',
    showInMenu: true,
  },
  {
    key: 'Standards',
    path: '/Standards',
    title: '标准阅读',
    description: '从文件管理打开的文档预览',
    menuLabel: '标准阅读',
    icon: 'FileTextOutlined',
    showInMenu: false,
  },
  {
    key: 'ZSTQ',
    path: '/ZSGL/ZSTQ',
    title: '知识提取',
    description: '从文档中自动提取知识并构建图谱',
    menuLabel: '知识提取',
    icon: 'ScanOutlined',
    showInMenu: false,
  },
  {
    key: 'ZSTPZS',
    path: '/ZSGL/ZSTPZS',
    title: '图谱展示',
    description: '知识图谱可视化展示与交互探索',
    menuLabel: '图谱展示',
    icon: 'PieChartOutlined',
    showInMenu: false,
  },
  {
    key: 'XLSJK',
    path: '/ZSGL/XLSJK',
    title: '向量数据库',
    description: '向量数据库管理与文档检索',
    menuLabel: '向量数据库',
    icon: 'CloudServerOutlined',
    showInMenu: false,
  },
  {
    key: 'BQGL',
    path: '/ZSGL/BQGL',
    title: '标签管理',
    description: '知识节点标签的分类与管理',
    menuLabel: '标签管理',
    icon: 'TagsOutlined',
    showInMenu: false,
  },
  {
    key: 'KSHZS',
    path: '/KSHZS',
    title: '可视化展示',
    description: '数据可视化与图表分析',
    menuLabel: '可视化展示',
    icon: 'DashboardOutlined',
    showInMenu: true,
  },
  {
    key: 'XTGL',
    path: '/XTGL',
    title: '系统设置',
    description: '系统参数与全局配置',
    menuLabel: '系统设置',
    icon: 'SettingOutlined',
    showInMenu: true,
  },
  {
    key: 'YHGL',
    path: '/XTGL/YHGL',
    title: '用户管理',
    description: '管理系统用户、权限与角色配置',
    menuLabel: '用户管理',
    icon: 'TeamOutlined',
    showInMenu: false,
  },
  {
    key: 'SJKGL',
    path: '/XTGL/SJKGL',
    title: '数据库管理',
    description: '管理图数据库、文件数据库、向量数据库、MySQL',
    menuLabel: '数据库管理',
    icon: 'DatabaseOutlined',
    showInMenu: false,
  },
  {
    key: 'MXGL',
    path: '/XTGL/MXGL',
    title: '模型管理',
    description: '配置LLM、Embedding、向量检索模型',
    menuLabel: '模型管理',
    icon: 'RobotOutlined',
    showInMenu: false,
  },
  {
    key: 'SJBF',
    path: '/XTGL/SJBF',
    title: '数据备份',
    description: '数据备份与恢复管理',
    menuLabel: '数据备份',
    icon: 'CloudUploadOutlined',
    showInMenu: false,
  },
  {
    key: 'WTGL',
    path: '/XTGL/WTGL',
    title: '问题反馈',
    description: '用户问题反馈与帮助文档管理',
    menuLabel: '问题反馈',
    icon: 'QuestionCircleOutlined',
    showInMenu: false,
  },
  {
    key: 'RZGL',
    path: '/XTGL/RZGL',
    title: '日志管理',
    description: '系统操作日志查看与管理',
    menuLabel: '日志管理',
    icon: 'FileTextOutlined',
    showInMenu: false,
  },
];

export function getPageInfoByPath(path: string): PageInfo | undefined {
  return pageConfig.find(page => page.path === path);
}

export function getMenuItems(): PageInfo[] {
  return pageConfig.filter(page => page.showInMenu);
}

export function getBreadcrumbs(path: string): { title: string; path: string }[] {
  const breadcrumbs = [{ title: '首页', path: '/' }];
  const page = getPageInfoByPath(path);
  if (page) {
    breadcrumbs.push({ title: page.menuLabel, path: page.path });
  }
  return breadcrumbs;
}
