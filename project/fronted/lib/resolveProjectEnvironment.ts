/** 配置表/方案生成：解析试验项目所属环境名（非「试验环境」等占位） */

const GENERIC_ENV_KEYS = new Set(['未分组', '其他环境', '试验环境', '未知环境']);

export function pickFirstEnvSegment(raw: string): string {
  return (raw || '').split('、')[0].trim();
}

export function isGenericEnvironmentName(name: string): boolean {
  const n = (name || '').trim();
  return !n || GENERIC_ENV_KEYS.has(n);
}

/**
 * 从图库返回的 category/test 节点解析环境名：
 * - 优先 child._env（Environment.Name，可能多个用「、」连接）
 * - 其次父级 category._env / Label（8 类环境分类名）
 */
export function resolveProjectEnvironmentName(child: Record<string, unknown>, category?: Record<string, unknown>): string {
  const fromChild = pickFirstEnvSegment(String(child?._env || child?.env_name || ''));
  if (fromChild && !isGenericEnvironmentName(fromChild)) return fromChild;

  const cat = category || child;
  const fromCat = pickFirstEnvSegment(String(cat?._env || ''));
  if (fromCat && !isGenericEnvironmentName(fromCat)) return fromCat;

  const lbl = String(cat?.label || cat?.Label || '').trim();
  const catType = String(cat?.type || '').toLowerCase();
  if (lbl && catType === 'category' && !isGenericEnvironmentName(lbl)) return lbl;

  return fromChild || fromCat || lbl || '';
}
