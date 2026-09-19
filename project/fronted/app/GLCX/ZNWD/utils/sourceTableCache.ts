import type { EnvironmentTableRow, SelectedIndicatorEntry, SelectedProjectEntry } from '@/components/EnvironmentTable';
import type { ZnwdEquipmentHierarchyRow } from '../components/ZnwdEquipmentHierarchyTable';

/** 来源抽屉：环境类问题展示的 Environment 节点行（字段名为中文） */
export type ZnwdEnvironmentSourceRow = {
  id?: string;
  名称: string;
  标签: string;
  描述: string;
  分类: string;
  分级: string;
  典型组合: string;
  约束: string;
  要求: string;
};

/** 回答完成后预取的来源表格缓存（与 ChatMessage.cachedSourceTable 一致） */
export interface ZnwdCachedSourceTable {
  envRows: EnvironmentTableRow[];
  environmentSources: ZnwdEnvironmentSourceRow[];
  equipmentHierarchyRows: ZnwdEquipmentHierarchyRow[];
  sourceViewMode: 'equipment_table' | 'environment' | 'equipment_hierarchy';
  involvesMethod: boolean;
  selectedProjects: SelectedProjectEntry[];
  selectedIndicators: SelectedIndicatorEntry[];
  selectedMethodIds: string[];
  highlightEquipmentIds: string[];
  highlightLevel2TypeIds: string[];
  highlightProjectIds: string[];
  highlightIndicatorIds: string[];
  keyword: string;
}

/** @deprecated 使用 ZnwdCachedSourceTable */
export type SourceTableCacheEntry = ZnwdCachedSourceTable;

const globalCache = new Map<string, ZnwdCachedSourceTable>();
const MAX_CACHE = 100;

export function buildSourceTableCacheKey(
  keyword: string,
  neo4jHits: { id?: string; properties?: Record<string, unknown> }[],
): string {
  const ids = (neo4jHits || [])
    .slice(0, 40)
    .map((h) => {
      const props = h.properties || {};
      return String(h.id || props.Id || props.id || props.Name || props.name || '').trim();
    })
    .filter(Boolean)
    .join('|');
  return `${(keyword || '').trim()}::${ids}`;
}

export function getGlobalSourceTableCache(key: string): ZnwdCachedSourceTable | undefined {
  return globalCache.get(key);
}

export function setGlobalSourceTableCache(key: string, val: ZnwdCachedSourceTable): void {
  globalCache.set(key, val);
  while (globalCache.size > MAX_CACHE) {
    const first = globalCache.keys().next().value;
    if (!first) break;
    globalCache.delete(first);
  }
}
