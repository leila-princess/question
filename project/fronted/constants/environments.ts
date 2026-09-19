export const ENVIRONMENT_ORDER: string[] = [
  '复杂电磁环境',  //复杂电磁环境
  '复杂地理环境',  //复杂地理环境
  '复杂气象环境',// 复杂气象环境
  '复杂水文环境',// 复杂水文环境
  '复杂水声环境',// 复杂水声环境
  '高原综合环境',// 高原综合环境
  '海上综合环境',// 海上综合环境
  '其他环境',// 其他环节
] as string[];

export type EnvironmentName = string;

type EnvironmentNode = { id?: string; name?: string };

function _sortByEnvId(a: EnvironmentNode, b: EnvironmentNode): number {
  const ai = String(a?.id || '');
  const bi = String(b?.id || '');
  const am = ai.match(/^HJ_(\d+)$/i);
  const bm = bi.match(/^HJ_(\d+)$/i);
  if (am && bm) return Number(am[1]) - Number(bm[1]);
  if (am) return -1;
  if (bm) return 1;
  return ai.localeCompare(bi, 'zh-CN');
}

function _replaceEnvironmentOrder(names: string[]) {
  const next = names.filter(Boolean);
  if (!next.length) return;
  ENVIRONMENT_ORDER.splice(0, ENVIRONMENT_ORDER.length, ...next);
}

export async function refreshEnvironmentOrder(): Promise<void> {
  try {
    const res = await fetch('/api/GLCX/JQCX/environments');
    const json = await res.json();
    const list: EnvironmentNode[] = Array.isArray(json?.data) ? json.data : [];
    const names = list.sort(_sortByEnvId).map((x) => String(x?.name || '')).filter(Boolean);
    _replaceEnvironmentOrder(names);
  } catch (error) {
    console.error('获取环境排序失败:', error);
  }
}

if (typeof window !== 'undefined') {
  void refreshEnvironmentOrder();
}

export const ENVIRONMENT_MAP: Record<string, { label: string; color: string }> = {
  '电磁环境': { label: '电磁环境', color: '#ef4444' },
  '地理环境': { label: '地理环境', color: '#3b82f6' },
  '气象环境': { label: '气象环境', color: '#10b981' },
  '水文环境': { label: '水文环境', color: '#6366f1' },
  '水声环境': { label: '水声环境', color: '#f59e0b' },
  '高原环境': { label: '高原环境', color: '#d97706' },
  '海上环境': { label: '海上环境', color: '#0ea5e9' },
  '其他环境': { label: '其他环境', color: '#8b5cf6' },
};

export function sortEnvironments(envNames: string[]): string[] {
  return ENVIRONMENT_ORDER.filter((env) => envNames.includes(env));
}

/** 左侧环境分类树排序：与 Neo4j 环境名称（如「复杂电磁环境」）按 ENVIRONMENT_ORDER 对齐 */
export function environmentDisplayOrderIndex(name: string): number {
  const n = (name || '').trim();
  for (let i = 0; i < ENVIRONMENT_ORDER.length; i++) {
    const ord = ENVIRONMENT_ORDER[i];
    if (!ord) continue;
    if (n === ord) return i;
    if (n.includes(ord)) return i;
    const short = ord.replace(/环境$/, '');
    if (short.length >= 2 && n.includes(short)) return i;
  }
  return ENVIRONMENT_ORDER.length + 2;
}

export function getAllEnvironments(): readonly string[] {
  return ENVIRONMENT_ORDER;
}