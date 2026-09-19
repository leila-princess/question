export interface GraphLinkHint {
  node_type: string;
  node_id: string;
  node_name: string;
  confidence?: number;
  reason?: string;
}

export interface SemanticTreeNode {
  node_id: string;
  title: string;
  summary?: string;
  section_ids?: string[];
  graph_link?: GraphLinkHint;
  graph_link_hint?: string;
  children?: SemanticTreeNode[];
}

export interface SemanticTreeData {
  document_class?: string;
  generated_at?: string;
  tree_source?: 'fallback' | 'llm' | 'cached';
  root: SemanticTreeNode;
}

export interface EquipmentTabLinkRow {
  key: string;
  node_type: string;
  /** 多选关联节点 id（与 page-pre 上传关联表一致） */
  node_ids: string[];
  nodes: { id: string; name: string }[];
  /** 关联度（0~1，保留两位小数） */
  rate?: number | null;
}

export interface EquipmentTab {
  key: string;
  equipment_id: string;
  equipment_name: string;
  rows: EquipmentTabLinkRow[];
}

export const EQUIPMENT_LINK_NODE_TYPES = [
  { value: 'TestProject', label: '项目', color: '#fa8c16' },
  { value: 'Environment', label: '环境', color: '#722ed1' },
  { value: 'Indicator', label: '指标', color: '#13c2c2' },
  { value: 'TestMethod', label: '方法', color: '#eb2f96' },
] as const;

export interface EquipmentTabLink {
  node_type: string;
  node_id: string;
  node_name: string;
  rel_type?: string;
  rate?: number | null;
}

export interface EnvironmentLink {
  node_id: string;
  node_name: string;
}

export interface GraphRecommendation {
  node_type: string;
  node_id: string;
  node_name: string;
  score?: number;
  status?: string;
  reason?: string;
}

export function linkKey(nodeType: string, nodeId: string) {
  return `${nodeType}:${nodeId}`;
}

export function tabToConfirmLinks(tab: EquipmentTab): EquipmentTabLink[] {
  const links: EquipmentTabLink[] = [];
  for (const r of tab.rows || []) {
    if (!r.node_type) continue;
    const ids = r.node_ids?.length
      ? r.node_ids
      : (r as EquipmentTabLinkRow & { node_id?: string }).node_id
        ? [(r as EquipmentTabLinkRow & { node_id?: string }).node_id!]
        : [];
    const nameMap = Object.fromEntries((r.nodes || []).map(n => [n.id, n.name]));
    for (const nid of ids) {
      if (!nid) continue;
      const isNewName = String(nid).startsWith('__new__');
      links.push({
        node_type: r.node_type,
        node_id: isNewName ? '' : nid,
        node_name: isNewName ? String(nid).slice('__new__'.length) : (nameMap[nid] || nid),
        rate: r.rate ?? null,
      });
    }
  }
  return links;
}

/** 根据推荐装备自动创建设备 Tab（仅 Equipment，不含 EquipmentType） */
export function applyRecommendationsToTabs(
  tabs: EquipmentTab[],
  recommendations: GraphRecommendation[],
  rootGraphLink?: GraphLinkHint | null,
): EquipmentTab[] {
  let next = [...tabs];

  const ensureTab = (eqId: string, eqName: string) => {
    if (!eqId || next.some(t => t.equipment_id === eqId)) return;
    next.push({
      key: eqId,
      equipment_id: eqId,
      equipment_name: eqName || eqId,
      rows: [],
    });
  };

  const equipRecs = recommendations.filter(r => r.node_id && r.node_type === 'Equipment');
  for (const r of equipRecs) {
    ensureTab(r.node_id, r.node_name);
  }

  if (rootGraphLink?.node_type === 'Equipment' && rootGraphLink.node_id) {
    ensureTab(rootGraphLink.node_id, rootGraphLink.node_name || rootGraphLink.node_id);
  }

  return next;
}

/** 推荐设备类型（需用户再选具体 Equipment） */
export function pickRecommendedEquipmentType(
  recommendations: GraphRecommendation[],
): GraphRecommendation | null {
  const types = recommendations.filter(r => r.node_type === 'EquipmentType' && r.node_id);
  if (!types.length) return null;
  return [...types].sort((a, b) => (b.score || 0) - (a.score || 0))[0];
}

export function collectSectionIds(node: SemanticTreeNode | undefined): string[] {
  if (!node) return [];
  const ids: string[] = [];
  for (const sid of node.section_ids || []) {
    if (sid && !ids.includes(sid)) ids.push(sid);
  }
  for (const ch of node.children || []) {
    for (const sid of collectSectionIds(ch)) {
      if (!ids.includes(sid)) ids.push(sid);
    }
  }
  return ids;
}

export function treeToAntData(node: SemanticTreeNode): { key: string; title: string; children?: ReturnType<typeof treeToAntData>[] } {
  return {
    key: node.node_id,
    title: node.title,
    children: (node.children || []).map(treeToAntData),
  };
}

export function flattenTreeNodes(node: SemanticTreeNode): SemanticTreeNode[] {
  const out: SemanticTreeNode[] = [node];
  for (const ch of node.children || []) {
    out.push(...flattenTreeNodes(ch));
  }
  return out;
}

export function mergeNodeHtml(
  node: SemanticTreeNode | undefined,
  nodeSlices: Record<string, string>,
  sections: { section_id: string; html?: string; text?: string }[],
): string {
  if (!node) return '';
  const secMap = Object.fromEntries(sections.map(s => [s.section_id, s]));
  const parts: string[] = [];
  for (const sid of collectSectionIds(node)) {
    if (nodeSlices[sid]) parts.push(nodeSlices[sid]);
    else if (secMap[sid]?.html) parts.push(secMap[sid].html!);
    else if (secMap[sid]?.text) parts.push(`<p>${secMap[sid].text}</p>`);
  }
  if (parts.length) return parts.join('');
  if (node.summary) return `<p>${node.summary}</p>`;
  return '';
}
