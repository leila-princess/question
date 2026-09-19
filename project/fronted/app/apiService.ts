// Path: app/apiService.ts
import axios from 'axios';

export const sendMessageToBackend = async (message: string) => {
  try {
    const response = await axios.post('/api/hello', { message });
    return response.data;
  } catch (error) {
    console.error("请求后端失败", error);
    throw error;
  }
};

interface SummaryResponse {
  success: boolean;
  data: {
    standardName: string;
    People: [];
    DraftingUnits:[];
    releaseDate: string;
    status: string;

  };
};
export const Getsummary = async (standardId: string): Promise<SummaryResponse> => {
  try {
    const response = await axios.get(`/api/summary?standardId=${encodeURIComponent(standardId)}`);
    return response.data as SummaryResponse;
  } catch (error) {
    console.error("获取标准摘要失败", error);
    throw error;
  }
};

export const queryNeo4jDatabase = async (searchParams: any) => {
  try {
    const response = await axios.post('/api/search', searchParams);
    return response.data;
  } catch (error) {
    console.error("请求查询后端失败", error);
    return { 
      success: false, 
      data: [], 
      message: error instanceof Error ? error.message : '查询请求失败'
    };
  }
};

export const queryzbxm = async (searchParams: any) => {
  try {
    const response = await axios.post('/api/selectsyxm', searchParams);
    return response.data;
  } catch (error) {
    console.error("请求查询后端失败", error);
    return { 
      success: false, 
      data: [], 
      message: error instanceof Error ? error.message : '查询请求失败'
    };
  }
};

export const experimentCreate = async (searchParams: any) => {
  try {
    const response = await axios.post('/api/experimentcre', searchParams);
    return response.data;
  } catch (error) {
    console.error("请求查询后端失败", error);
    return { 
      success: false, 
      data: [], 
      message: error instanceof Error ? error.message : '查询请求失败'
    };
  }
};

// 新增：获取图谱数据
export const fetchGraphData = async (params: { query: string, params: any }) => {
  try {
    const response = await axios.post('/api/kg_graph/graph', params);
    console.log("fetchGraphData里面的params",params);
    return response.data;
  } catch (error) {
    console.error("请求图谱数据失败", error);
    return {
      success: false,
      nodes: [],
      edges: [],
      message: error instanceof Error ? error.message : '获取图谱数据失败'
    };
  }
};


export async function fetchAllGraphData(limit = 500) {
  const resp = await fetch("/api/kg/all_nodes", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ limit }),
  });
  const result = await resp.json();
  if (!result.success) {
    throw new Error(result.error || '获取图谱数据失败');
  }
  return { nodes: result.nodes || [], edges: result.edges || [] };
}

/** ZSTPZS：仅设备类型节点（初始视图） */
export async function fetchTpzsBaseGraph(limit = 500) {
  const resp = await fetch("/api/kg/tpzs_base", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ limit }),
  });
  const result = await resp.json();
  if (!result.success) {
    throw new Error(result.error || '获取设备类型图谱失败');
  }
  return { nodes: result.nodes || [], edges: result.edges || [] };
}

/** ZSTPZS：按节点类型 + 关键词模糊查询 */
export async function searchTpzsGraph(
  nodeType: string,
  keyword: string,
  nodeLimit = 150,
) {
  const resp = await fetch("/api/kg/tpzs_query", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ nodeType, keyword, nodeLimit }),
  });
  const result = await resp.json();
  if (!result.success) {
    throw new Error(result.error || '查询失败');
  }
  return { nodes: result.nodes || [], edges: result.edges || [] };
}

export type TpzsNodePanelPayload = {
  panel:
    | 'env_table'
    | 'equipment_type_devices'
    | 'equipment_type_kind_list'
    | 'environment_fields'
    | 'empty'
    | 'other';
  environmentRows?: Array<{
    equipmentTypeId: string;
    equipmentTypeName: string;
    projectsByEnv: Record<string, unknown[]>;
    indicators: Array<{ id: string; name: string; description?: string }>;
  }>;
  typeDevices?: Array<{ id: string; name: string }>;
  equipmentTypes?: Array<{ id: string; name: string; graphId?: string }>;
  typeCategory?: string;
  environmentFields?: Array<{ label: string; key: string; value: string }>;
  hint?: string;
};

export async function fetchTpzsNodePanel(nodeId: string, nodeGroup: string): Promise<TpzsNodePanelPayload> {
  const resp = await fetch('/api/kg/tpzs_node_panel', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ node_id: nodeId, node_group: nodeGroup }),
  });
  const result = await resp.json();
  if (!result.success) {
    throw new Error(result.error || '加载节点面板失败');
  }
  return result as TpzsNodePanelPayload;
}

export async function exploreNode(nodeId: string, maxDepth = 1, nodeTypes: string[] = [], edgeTypes: string[] = [], limit = 200) {
  const resp = await fetch("/api/kg/explore", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ node_id: nodeId, max_depth: maxDepth, node_types: nodeTypes, edge_types: edgeTypes, limit }),
  });
  const result = await resp.json();
  if (!result.success) {
    throw new Error(result.error || '探索节点失败');
  }
  return { nodes: result.nodes || [], edges: result.edges || [] };
}


/**
 * 根据节点标签和关系类型过滤知识图谱
 * @param nodeLabels 节点标签数组，例如 ["Standard", "Clause"]
 * @param relTypes 关系类型数组，例如 ["HAS_PART", "DEFINES"]
 * @param limit 返回数量限制，默认500
 * @returns 过滤后的节点和关系数据
 */
export async function filterGraphData(
  nodeLabels: string[] = [],
  relTypes: string[] = [],
  limit: number = 500
): Promise<any> {
  const response = await fetch('http://localhost:5000/api/kg/filter', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      node_labels: nodeLabels,
      rel_types: relTypes,
      limit,
    }),
  });

  if (!response.ok) {
    const errorText = await response.text();
    throw new Error(`过滤请求失败 (${response.status}): ${errorText}`);
  }

  const result = await response.json();
  if (!result.success) {
    throw new Error(result.error || '过滤失败');
  }
  return result;
}

export async function ClearGraphData(
  nodeLabels: string[] = [],
  relTypes: string[] = [],
  limit: number = 500
): Promise<any> {
  const response = await fetch('http://localhost:5000/api/pdf2kg/clear', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
  });

  if (!response.ok) {
    const errorText = await response.text();
    throw new Error(`过滤请求失败 (${response.status}): ${errorText}`);
  }

  const result = await response.json();
  if (!result.success) {
    throw new Error(result.error || '清除失败');
  }
  return result;
}

// ---------- 图片访问 ----------
/**
 * 构造图片访问 URL
 * 此函数不是 API 调用，而是根据后端图片服务生成可访问的 URL
 * @param path 节点中存储的图片路径（如 "outputs\\extracted_figures\\12_img1.png"）
 * @returns 完整的图片 URL
 */
export function getImageUrl(path: string): string {
  if (!path) return '';
  // 提取文件名（兼容 Windows 反斜杠和 Linux 正斜杠）
  const filename = path.split(/[\\/]/).pop();
  return `http://localhost:5000/api/pdf2kg/images/${filename}`;
}