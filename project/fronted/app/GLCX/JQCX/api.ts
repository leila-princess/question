/**
 * 精确查询(JQCX) API 客户端
 * 所有与后端的交互集中在此文件
 * 
 * 请求走 Next.js 代理路由 /api/GLCX/JQCX，由代理转发到 Flask 后端
 * 后端未启动时返回空数组，不阻塞页面渲染
 */

const PROXY_BASE = '/api/GLCX/JQCX';

/**
 * 通用请求函数
 */
async function apiRequest<T>(path: string, options?: {
  method?: 'GET' | 'POST';
  body?: Record<string, any>;
  params?: Record<string, string>;
}): Promise<T> {
  const { method = 'GET', body, params } = options || {};

  const url = new URL(PROXY_BASE, window.location.origin);
  url.searchParams.set('path', path);
  if (params) {
    Object.entries(params).forEach(([k, v]) => {
      if (v !== undefined && v !== null) url.searchParams.set(k, v);
    });
  }

  const fetchOptions: RequestInit = {
    method,
    headers: { 'Content-Type': 'application/json' },
  };
  if (body) fetchOptions.body = JSON.stringify(body);

  try {
    const response = await fetch(url.toString(), fetchOptions);
    const result = await response.json();

    if (!result.success) {
      console.warn(`API 请求失败: ${result.error}`);
      return [] as unknown as T;
    }

    return result.data as T;
  } catch (error) {
    console.warn(`后端连接失败: ${path}`, error);
    return [] as unknown as T;
  }
}

// ==================== 节点类型 ====================

export interface NodeBase {
  id: string;
  name: string;
  label?: string;
  description?: string;
  status?: string;
  user_id?: string;
  create_time?: string;
  _labels?: string[];
}

/**
 * 设备分类（一级）— EquipmentCategory
 */
export interface EquipmentCategoryNode extends NodeBase {
  type?: string; // aviation, ground, electronic, naval
}

/**
 * 设备类型（二级）— EquipmentType
 */
export interface EquipmentTypeNode extends NodeBase {
  type?: string; // aviation, ground, electronic, naval
}

/**
 * 具体设备（三级）— Equipment
 */
export interface EquipmentNode extends NodeBase {
  type?: string; // drone, tank, radar, communication
}

/**
 * 三层设备树节点
 */
export interface EquipmentTreeData {
  category: EquipmentCategoryNode;
  types: {
    type: EquipmentTypeNode;
    equipment: EquipmentNode[];
  }[];
}

/**
 * 环境节点 — Environment
 */
export interface EnvironmentNode extends NodeBase {
  type?: string;
  class?: string;
  limit?: string;
  request?: string;
}

/**
 * 试验项目 — TestProject
 * Type: 'category' (父级) 或 'test' (子级)
 */
export interface ProjectNode extends NodeBase {
  type?: string; // 'category' 或 'test'
  children?: ProjectNode[];
}

/**
 * 考核指标 — Indicator
 */
export interface IndicatorNode extends NodeBase {
  type?: string;
}

/**
 * 试验方法 — TestMethod（与 GLCX /methods 及 MethodList 展示字段一致）
 */
export interface MethodRelationEnd {
  id: string;
  name: string;
  rate?: number;
}

export interface MethodNode extends NodeBase {
  type?: string;
  source_name?: string;
  std_id?: string;
  source_id?: string;
  files?: Array<{
    id: string;
    name: string;
    path?: string;
    source_id?: string;
    source_name?: string;
    minio_id?: string;
  }>;
  _relationType?: 'project-only' | 'indicator-only' | 'both';
  /** 与 Neo4j (TestProject)-[:CON {rate}]->(TestMethod) 一致，按 rate 降序 */
  related_projects?: MethodRelationEnd[];
  related_indicators?: MethodRelationEnd[];
  /** 项目/指标关联中的最高 rate，用于排序与总览 */
  max_rate?: number;
}

// ==================== API 函数 ====================

/**
 * 获取三层设备树
 * GET /equipment-tree
 * 返回: [{ category, types: [{ type, equipment: [] }] }]
 */
export async function fetchEquipmentTree(): Promise<EquipmentTreeData[]> {
  return apiRequest<EquipmentTreeData[]>('/equipment-tree');
}

/**
 * 获取所有环境
 * GET /environments
 */
export async function fetchEnvironments(): Promise<EnvironmentNode[]> {
  return apiRequest<EnvironmentNode[]>('/environments');
}

export interface EnvironmentFiveElementsItem {
  env_name: string;
  type: string;
  class: string;
  limit: string;
  request: string;
  combination: string;
}

/** 全部环境五要素（前端缓存，表头问号弹窗使用） */
export async function fetchAllEnvironmentSettings(): Promise<EnvironmentFiveElementsItem[]> {
  return apiRequest<EnvironmentFiveElementsItem[]>('/environment-settings-all');
}

/**
 * 获取项目列表（带父子结构）
 * GET /projects?equipment_id=xxx
 * @param equipmentId 设备ID（非设备类型ID）
 */
export async function fetchProjects(equipmentId?: string): Promise<ProjectNode[]> {
  return apiRequest<ProjectNode[]>('/projects', {
    params: equipmentId ? { equipment_id: equipmentId } : {},
  });
}

/**
 * 获取指标列表
 * GET /indicators?equipment_id=xxx
 * @param equipmentId 设备ID（非设备类型ID）
 */
export async function fetchIndicators(equipmentId?: string): Promise<IndicatorNode[]> {
  return apiRequest<IndicatorNode[]>('/indicators', {
    params: equipmentId ? { equipment_id: equipmentId } : {},
  });
}

/**
 * 查询关联方法
 * POST /methods
 * body: { projectIds: [...], indicatorIds: [...] }
 */
export async function fetchMethods(params: {
  projectIds: string[];
  indicatorIds: string[];
}): Promise<MethodNode[]> {
  return apiRequest<MethodNode[]>('/methods', {
    method: 'POST',
    body: params,
  });
}

/**
 * 通用节点查询
 * POST /query
 */
export async function queryNodes(nodeType: string, keyword?: string, filters?: Record<string, any>): Promise<NodeBase[]> {
  return apiRequest<NodeBase[]>('/query', {
    method: 'POST',
    body: { nodeType, keyword, filters },
  });
}

/**
 * 获取 Schema 定义
 * GET /schema
 */
export async function fetchSchema(): Promise<any> {
  return apiRequest<any>('/schema');
}

/**
 * 获取文件摘要
 * POST /file-abstract
 */
export async function fetchFileAbstract(fileId: string, minioPath: string): Promise<string> {
  const result = await apiRequest<{ abstract?: string }>('/file-abstract', {
    method: 'POST',
    body: { file_id: fileId, minio_path: minioPath },
  });
  return result?.abstract || '';
}
