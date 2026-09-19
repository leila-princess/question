/** 方案默认命名：装备类型 + 时间；重名时末尾追加 1、2、3… */

export interface SchemeNameRecord {
  task_id?: string;
  scheme_name?: string | null;
}

function sanitizeNamePart(value: string): string {
  return value.replace(/[/\\:*?"<>|\s]+/g, '').trim();
}

function formatSchemeTimeSuffix(date = new Date()): string {
  const p = (n: number) => String(n).padStart(2, '0');
  return `${date.getFullYear()}${p(date.getMonth() + 1)}${p(date.getDate())}_${p(date.getHours())}${p(date.getMinutes())}${p(date.getSeconds())}`;
}

/** 从已选设备节点解析装备类型标签 */
export function resolveEquipmentTypeLabel(
  equipmentNodes: Array<{ type?: string; category?: string; name?: string }>,
  deviceDisplayName?: string,
): string {
  const types = [
    ...new Set(
      equipmentNodes
        .map((n) => sanitizeNamePart(String(n.type || '').trim()))
        .filter(Boolean),
    ),
  ];
  if (types.length === 1) return types[0];
  if (types.length > 1) return types.join('');
  const display = sanitizeNamePart(String(deviceDisplayName || '').trim());
  if (display) return display;
  const category = sanitizeNamePart(String(equipmentNodes[0]?.category || '').trim());
  if (category) return category;
  return '方案';
}

export function buildDefaultSchemeSaveName(
  equipmentNodes: Array<{ type?: string; category?: string; name?: string }>,
  deviceDisplayName?: string,
  date = new Date(),
): string {
  const typeLabel = resolveEquipmentTypeLabel(equipmentNodes, deviceDisplayName);
  return `${typeLabel}${formatSchemeTimeSuffix(date)}`;
}

/** 与已有方案名去重，重复时在末尾加 1、2、3… */
export function resolveUniqueSchemeName(
  baseName: string,
  records: SchemeNameRecord[],
  excludeTaskId?: string,
): string {
  const trimmedBase = baseName.trim();
  if (!trimmedBase) return trimmedBase;

  const existing = new Set(
    records
      .filter((r) => !excludeTaskId || r.task_id !== excludeTaskId)
      .map((r) => String(r.scheme_name || '').trim())
      .filter(Boolean),
  );

  if (!existing.has(trimmedBase)) return trimmedBase;

  let suffix = 1;
  while (existing.has(`${trimmedBase}${suffix}`)) {
    suffix += 1;
  }
  return `${trimmedBase}${suffix}`;
}
