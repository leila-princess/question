/**
 * 语义搜索(SSYQ) API 客户端
 */
import type { MethodNode } from '@/components/MethodList';

const PROXY_BASE = '/api/GLCX/SSYQ';

async function apiRequest<T>(path: string, body: Record<string, any>): Promise<T> {
  const url = new URL(PROXY_BASE, window.location.origin);
  url.searchParams.set('path', path);

  const response = await fetch(url.toString(), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const result = await response.json();

  if (!result.success) {
    console.warn(`SSYQ API 请求失败: ${result.error}`);
    return {} as unknown as T;
  }

  return result.data as T;
}

export interface SearchProjectNode {
  id: string;
  name: string;
  type?: string;
  children?: SearchProjectNode[];
  _env?: string;
  environmentid?: string;
}

export interface SearchIndicatorNode {
  id: string;
  name: string;
  description?: string;
  type?: string;
  parent_id?: string;
  children?: SearchIndicatorNode[];
}

export type SearchMethodNode = MethodNode;

export interface SearchTableRow {
  type: 'equipment' | 'project' | 'indicator';
  equipmentId?: string;
  equipmentName?: string;
  projectId?: string;
  projectName?: string;
  envName?: string;
  envId?: string;
  indicatorId?: string;
  indicatorName?: string;
  projectsByEnv?: Record<string, SearchProjectNode[]>;
  indicators?: SearchIndicatorNode[];
  equipments?: Array<{ id: string; name: string }>;
}

export interface SearchGraphData {
  nodes: Array<{
    id: string;
    label: string;
    group: string;
    properties?: Record<string, any>;
  }>;
  edges: Array<{
    id: string;
    from: string;
    to: string;
    label: string;
  }>;
}

export interface SearchResult {
  searchResults: {
    equipments: Array<{ id: string; name: string }>;
    projects: Array<{ id: string; name: string }>;
    indicators: Array<{ id: string; name: string }>;
  };
  tableRows: SearchTableRow[];
  graphData: SearchGraphData;
}

export interface SearchCategoryStats {
  equipments: number;
  projects: number;
  indicators: number;
}

export function buildSearchCategoryStats(
  searchResults?: SearchResult['searchResults'],
): SearchCategoryStats {
  return {
    equipments: searchResults?.equipments?.length ?? 0,
    projects: searchResults?.projects?.length ?? 0,
    indicators: searchResults?.indicators?.length ?? 0,
  };
}

export interface SearchHighlightIds {
  equipmentIds: string[];
  projectIds: string[];
  indicatorIds: string[];
}

/** 从检索结果提取需高亮的设备/项目/指标 ID（仅命中项，非整行） */
export function buildSearchHighlightIds(
  searchResults?: SearchResult['searchResults'],
): SearchHighlightIds {
  return {
    equipmentIds: (searchResults?.equipments ?? []).map((e) => e.id).filter(Boolean),
    projectIds: (searchResults?.projects ?? []).map((p) => p.id).filter(Boolean),
    indicatorIds: (searchResults?.indicators ?? []).map((i) => i.id).filter(Boolean),
  };
}

export function formatSearchStatsMessage(stats: SearchCategoryStats): string {
  const parts: string[] = [];
  if (stats.equipments > 0) parts.push(`${stats.equipments} 个相关装备`);
  if (stats.projects > 0) parts.push(`${stats.projects} 个相关项目`);
  if (stats.indicators > 0) parts.push(`${stats.indicators} 个相关指标`);
  if (parts.length === 0) return '未找到匹配的装备、项目或指标';
  return `找到 ${parts.join('、')}`;
}

export async function fetchSearch(keyword: string): Promise<SearchResult> {
  return apiRequest<SearchResult>('/search', { keyword });
}

/** 方法列表：与 JQCX 相同，请求 POST /api/GLCX/JQCX?path=/methods（后端与 SSYQ 共用实现） */
export { fetchMethods } from '../JQCX/api';
