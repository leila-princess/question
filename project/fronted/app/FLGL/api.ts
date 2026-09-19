const PROXY_BASE = '/api/FLGL';
const DIRECT_BACKEND = process.env.NEXT_PUBLIC_BACKEND_URL || 'http://localhost:5000';

async function parseJsonResponse(response: Response, fallbackMessage: string) {
  const text = await response.text();
  try {
    return JSON.parse(text);
  } catch {
    const preview = text.replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 220);
    throw new Error(preview || `${fallbackMessage}（HTTP ${response.status}）`);
  }
}

function nonJsonErrorPreview(text: string, status: number, fallback: string): string {
  const preview = text.replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 220);
  return preview || `${fallback}（HTTP ${status}）`;
}

async function apiRequest<T>(path: string, method: 'GET' | 'POST' | 'PUT' | 'DELETE' = 'GET', body?: Record<string, any>): Promise<T> {
  const url = method === 'GET' && body
    ? `${PROXY_BASE}${path}?${new URLSearchParams(body as any).toString()}`
    : `${PROXY_BASE}${path}`;

  const response = await fetch(url, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
  });
  const result = await parseJsonResponse(response, 'FLGL 接口返回了非 JSON 内容');
  if (!response.ok || !result.success) {
    const error = result.error || result.message || `操作失败（HTTP ${response.status}）`;
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

export interface EquipmentTreeNode {
  id: string;
  name: string;
  type?: string;
  label?: string;
  children?: Array<EquipmentTreeNode | GraphNode>;
  [key: string]: any;
}

export interface NodeRelation {
  rel_type: string;
  target_labels: string[];
  target_id: string;
  target_name: string;
  direction: 'outgoing' | 'incoming';
  /** 关系上的 rate（如 CON 相关度），无则 undefined */
  rate?: number | null;
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

export async function fetchEquipmentTree(): Promise<EquipmentTreeNode[]> {
  return apiRequest<EquipmentTreeNode[]>('/newnodes/equipment-tree', 'GET');
}

export async function fetchMethodsByRelation(relatedType: 'TestProject' | 'Indicator', equipmentId?: string): Promise<GraphNode[]> {
  const params: Record<string, string> = { related_type: relatedType };
  if (equipmentId) params.equipment_id = equipmentId;
  const q = new URLSearchParams(params).toString();
  const res = await fetch(`${DIRECT_BACKEND}/api/FLGL_method/methods-by-relation?${q}`);
  const result = await parseJsonResponse(res, '方法查询接口返回了非 JSON 内容');
  if (!res.ok || !result.success) throw new Error(result.error || result.message || `方法查询失败（HTTP ${res.status}）`);
  return result.data || [];
}

export async function uploadMethodAttachment(file: File, userId = 'admin'): Promise<{ file_id: string; file_node: GraphNode; minio_path: string }> {
  const formData = new FormData();
  formData.append('file', file);
  formData.append('user_id', userId);
  const res = await fetch(`${DIRECT_BACKEND}/api/FLGL_method/upload-attachment`, { method: 'POST', body: formData });
  const result = await parseJsonResponse(res, '附件上传接口返回了非 JSON 内容');
  if (!res.ok || !result.success) throw new Error(result.error || result.message || `附件上传失败（HTTP ${res.status}）`);
  return result.data;
}

export async function fetchMethodDraftId(): Promise<string> {
  const res = await fetch('/api/FLGL_method/method-draft-id');
  const result = await parseJsonResponse(res, '方法草稿ID接口返回了非 JSON 内容');
  if (!res.ok || !result.success) throw new Error(result.error || result.message || `获取方法ID失败（HTTP ${res.status}）`);
  return result.data?.method_id || '';
}

export async function uploadMethodImage(file: File, methodId: string, userId = 'admin'): Promise<{ file_id: string; file_node: GraphNode; minio_path: string; url: string }> {
  const formData = new FormData();
  formData.append('file', file);
  formData.append('method_id', methodId);
  formData.append('user_id', userId);
  const res = await fetch('/api/FLGL_method/upload-method-image', { method: 'POST', body: formData });
  const result = await parseJsonResponse(res, '方法图片上传接口返回了非 JSON 内容');
  if (!res.ok || !result.success) throw new Error(result.error || result.message || `图片上传失败（HTTP ${res.status}）`);
  return result.data;
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

export async function fetchAvailableNodes(
  targetType: NodeType,
  context?: { sourceNodeId?: string; sourceNodeType?: NodeType },
): Promise<Array<{ id: string; name: string }>> {
  return apiRequest<Array<{ id: string; name: string }>>('/available-nodes', 'POST', {
    target_type: targetType,
    source_node_id: context?.sourceNodeId,
    source_node_type: context?.sourceNodeType,
  });
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
  const text = await res.text();
  try {
    return JSON.parse(text);
  } catch {
    const preview = text.replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 220);
    return { success: false, error: preview || `环境解析失败（HTTP ${res.status}）` };
  }
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
  const text = await res.text();
  try {
    return JSON.parse(text);
  } catch {
    const preview = text.replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 220);
    return { success: false, error: preview || `环境导入失败（HTTP ${res.status}）` };
  }
}

// ==================== 方法提取 API ====================

export async function extractMethod(
  file: File,
  draftMethodId?: string,
  options?: { forceRefresh?: boolean },
): Promise<any> {
  const formData = new FormData();
  formData.append('file', file);
  if (options?.forceRefresh !== false) {
    formData.append('force_refresh', '1');
  }
  let draft = (draftMethodId || '').trim();
  if (!draft) {
    try {
      const dr = await fetch(`${DIRECT_BACKEND}/api/FLGL_method/method-draft-id`);
      const dj = await parseJsonResponse(dr, '方法草稿ID接口返回了非 JSON 内容');
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
      error: nonJsonErrorPreview(text, res.status, '方法提取接口返回了非 JSON 内容'),
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
      error: nonJsonErrorPreview(text, res.status, '方法导入接口返回了非 JSON 内容'),
      status: res.status,
    };
  }
}

/** 按名称检查 TestMethod 是否已在库中（与 method-extract 中逻辑一致） */
export async function checkMethodNameDuplicate(name: string): Promise<{
  success: boolean;
  data?: { duplicate: boolean; existing: GraphNode | null; matches?: GraphNode[] };
  error?: string;
}> {
  const q = encodeURIComponent((name || '').trim());
  const res = await fetch(`${DIRECT_BACKEND}/api/FLGL_method/method-name-duplicate?name=${q}`);
  const text = await res.text();
  try {
    return JSON.parse(text) as {
      success: boolean;
      data?: { duplicate: boolean; existing: GraphNode | null; matches?: GraphNode[] };
      error?: string;
    };
  } catch {
    return { success: false, error: text || `HTTP ${res.status}` };
  }
}

export type ExtractSectionGroup = {
  parent: { sectionNo: string; title: string; level: number };
  children: Array<{ sectionNo: string; title: string; level: number }>;
};

export async function extractMethodFromSections(params: {
  sourceFileId: string;
  objectName?: string;
  previewCacheKey?: string;
  sections: ExtractSectionGroup[];
  userId?: string;
  draftMethodId?: string;
  useLlmMerge?: boolean;
}): Promise<any> {
  const res = await fetch(`${DIRECT_BACKEND}/api/FLGL_method/method-extract-sections`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      source_file_id: params.sourceFileId,
      object_name: params.objectName || '',
      preview_cache_key: params.previewCacheKey || '',
      sections: params.sections,
      user_id: params.userId || 'admin',
      draft_method_id: params.draftMethodId || '',
      use_llm_merge: !!params.useLlmMerge,
    }),
  });
  const text = await res.text();
  try {
    return JSON.parse(text);
  } catch {
    return {
      success: false,
      error: nonJsonErrorPreview(text, res.status, '按章节提取方法接口返回了非 JSON 内容'),
      status: res.status,
    };
  }
}

export async function extractMethodFromSectionsFile(params: {
  file: File;
  previewCacheKey?: string;
  sections: ExtractSectionGroup[];
  userId?: string;
  draftMethodId?: string;
  useLlmMerge?: boolean;
}): Promise<any> {
  const formData = new FormData();
  formData.append('file', params.file);
  formData.append('sections', JSON.stringify(params.sections));
  formData.append('user_id', params.userId || 'admin');
  formData.append('draft_method_id', params.draftMethodId || '');
  if (params.previewCacheKey) formData.append('preview_cache_key', params.previewCacheKey);
  if (params.useLlmMerge) formData.append('use_llm_merge', '1');

  const res = await fetch(`${DIRECT_BACKEND}/api/FLGL_method/method-extract-sections-file`, {
    method: 'POST',
    body: formData,
  });
  const text = await res.text();
  try {
    return JSON.parse(text);
  } catch {
    return {
      success: false,
      error: nonJsonErrorPreview(text, res.status, '按章节提取方法（文件）接口返回了非 JSON 内容'),
      status: res.status,
    };
  }
}

export async function fetchDocumentHeadings(
  file: File,
  options?: { mode?: 'full' | 'fast' },
): Promise<{
  success: boolean;
  data?: {
    toc?: unknown[];
    full_headings?: Array<{
      section_id?: string;
      sectionNo?: string;
      title: string;
      level: number;
      snippet?: string;
      source?: string;
    }>;
    full_text_len?: number;
    preview_cache_key?: string;
    extract_method?: string;
    toc_source?: string;
    sections_count?: number;
    heading_count?: number;
    toc_routing_rev?: string;
    debug_dir?: string;
    has_preview_content?: boolean;
    preview_html_len?: number;
    preview_markdown_len?: number;
    preview_local_dir?: string;
  };
  error?: string;
}> {
  const formData = new FormData();
  formData.append('file', file);
  if (options?.mode) formData.append('mode', options.mode);
  const res = await fetch(`${DIRECT_BACKEND}/api/FLGL_method/document-headings-upload`, {
    method: 'POST',
    body: formData,
  });
  const text = await res.text();
  try {
    return JSON.parse(text);
  } catch {
    return {
      success: false,
      error: nonJsonErrorPreview(text, res.status, '文档标题解析接口返回了非 JSON 内容'),
    };
  }
}

/** 从 FLGL preview 本地落盘读取全文 HTML/Markdown（与 document-headings-upload 同源，避免二次 MinerU） */
export async function fetchPreviewParseContent(
  previewCacheKey: string,
  format: 'html' | 'md' = 'html',
): Promise<string> {
  const key = encodeURIComponent(previewCacheKey);
  const res = await fetch(
    `${DIRECT_BACKEND}/api/FLGL_method/preview-parse-content?key=${key}&format=${format}`,
  );
  const ct = (res.headers.get('content-type') || '').toLowerCase();
  if (ct.includes('text/html') || ct.includes('text/markdown') || ct.includes('text/plain')) {
    if (res.status === 409) throw new Error('PARSE_PENDING');
    if (!res.ok) {
      const errText = (await res.text().catch(() => '')).trim();
      throw new Error(errText || `HTTP ${res.status}`);
    }
    return await res.text();
  }
  const json = await parseJsonResponse(res, '预览正文接口返回了非 JSON 内容');
  if (!res.ok || !json.success) {
    if ((json as { code?: string }).code === 'parse_pending') throw new Error('PARSE_PENDING');
    throw new Error(String(json.error || `HTTP ${res.status}`));
  }
  return String((json.data as { content?: string })?.content || '');
}

export interface FileDuplicateCheckResult {
  duplicate: boolean;
  file_md5?: string;
  file_node_id?: string;
  doc_id?: string;
  file_name?: string;
  minio_id?: string;
  chroma_id?: string;
  chroma_chunks?: number;
  has_chroma?: boolean;
  is_pdf?: boolean;
  use_chroma_source?: boolean;
  linked_method_count?: number;
  linked_methods?: Array<{ id: string; name: string; source_name?: string }>;
}

export async function checkMethodFileDuplicate(file: File): Promise<FileDuplicateCheckResult> {
  const formData = new FormData();
  formData.append('file', file);
  const res = await fetch(`${DIRECT_BACKEND}/api/FLGL_method/file-duplicate-check`, {
    method: 'POST',
    body: formData,
  });
  const json = await parseJsonResponse(res, '文件查重接口返回了非 JSON 内容');
  if (!res.ok || !json.success) {
    throw new Error(String(json.error || '文件查重失败'));
  }
  return (json.data || { duplicate: false }) as FileDuplicateCheckResult;
}

export async function extractMethodAuto(params: {
  file?: File | null;
  sourceFileId?: string;
  objectName?: string;
  userId?: string;
  draftMethodId?: string;
  skipRecall?: boolean;
  docClassHint?: string;
  useChromaSource?: boolean;
}): Promise<any> {
  const formData = new FormData();
  if (params.file) {
    formData.append('file', params.file);
  }
  formData.append('source_file_id', params.sourceFileId || 'WJ_auto0001');
  if (params.objectName) formData.append('object_name', params.objectName);
  formData.append('user_id', params.userId || 'admin');
  if (params.skipRecall) formData.append('skip_recall', '1');
  if (params.docClassHint) formData.append('doc_class_hint', params.docClassHint);
  if (params.useChromaSource) formData.append('use_chroma_source', '1');

  let draft = (params.draftMethodId || '').trim();
  if (!draft) {
    try {
      const dr = await fetch(`${DIRECT_BACKEND}/api/FLGL_method/method-draft-id`);
      const dj = await parseJsonResponse(dr, '方法草稿ID接口返回了非 JSON 内容');
      if (dr.ok && dj.success) draft = String(dj.data?.method_id || '').trim();
    } catch {
      draft = '';
    }
  }
  if (draft) formData.append('draft_method_id', draft);

  const res = await fetch(`${DIRECT_BACKEND}/api/FLGL_method/method-extract-auto`, {
    method: 'POST',
    body: formData,
  });
  const text = await res.text();
  try {
    return JSON.parse(text);
  } catch {
    return {
      success: false,
      error: nonJsonErrorPreview(text, res.status, 'LLM 自动提取接口返回了非 JSON 内容'),
      status: res.status,
    };
  }
}

export type MethodExtractStreamEvent =
  | { type: 'job_started'; data: Record<string, unknown> }
  | { type: 'organization_judged'; data: Record<string, unknown> }
  | { type: 'llm_extract_started'; data: Record<string, unknown> }
  | { type: 'project_discovered'; data: Record<string, unknown> }
  | { type: 'project_extract_started'; data: Record<string, unknown> }
  | { type: 'method_extract_started'; data: Record<string, unknown> }
  | { type: 'method_ready'; data: { method: Record<string, unknown>; method_index?: number; job_id?: string; document_dir?: string } }
  | { type: 'method_failed'; data: Record<string, unknown> }
  | { type: 'done'; data: Record<string, unknown> }
  | { type: 'error'; data: { message?: string } };

function parseSseBlock(block: string): { event: string; data: string } | null {
  let event = 'message';
  const dataLines: string[] = [];
  for (const line of block.split('\n')) {
    if (line.startsWith('event:')) event = line.slice(6).trim();
    else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim());
  }
  if (!dataLines.length) return null;
  return { event, data: dataLines.join('\n') };
}

export async function extractMethodAutoStream(params: {
  file?: File | null;
  sourceFileId?: string;
  objectName?: string;
  userId?: string;
  draftMethodId?: string;
  skipRecall?: boolean;
  docClassHint?: string;
  useChromaSource?: boolean;
  onEvent: (evt: MethodExtractStreamEvent) => void;
  signal?: AbortSignal;
}): Promise<{ jobId?: string; documentDir?: string; methodsCount?: number }> {
  const formData = new FormData();
  if (params.file) {
    formData.append('file', params.file);
  }
  formData.append('source_file_id', params.sourceFileId || 'WJ_auto0001');
  if (params.objectName) formData.append('object_name', params.objectName);
  formData.append('user_id', params.userId || 'admin');
  if (params.skipRecall) formData.append('skip_recall', '1');
  if (params.docClassHint) formData.append('doc_class_hint', params.docClassHint);
  if (params.useChromaSource) formData.append('use_chroma_source', '1');

  let draft = (params.draftMethodId || '').trim();
  if (!draft) {
    try {
      const dr = await fetch(`${DIRECT_BACKEND}/api/FLGL_method/method-draft-id`);
      const dj = await parseJsonResponse(dr, '方法草稿ID接口返回了非 JSON 内容');
      if (dr.ok && dj.success) draft = String(dj.data?.method_id || '').trim();
    } catch {
      draft = '';
    }
  }
  if (draft) formData.append('draft_method_id', draft);

  const res = await fetch(`${DIRECT_BACKEND}/api/FLGL_method/method-extract-auto/stream`, {
    method: 'POST',
    body: formData,
    signal: params.signal,
  });
  if (!res.ok || !res.body) {
    const text = await res.text().catch(() => '');
    throw new Error(nonJsonErrorPreview(text, res.status, 'LLM 流式提取失败'));
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let jobId = '';
  let documentDir = '';
  let methodsCount = 0;

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split('\n\n');
    buffer = parts.pop() || '';
    for (const block of parts) {
      const trimmed = block.trim();
      if (!trimmed || trimmed.startsWith(':')) continue;
      const parsed = parseSseBlock(trimmed);
      if (!parsed) continue;
      let payload: Record<string, unknown> = {};
      try {
        payload = JSON.parse(parsed.data) as Record<string, unknown>;
      } catch {
        continue;
      }
      const eventType = parsed.event as MethodExtractStreamEvent['type'];
      if (payload.job_id) jobId = String(payload.job_id);
      if (payload.document_dir) documentDir = String(payload.document_dir);
      if (eventType === 'done' && payload.methods_count != null) {
        methodsCount = Number(payload.methods_count);
      }
      params.onEvent({ type: eventType, data: payload } as MethodExtractStreamEvent);
      if (eventType === 'error') {
        throw new Error(String((payload as { message?: string }).message || '流式提取失败'));
      }
    }
  }
  return { jobId, documentDir, methodsCount };
}

export async function fetchMethodExtractStatus(params: {
  jobId?: string;
  documentDir: string;
}): Promise<{
  success: boolean;
  data?: {
    methods_count?: number;
    methods?: Record<string, unknown>[];
    status?: string;
    document_dir?: string;
  };
  error?: string;
}> {
  const q = new URLSearchParams();
  if (params.jobId) q.set('job_id', params.jobId);
  q.set('document_dir', params.documentDir);
  const res = await fetch(`${DIRECT_BACKEND}/api/FLGL_method/method-extract-status?${q.toString()}`);
  const text = await res.text();
  try {
    return JSON.parse(text);
  } catch {
    return {
      success: false,
      error: nonJsonErrorPreview(text, res.status, '提取状态查询失败'),
    };
  }
}

export type GuidedMethodCandidate = {
  candidate_id: string;
  status: string;
  display_name: string;
  project_name?: string;
  method_short_name?: string;
  span_hint?: string;
  memory_excerpt?: string;
  user_feedback?: string;
  confidence?: number;
  anchor_section_ids?: string[];
  reasoning?: string;
  granularity_rationale?: string;
  structure_profile_ref?: string;
  semantic_slots?: { conditions?: string; process?: string; evaluation?: string };
  materialized?: Record<string, unknown>;
};

export type GuidedStructureProfile = {
  document_style?: string;
  method_unit?: string;
  organization_mode?: string;
  organization_confidence?: number;
  organization_reason?: string;
  organization_warnings?: string[];
  project_heading_patterns?: string[];
  section_aliases?: Record<string, string[]>;
  warnings?: string[];
};

export type GuidedExtractStreamEvent =
  | { type: 'session_ready'; data: Record<string, unknown> }
  | { type: 'understanding_progress'; data: { chunk_index?: number; chunk_total?: number } }
  | { type: 'understanding_done'; data: { candidate_count?: number } }
  | { type: 'done'; data: Record<string, unknown> }
  | { type: 'error'; data: { message?: string } };

export async function createGuidedExtractSession(file: File) {
  const formData = new FormData();
  formData.append('file', file);
  const res = await fetch(`${DIRECT_BACKEND}/api/FLGL_method/method-extract-guided/session`, {
    method: 'POST',
    body: formData,
  });
  const text = await res.text();
  try {
    return JSON.parse(text);
  } catch {
    return { success: false, error: nonJsonErrorPreview(text, res.status, '创建引导式会话失败') };
  }
}

export async function fetchGuidedExtractSession(sessionId: string) {
  const res = await fetch(`${DIRECT_BACKEND}/api/FLGL_method/method-extract-guided/${encodeURIComponent(sessionId)}`);
  const text = await res.text();
  try {
    return JSON.parse(text);
  } catch {
    return { success: false, error: nonJsonErrorPreview(text, res.status, '读取引导式会话失败') };
  }
}

export async function guidedUnderstandStream(params: {
  sessionId: string;
  onEvent: (evt: GuidedExtractStreamEvent) => void;
  signal?: AbortSignal;
}) {
  const res = await fetch(
    `${DIRECT_BACKEND}/api/FLGL_method/method-extract-guided/${encodeURIComponent(params.sessionId)}/understand`,
    { method: 'POST', signal: params.signal },
  );
  if (!res.ok || !res.body) {
    const text = await res.text().catch(() => '');
    throw new Error(nonJsonErrorPreview(text, res.status, '引导式理解失败'));
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let streamError: string | null = null;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split('\n\n');
    buffer = parts.pop() || '';
    for (const block of parts) {
      const trimmed = block.trim();
      if (!trimmed) continue;
      let event = 'message';
      const dataLines: string[] = [];
      for (const line of trimmed.split('\n')) {
        if (line.startsWith('event:')) event = line.slice(6).trim();
        else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim());
      }
      if (!dataLines.length) continue;
      try {
        const payload = JSON.parse(dataLines.join('\n'));
        params.onEvent({ type: event as GuidedExtractStreamEvent['type'], data: payload });
        if (event === 'error') {
          streamError = String((payload as { message?: string }).message || '引导式理解失败');
        }
      } catch {
        /* ignore */
      }
    }
  }
  if (streamError) {
    throw new Error(streamError);
  }
}

export async function patchGuidedCandidate(
  sessionId: string,
  candidateId: string,
  body: Record<string, unknown>,
) {
  const res = await fetch(
    `${DIRECT_BACKEND}/api/FLGL_method/method-extract-guided/${encodeURIComponent(sessionId)}/candidates/${encodeURIComponent(candidateId)}`,
    {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    },
  );
  const text = await res.text();
  try {
    return JSON.parse(text);
  } catch {
    return { success: false, error: nonJsonErrorPreview(text, res.status, '更新候选失败') };
  }
}

export async function refineGuidedCandidate(sessionId: string, candidateId: string) {
  const res = await fetch(
    `${DIRECT_BACKEND}/api/FLGL_method/method-extract-guided/${encodeURIComponent(sessionId)}/candidates/${encodeURIComponent(candidateId)}/refine`,
    { method: 'POST' },
  );
  const text = await res.text();
  try {
    return JSON.parse(text);
  } catch {
    return { success: false, error: nonJsonErrorPreview(text, res.status, 'LLM 修正锚点失败') };
  }
}

export async function materializeGuidedSession(sessionId: string) {
  const res = await fetch(
    `${DIRECT_BACKEND}/api/FLGL_method/method-extract-guided/${encodeURIComponent(sessionId)}/materialize`,
    { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' },
  );
  const text = await res.text();
  try {
    return JSON.parse(text);
  } catch {
    return { success: false, error: nonJsonErrorPreview(text, res.status, '程序化取文失败') };
  }
}

export async function finalizeGuidedSession(sessionId: string, draftMethodId?: string) {
  const res = await fetch(
    `${DIRECT_BACKEND}/api/FLGL_method/method-extract-guided/${encodeURIComponent(sessionId)}/finalize`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ draft_method_id: draftMethodId || '' }),
    },
  );
  const text = await res.text();
  try {
    return JSON.parse(text);
  } catch {
    return { success: false, error: nonJsonErrorPreview(text, res.status, '生成方法失败') };
  }
}

export async function recallMethodScoped(params: {
  equipmentIds: string[];
  documentDir?: string;
  methodIndex?: number;
  methodName?: string;
  graphMatches?: Record<string, unknown>;
}): Promise<{ success: boolean; data?: Record<string, unknown>; error?: string }> {
  const res = await fetch(`${DIRECT_BACKEND}/api/FLGL_method/method-recall-scoped`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      equipment_ids: params.equipmentIds,
      document_dir: params.documentDir || '',
      method_index: params.methodIndex || 0,
      method_name: params.methodName || '',
      graph_matches: params.graphMatches,
    }),
  });
  const text = await res.text();
  try {
    return JSON.parse(text);
  } catch {
    return {
      success: false,
      error: nonJsonErrorPreview(text, res.status, 'scoped 召回失败'),
    };
  }
}

export interface RecommendMethodRelationsParams {
  method: {
    name?: string;
    description?: string;
    synr?: string;
    type?: string;
    label?: string;
    source_name?: string;
  };
  equipmentIds?: string[];
  documentDir?: string;
  methodIndex?: number;
  methodName?: string;
}

export interface RecommendMethodRelationsResult {
  equipment_recommendations?: Array<{
    equipment_id: string;
    equipment_name: string;
    score?: number;
    projects?: Array<{ node_id: string; node_name?: string; score?: number }>;
    indicators?: Array<{ node_id: string; node_name?: string; score?: number }>;
  }>;
  equipment_candidates?: Array<{ node_id: string; node_name?: string; score?: number }>;
  recommended_nodes?: Array<{ node_type?: string; node_id: string; node_name?: string; score?: number }>;
  recommended_relations?: Array<Record<string, unknown>>;
}

export async function recommendMethodRelations(
  params: RecommendMethodRelationsParams,
): Promise<{ success: boolean; data?: RecommendMethodRelationsResult; error?: string }> {
  const res = await fetch(`${DIRECT_BACKEND}/api/FLGL_method/method-recommend-relations`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      method: params.method,
      equipment_ids: params.equipmentIds || [],
      document_dir: params.documentDir || '',
      method_index: params.methodIndex || 0,
      method_name: params.methodName || params.method?.name || '',
    }),
  });
  const text = await res.text();
  try {
    return JSON.parse(text);
  } catch {
    return {
      success: false,
      error: nonJsonErrorPreview(text, res.status, 'LLM 推荐关联节点失败'),
    };
  }
}

export async function fetchMethodEquipmentTabs(methodId: string): Promise<{
  equipment_recommendations?: RecommendMethodRelationsResult['equipment_recommendations'];
}> {
  const mid = (methodId || '').trim();
  if (!mid) return {};
  const res = await fetch(`${DIRECT_BACKEND}/api/FLGL_method/method/${encodeURIComponent(mid)}/equipment-tabs`);
  const text = await res.text();
  try {
    const json = JSON.parse(text);
    if (!res.ok || !json.success) {
      throw new Error(String(json.error || '加载设备关联失败'));
    }
    return json.data || {};
  } catch (e) {
    if (e instanceof Error) throw e;
    throw new Error(nonJsonErrorPreview(text, res.status, '加载设备关联失败'));
  }
}
