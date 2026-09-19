/** Human-readable labels for graph source nodes in ZNWD drawer. */

const LABEL_ZH: Record<string, string> = {
  EquipmentType: '设备类型',
  Equipment: '装备',
  TestProject: '试验项目',
  Indicator: '指标',
  TestMethod: '试验方法',
  Environment: '试验环境',
  File: '标准文件',
};

const HIDDEN_PROP_KEYS = new Set([
  'Id',
  'id',
  'EquipmentTypeId',
  'equipmentTypeId',
  'Status',
  'status',
  'User_name',
  'Create_time',
]);

export function nodeTypeZh(labels?: string[] | string): string {
  const lbl = Array.isArray(labels) ? labels[0] : labels;
  return LABEL_ZH[String(lbl || '')] || String(lbl || '节点');
}

export function formatNodeTitle(labels?: string[] | string, properties?: Record<string, unknown>): string {
  const lbl = Array.isArray(labels) ? labels[0] : labels;
  const props = properties || {};
  const name = String(props.Name ?? props.name ?? '').trim();
  const typeVal = String(props.Type ?? props.type ?? '').trim();
  if (lbl === 'EquipmentType' && name) {
    if (typeVal && !name.includes(typeVal)) return `${typeVal} · ${name}`;
    return name;
  }
  if (name) return name;
  if (typeVal) return typeVal;
  const id = String(props.Id ?? props.id ?? '').trim();
  return id ? `${nodeTypeZh(lbl)} ${id}` : nodeTypeZh(lbl);
}

export function formatNodePropertiesForDisplay(
  labels?: string[] | string,
  properties?: Record<string, unknown>
): { key: string; label: string; value: string }[] {
  const props = properties || {};
  const lbl = Array.isArray(labels) ? labels[0] : labels;
  const out: { key: string; label: string; value: string }[] = [];
  const priority = ['Name', 'Type', 'Description', 'Label', 'Class'];
  const seen = new Set<string>();

  for (const k of priority) {
    const v = props[k];
    if (v == null || String(v).trim() === '') continue;
    seen.add(k);
    out.push({ key: k, label: propLabelZh(k), value: String(v) });
  }

  for (const [k, v] of Object.entries(props)) {
    if (seen.has(k) || HIDDEN_PROP_KEYS.has(k)) continue;
    if (v == null || String(v).trim() === '') continue;
    out.push({ key: k, label: propLabelZh(k), value: String(v).slice(0, 500) });
  }

  if (lbl === 'EquipmentType' && !seen.has('Type') && props.Type) {
    out.unshift({ key: 'Type', label: '一级类别', value: String(props.Type) });
  }

  return out;
}

function propLabelZh(key: string): string {
  const map: Record<string, string> = {
    Name: '名称',
    Type: '类别',
    Description: '描述',
    Label: '标签',
    Class: '分类',
  };
  return map[key] || key;
}

export function formatNodeId(properties?: Record<string, unknown>): string {
  return String(properties?.Id ?? properties?.id ?? '').trim();
}
