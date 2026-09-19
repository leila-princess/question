export type LevelTypeLabel = '父级' | '子级';

export const LEVEL_TYPE_FILTER_OPTIONS: { label: LevelTypeLabel; value: LevelTypeLabel }[] = [
  { label: '父级', value: '父级' },
  { label: '子级', value: '子级' },
];

/** 将图谱 type 字段归一化为「父级 / 子级」展示标签 */
export function resolveLevelTypeLabel(val: unknown): LevelTypeLabel {
  const v = String(val || '').trim();
  if (v === 'category' || v === '父级') return '父级';
  return '子级';
}

export function matchesLevelTypeFilter(recordType: unknown, filter?: LevelTypeLabel): boolean {
  if (!filter) return true;
  return resolveLevelTypeLabel(recordType) === filter;
}
