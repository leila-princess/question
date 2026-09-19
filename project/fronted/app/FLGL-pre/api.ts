const PROXY_BASE = '/api/FLGL';
const DIRECT_BACKEND = process.env.NEXT_PUBLIC_BACKEND_URL || 'http://localhost:5000';

async function apiRequest<T>(path: string, method: 'GET' | 'POST' | 'PUT' | 'DELETE' = 'GET', body?: Record<string, any>): Promise<T> {
  const url = method === 'GET' && body
    ? `${PROXY_BASE}${path}?${new URLSearchParams(body as any).toString()}`
    : `${PROXY_BASE}${path}`;

  const response = await fetch(url, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
  });
  const result = await response.json();
  if (!result.success) {
    const error = result.error || '操作失败';
    console.warn(`FLGL API 失败: ${error}`);
    throw new Error(error);
  }
  const data = result.data;
  if (data === undefined || data === null) {
    return [] as unknown as T;
  }
  return data as T;
}

export type NodeType = 'EquipmentType' | 'Equipment' | 'Environment' | 'TestProject' | 'Indicator' | 'TestMethod' | 'File';

export interface GraphNode {
  id: string;
  name?: string;
  label?: string;
  description?: string;
  type?: string;
  status?: string;
  user_id?: string;
  user_name?: string;
  create_time?: string;
  [key: string]: any;
}

export interface NodeRelation {
  rel_type: string;
  target_labels: string[];
  target_id: string;
  target_name: string;
  direction: 'outgoing' | 'incoming';
}

export interface RelationInput {
  target_id: string;
  rel_type: string;
  direction: 'outgoing' | 'incoming';
}

export async function fetchNodes(nodeType: NodeType, search?: string): Promise<GraphNode[]> {
  if (nodeType === 'Equipment' || nodeType === 'TestProject' || nodeType === 'Indicator') {
    const params = search ? { search } : undefined;
    return apiRequest<GraphNode[]>(`/newnodes/${nodeType}`, 'GET', params);
  }
  return apiRequest<GraphNode[]>(`/nodes/${nodeType}`, 'GET');
}

export async function createNode(nodeType: NodeType, data: Record<string, any>): Promise<GraphNode> {
  if (nodeType === 'Equipment' || nodeType === 'TestProject' || nodeType === 'Indicator') {
    return apiRequest<GraphNode>(`/newnodes/${nodeType}`, 'POST', data);
  }
  return apiRequest<GraphNode>(`/nodes/${nodeType}`, 'POST', data);
}

export async function updateNode(nodeType: NodeType, nodeId: string, data: Record<string, any>): Promise<GraphNode> {
  if (nodeType === 'Equipment' || nodeType === 'TestProject' || nodeType === 'Indicator') {
    return apiRequest<GraphNode>(`/newnodes/${nodeType}/${nodeId}`, 'PUT', data);
  }
  return apiRequest<GraphNode>(`/nodes/${nodeType}/${nodeId}`, 'PUT', data);
}

export async function deleteNode(nodeType: NodeType, nodeId: string): Promise<void> {
  if (nodeType === 'Equipment' || nodeType === 'TestProject' || nodeType === 'Indicator') {
    return apiRequest<void>(`/newnodes/${nodeType}/${nodeId}`, 'DELETE');
  }
  return apiRequest<void>(`/nodes/${nodeType}/${nodeId}`, 'DELETE');
}

export async function fetchNodeRelations(nodeType: NodeType, nodeId: string): Promise<NodeRelation[]> {
  if (nodeType === 'Equipment' || nodeType === 'TestProject' || nodeType === 'Indicator') {
    return apiRequest<NodeRelation[]>(`/newnodes/${nodeType}/${nodeId}/relations`, 'GET');
  }
  return apiRequest<NodeRelation[]>(`/nodes/${nodeType}/${nodeId}/relations`, 'GET');
}

export async function fetchEnvironments(): Promise<Array<{ id: string; name: string }>> {
  return apiRequest<Array<{ id: string; name: string }>>('/newnodes/environments', 'GET');
}

/** 方法 Tab 左侧树：图库中项目链上的环境类 + 指标 + 未分类 */
export type MethodCategoryItem = {
  key: string;
  title: string;
  type: 'environment' | 'indicator' | 'unassigned';
  env_id?: string;
  count: number;
};

export async function fetchMethodCategoryTree(): Promise<{
  categories: MethodCategoryItem[];
  method_category_keys: Record<string, string[]>;
}> {
  return apiRequest<{
    categories: MethodCategoryItem[];
    method_category_keys: Record<string, string[]>;
  }>('/method-categories', 'GET');
}

export async function createRelations(nodeId: string, relations: RelationInput[]): Promise<{ created: number }> {
  return apiRequest<{ created: number }>('/relations', 'POST', { node_id: nodeId, relations });
}

export async function deleteRelation(nodeId: string, targetId: string, relType: string, direction: string): Promise<void> {
  return apiRequest<void>('/relations', 'DELETE', { node_id: nodeId, target_id: targetId, rel_type: relType, direction });
}

export async function fetchAvailableNodes(targetType: NodeType): Promise<Array<{ id: string; name: string }>> {
  return apiRequest<Array<{ id: string; name: string }>>('/available-nodes', 'POST', { target_type: targetType });
}

export async function parseDocument(fileData: any): Promise<any> {
  return apiRequest<any>('/document-parse', 'POST', fileData);
}
// ==================== 环境解析 API ====================

export interface ParsedEnvironment {
  name: string;
  description: string;
  type: string;
  class: string;
  combination: string;
  limit: string;
  request: string;
  images?: Array<{ path: string; url?: string }>;
  label?: string;
  user_id?: string;
}

export async function parseEnvironmentDoc(file: File): Promise<{ success: boolean; data?: ParsedEnvironment[]; error?: string }> {
  const formData = new FormData();
  formData.append('file', file);
  const res = await fetch(`${PROXY_BASE}/env-parse`, { method: 'POST', body: formData });
  return res.json();
}

/** 环境批量导入：与后端 environments 条目一一对应 */
export type EnvImportResolution = {
  action: 'create' | 'replace' | 'skip' | 'rename';
  existing_id?: string;
  new_name?: string;
};

export type EnvImportConflict = {
  index: number;
  name: string;
  existing_id?: string | null;
  reason: 'empty_name' | 'exists_in_db' | 'duplicate_in_batch';
  duplicate_of_index?: number;
};

export type EnvImportResultData =
  | { status: 'conflict'; conflicts: EnvImportConflict[] }
  | {
      status: 'done';
      created: number;
      replaced: number;
      skipped: number;
      renamed: number;
      nodes?: GraphNode[];
    };

export async function importEnvironments(
  environments: ParsedEnvironment[],
  resolutions?: EnvImportResolution[],
): Promise<{ success: boolean; data?: EnvImportResultData; error?: string }> {
  const body: Record<string, unknown> = { environments };
  if (resolutions != null) {
    body.resolutions = resolutions;
  }
  const res = await fetch(`${PROXY_BASE}/env-import`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  return res.json();
}

// ==================== 方法提取 API ====================

export async function extractMethod(file: File, draftMethodId?: string): Promise<any> {
  const formData = new FormData();
  formData.append('file', file);
  let draft = (draftMethodId || '').trim();
  if (!draft) {
    try {
      const dr = await fetch(`${DIRECT_BACKEND}/api/FLGL_method/method-draft-id`);
      const text = await dr.text();
      const dj = JSON.parse(text);
      if (dr.ok && dj.success) draft = String(dj.data?.method_id || '').trim();
    } catch {
      draft = '';
    }
  }
  if (draft) formData.append('draft_method_id', draft);
  const res = await fetch(`${DIRECT_BACKEND}/api/FLGL_method/method-extract`, { method: 'POST', body: formData });
  const text = await res.text();
  try {
    return JSON.parse(text);
  } catch {
    return {
      success: false,
      error: text || `HTTP ${res.status} ${res.statusText}`,
      status: res.status,
    };
  }
}

export async function importMethod(data: any): Promise<any> {
  const res = await fetch(`${DIRECT_BACKEND}/api/FLGL_method/method-import`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  const text = await res.text();
  try {
    return JSON.parse(text);
  } catch {
    return {
      success: false,
      error: text || `HTTP ${res.status} ${res.statusText}`,
      status: res.status,
    };
  }
}

/** 按名称检查 TestMethod 是否已在库中（与 method-extract 中逻辑一致） */
export async function checkMethodNameDuplicate(name: string): Promise<{
  success: boolean;
  data?: { duplicate: boolean; existing: GraphNode | null };
  error?: string;
}> {
  const q = encodeURIComponent((name || '').trim());
  const res = await fetch(`${DIRECT_BACKEND}/api/FLGL_method/method-name-duplicate?name=${q}`);
  const text = await res.text();
  try {
    return JSON.parse(text) as { success: boolean; data?: { duplicate: boolean; existing: GraphNode | null }; error?: string };
  } catch {
    return { success: false, error: text || `HTTP ${res.status}` };
  }
}
