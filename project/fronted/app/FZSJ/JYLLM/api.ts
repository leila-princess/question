/**
 * JYLLM 辅助设计 API 客户端
 */

const JYLLM_PROXY = '/api/fzsj/jyllm';

async function jyllmRequest<T>(path: string, options?: {
  method?: 'GET' | 'POST';
  body?: Record<string, any>;
  params?: Record<string, string>;
}): Promise<T> {
  const { method = 'GET', body, params } = options || {};

  const url = new URL(`${JYLLM_PROXY}${path}`, window.location.origin);
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
      console.warn(`JYLLM API 请求失败: ${result.error}`);
      return null as unknown as T;
    }
    return (result.data ?? result) as T;
  } catch (error) {
    console.warn(`JYLLM 后端连接失败: ${path}`, error);
    return null as unknown as T;
  }
}

// ========== 数据查询 ==========

export async function fetchEquipmentTree() {
  return jyllmRequest<any[]>('/equipment-tree');
}

function buildQueryString(params: Record<string, string | undefined>) {
  const query = Object.entries(params)
    .filter(([, value]) => value && value.trim())
    .map(([key, value]) => `${encodeURIComponent(key)}=${encodeURIComponent(value!)}`)
    .join('&');
  return query ? `?${query}` : '';
}

export async function fetchProjects(equipmentIds?: string[], envIds?: string[]) {
  const equipmentParam = equipmentIds?.filter(Boolean).join(',');
  const envParam = envIds?.filter(Boolean).join(',');
  const query = buildQueryString({
    equipment_id: equipmentParam,
    env_id: envParam,
  });
  return jyllmRequest<any[]>(`/projects${query}`);
}

export async function fetchIndicators(equipmentIds?: string[]) {
  const equipmentParam = equipmentIds?.filter(Boolean).join(',');
  const query = buildQueryString({
    equipment_id: equipmentParam,
  });
  return jyllmRequest<any[]>(`/indicators${query}`);
}

export async function fetchMethods(params: { projectIds?: string[]; indicatorIds?: string[] }) {
  return jyllmRequest<any[]>('/methods', {
    method: 'POST',
    body: {
      projectIds: params.projectIds || [],
      indicatorIds: params.indicatorIds || [],
    },
  });
}

export async function fetchEnvironments() {
  return jyllmRequest<any[]>('/environments');
}

export async function fetchEnvironmentDetail(envId: string) {
  return jyllmRequest<any>('/environment/detail/' + envId);
}

export async function fetchProjectsByDeviceAndEnv(params: {
  deviceIds: string[];
  envIds: string[];
}) {
  return jyllmRequest<any[]>('/projects-by-env', {
    method: 'POST',
    body: params,
  });
}

export async function fetchIndicatorsByDeviceAndEnv(params: {
  deviceIds: string[];
  envIds: string[];
}) {
  return jyllmRequest<any[]>('/indicators-by-env', {
    method: 'POST',
    body: params,
  });
}

// ========== 资料员 LLM ==========

export async function callLibrarian(params: {
  equipment_ids: string[];
  environment_id: string;
}) {
  return jyllmRequest<{
    suggestion: string;
    env_details: Record<string, any>;
    reasoning: string;
  }>('/librarian', {
    method: 'POST',
    body: params,
  });
}

// ========== 文件上传与解析 ==========

export async function uploadFile(file: File, metadata?: {
  category?: string;
  label?: string;
  user_id?: string;
  title?: string;
  abstract?: string;
  keywords?: string[];
  ics?: string;
  ccs?: string;
  sync_chroma?: boolean;
  associations?: any[];
  uid?: string;
}) {
  const uid = metadata?.uid || `uid_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
  const formData = new FormData();
  formData.append('files', file);
  formData.append('category', metadata?.category || '其他');
  formData.append('label', metadata?.label || '');
  formData.append('user_id', metadata?.user_id || 'admin');
  formData.append('duplicate_action', 'replace');
  formData.append(`uid_${file.name}`, uid);
  if (metadata?.title) formData.append(`title_${file.name}`, metadata.title);
  if (metadata?.abstract) formData.append(`abstract_${file.name}`, metadata.abstract);
  if (metadata?.keywords) formData.append(`keywords_${file.name}`, JSON.stringify(metadata.keywords));
  if (metadata?.ics) formData.append(`ics_${file.name}`, metadata.ics);
  if (metadata?.ccs) formData.append(`ccs_${file.name}`, metadata.ccs);
  if (metadata?.associations) formData.append(`associations_${file.name}`, JSON.stringify(metadata.associations));
  formData.append(`sync_chroma_${file.name}`, metadata?.sync_chroma !== false ? 'true' : 'false');
  
  try {
    const response = await fetch('/api/wjgl/upload', {
      method: 'POST',
      body: formData,
    });
    const result = await response.json();
    if (!result.success) {
      throw new Error(result.error || '上传失败');
    }
    return result.data?.[0] || result.data;
  } catch (error) {
    console.error('文件上传失败:', error);
    throw error;
  }
}

export async function parseFile(fileId: string) {
  try {
    const response = await fetch('/api/wjgl/auto-parse', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ file_id: fileId }),
    });
    const result = await response.json();
    if (!result.success) {
      throw new Error(result.error || '解析失败');
    }
    return result.data;
  } catch (error) {
    console.error('文件解析失败:', error);
    throw error;
  }
}

// ========== 方案管理 ==========

export async function fetchSchemes(userId: string = 'admin') {
  try {
    const response = await fetch(
      `/api/fzsj/jyllm/history/list?user_id=${encodeURIComponent(userId)}&page=1&page_size=50`
    );
    const result = await response.json();
    if (!result.success) return [];
    return result.items || [];
  } catch {
    return [];
  }
}

/** 删除 MySQL 中的 JYLLM 方案历史（jyllm_scheme_history 及关联子表） */
export async function deleteSchemeHistory(taskId: string, userId: string = 'admin') {
  const res = await fetch(
    `/api/fzsj/jyllm/history/delete/${encodeURIComponent(taskId)}?user_id=${encodeURIComponent(userId)}`,
    { method: 'DELETE' }
  );
  const result = await res.json();
  if (!res.ok || !result.success) {
    throw new Error((result as { error?: string }).error || '删除失败');
  }
  return true;
}

export async function fetchScheme(taskId: string, userId: string = 'admin') {
  try {
    const response = await fetch(
      `/api/fzsj/jyllm/history/detail/${encodeURIComponent(taskId)}?user_id=${encodeURIComponent(userId)}`
    );
    const result = await response.json();
    if (result.success && result.data) {
      return {
        task_id: result.data.task_id,
        chapters: result.data.chapters || [],
        full_scheme: result.data.generated_content || '',
        scheme_description: result.data.scheme_description || '',
        current_step: result.data.current_step,
        generation_status: result.data.generation_status,
        messages: result.data.messages,
        planner_prompts: result.data.planner_prompts,
        overall_score: result.data.overall_score,
        overall_comment: result.data.overall_comment,
        metadata: {
          devices: result.data.selected_equipment || [],
          environments: result.data.selected_environments || [],
          projects: result.data.selected_projects || [],
          indicators: result.data.selected_indicators || [],
          methods: result.data.selected_methods || [],
        },
      };
    }
  } catch {
    // fallback below
  }
  return jyllmRequest<any>('/scheme/' + taskId);
}

export async function saveScheme(data: {
  task_id: string;
  full_scheme: string;
  chapters: any[];
  metadata: Record<string, any>;
}) {
  return jyllmRequest<any>('/save', {
    method: 'POST',
    body: data,
  });
}

/** 将 data URL 插图上传至 MinIO（方案/{task_id}/assets/），返回同源 URL 供 Markdown 引用 */
export async function uploadSchemeAsset(taskId: string, dataUrl: string): Promise<string | null> {
  const m = /^data:([^;]+);base64,(.+)$/.exec(dataUrl.trim());
  if (!m || !taskId.trim()) return null;
  try {
    const res = await fetch(`${JYLLM_PROXY}/scheme_asset`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        task_id: taskId.trim(),
        content_type: m[1].trim(),
        base64: m[2].replace(/\s/g, ''),
      }),
    });
    const j = await res.json();
    if (!j.success) return null;
    const url = j.data?.url as string | undefined;
    return url || null;
  } catch {
    return null;
  }
}

export async function updateScheme(data: {
  task_id: string;
  full_scheme: string;
  chapters: any[];
  metadata: Record<string, any>;
}) {
  return jyllmRequest<any>(`/update/${data.task_id}`, {
    method: 'POST',
    body: data,
  });
}

export async function exportScheme(taskId: string, format: 'md' | 'pdf' | 'word') {
  return jyllmRequest<any>('/export/' + taskId, {
    method: 'POST',
    body: { taskId, format },
  });
}

export async function submitFeedback(data: {
  task_id: string;
  chapter_index: number;
  score?: number;
  comment: string;
  action?: 'save' | 'rewrite';
}) {
  return jyllmRequest<any>('/user-feedback', {
    method: 'POST',
    body: { ...data, score: data.score ?? 0, action: data.action ?? 'save' },
  });
}
