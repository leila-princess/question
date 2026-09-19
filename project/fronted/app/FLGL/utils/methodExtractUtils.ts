import type { DataNode } from 'antd/es/tree';
import type { TocTreeNode } from '@/app/ZSGL/WJGL/components/documentTocUtils';
import { flattenTocTree } from '@/app/ZSGL/WJGL/components/documentTocUtils';
import type { EquipmentTab, GraphRecommendation } from '@/app/ZSGL/WJGL/components/semanticTypes';
import { applyRecommendationsToTabs } from '@/app/ZSGL/WJGL/components/semanticTypes';
import type { EquipmentTreeNode } from '../api';

export type ExtractSectionGroup = {
  parent: {
    sectionNo: string;
    title: string;
    level: number;
    section_id?: string;
    source?: 'toc' | 'parser';
  };
  children: Array<{
    sectionNo: string;
    title: string;
    level: number;
    section_id?: string;
    source?: 'toc' | 'parser';
  }>;
};

export type HeadingNodeSource = 'toc' | 'parser';

export interface FullHeadingItem {
  section_id?: string;
  sectionNo?: string;
  title: string;
  level: number;
  start_page?: number;
  end_page?: number;
  snippet?: string;
  source?: HeadingNodeSource;
}

/** 用户手动定义的一个待提取方法（1 条 = 1 个方法） */
export interface MethodExtractDraft {
  id: string;
  rootKey: string;
  rootSectionNo: string;
  rootTitle: string;
  rootLevel: number;
  /** 除根节外，用户勾选的子节 key */
  childKeys: string[];
}

function newDraftId() {
  return `draft-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`;
}

export function findTocNodeByKey(nodes: TocTreeNode[], key: string): TocTreeNode | undefined {
  for (const n of nodes) {
    if (String(n.key) === key) return n;
    if (n.children?.length) {
      const found = findTocNodeByKey(n.children as TocTreeNode[], key);
      if (found) return found;
    }
  }
  return undefined;
}

export function collectDescendantKeys(node: TocTreeNode): string[] {
  const keys: string[] = [];
  for (const ch of (node.children || []) as TocTreeNode[]) {
    keys.push(String(ch.key));
    keys.push(...collectDescendantKeys(ch));
  }
  return keys;
}

export function nodeToSectionMeta(node: TocTreeNode & { section_id?: string; source?: HeadingNodeSource }) {
  return {
    sectionNo: node.sectionNo || '',
    title: String(node.rawTitle || node.title || ''),
    level: node.level || 1,
    section_id: node.section_id,
    source: node.source || 'toc',
  };
}

/** 由 parser full_headings 构建层级 Tree（与 TOC 树节点结构兼容，保持 04 数组顺序） */
export function buildParserHeadingTree(headings: FullHeadingItem[]): TocTreeNode[] {
  const roots: TocTreeNode[] = [];
  const stack: Array<{ level: number; node: TocTreeNode }> = [];
  (headings || []).forEach((h, idx) => {
    const level = Math.max(1, Number(h.level) || 1);
    const key = `parser:${idx}:${h.section_id || h.sectionNo || h.title}`;
    const node = {
      key,
      title: h.sectionNo ? `${h.sectionNo} ${h.title}`.trim() : h.title,
      rawTitle: h.title,
      sectionNo: h.sectionNo || '',
      level,
      children: [] as TocTreeNode[],
      section_id: h.section_id,
      source: 'parser' as const,
    } as TocTreeNode & { section_id?: string; source?: HeadingNodeSource };
    while (stack.length && stack[stack.length - 1].level >= level) {
      stack.pop();
    }
    if (!stack.length) {
      roots.push(node);
    } else {
      const parent = stack[stack.length - 1].node;
      (parent.children as TocTreeNode[]).push(node);
    }
    stack.push({ level, node });
  });
  return roots;
}

/** 从目录节点创建一条待提取方法（默认仅根节，不含自动子节） */
export function createMethodDraftFromNode(node: TocTreeNode, childKeys: string[] = []): MethodExtractDraft {
  const meta = nodeToSectionMeta(node);
  return {
    id: newDraftId(),
    rootKey: String(node.key),
    rootSectionNo: meta.sectionNo,
    rootTitle: meta.title,
    rootLevel: meta.level,
    childKeys: childKeys.filter((k) => k !== String(node.key)),
  };
}

function buildTocParentMap(tocTree: TocTreeNode[]): Map<string, TocTreeNode> {
  const parentMap = new Map<string, TocTreeNode>();
  const walk = (nodes: TocTreeNode[], parent?: TocTreeNode) => {
    for (const node of nodes) {
      if (parent) parentMap.set(String(node.key), parent);
      if (node.children?.length) walk(node.children as TocTreeNode[], node);
    }
  };
  walk(tocTree);
  return parentMap;
}

/** 在已勾选 key 中筛出顶层节点（其父节点未被勾选） */
export function getTopLevelCheckedKeys(checkedKeys: string[], tocTree: TocTreeNode[]): string[] {
  if (!checkedKeys.length) return [];
  const checkedSet = new Set(checkedKeys);
  const parentMap = buildTocParentMap(tocTree);
  return checkedKeys.filter((key) => {
    const parent = parentMap.get(key);
    return !parent || !checkedSet.has(String(parent.key));
  });
}

/**
 * 将当前一批目录勾选合并为一条待提取方法（一次「添加为一个方法」= 一个方法）。
 * 取文档顺序最靠前的顶层节点为根，其余勾选节点记入 childKeys。
 */
export function createMethodDraftFromSelection(
  checkedKeys: string[],
  tocTree: TocTreeNode[],
): MethodExtractDraft | null {
  if (!checkedKeys.length || !tocTree.length) return null;

  const allNodes = flattenTocTree(tocTree);
  const order = new Map(allNodes.map((n, i) => [String(n.key), i]));
  const topLevelKeys = getTopLevelCheckedKeys(checkedKeys, tocTree).sort(
    (a, b) => (order.get(a) ?? 0) - (order.get(b) ?? 0),
  );
  if (!topLevelKeys.length) return null;

  const rootKey = topLevelKeys[0];
  const root = findTocNodeByKey(tocTree, rootKey);
  if (!root) return null;

  const childKeys = checkedKeys.filter((k) => k !== rootKey).sort(
    (a, b) => (order.get(a) ?? 0) - (order.get(b) ?? 0),
  );

  const draft = createMethodDraftFromNode(root, childKeys);
  if (topLevelKeys.length > 1) {
    const titles = topLevelKeys
      .map((k) => findTocNodeByKey(tocTree, k))
      .filter(Boolean)
      .map((n) => {
        const meta = nodeToSectionMeta(n!);
        return meta.sectionNo ? `${meta.sectionNo} ${meta.title}`.trim() : meta.title;
      });
    draft.rootTitle = titles.join('、');
    draft.rootSectionNo = '';
  }
  return draft;
}

/** 单条 draft → API sections 分组 */
export function draftToSectionGroup(
  draft: MethodExtractDraft,
  tocTree: TocTreeNode[],
): ExtractSectionGroup | null {
  const root = findTocNodeByKey(tocTree, draft.rootKey);
  if (!root) return null;
  const parent = nodeToSectionMeta(root);
  if (draft.rootTitle) {
    parent.title = draft.rootTitle;
  }
  if (draft.rootSectionNo === '' && draft.childKeys.length > 0) {
    parent.sectionNo = '';
  }
  const children = (draft.childKeys || [])
    .map((k) => findTocNodeByKey(tocTree, k))
    .filter(Boolean)
    .map((n) => nodeToSectionMeta(n!));
  return { parent, children };
}

/** 待提取队列 → API sections（长度 = 方法个数，由用户显式决定） */
export function draftsToSectionGroups(
  drafts: MethodExtractDraft[],
  tocTree: TocTreeNode[],
): ExtractSectionGroup[] {
  return drafts
    .map((d) => draftToSectionGroup(d, tocTree))
    .filter((g): g is ExtractSectionGroup => g != null);
}

/** @deprecated 自动按勾选分组；请改用 draftsToSectionGroups */
export function groupSectionsForExtract(
  checkedKeys: string[],
  tocTree: TocTreeNode[],
): ExtractSectionGroup[] {
  if (!checkedKeys.length || !tocTree.length) return [];

  const allNodes = flattenTocTree(tocTree);
  const checkedNodes = allNodes.filter((n) => checkedKeys.includes(String(n.key)));

  const parentMap = new Map<string, TocTreeNode>();
  const buildParentMap = (nodes: TocTreeNode[], parent?: TocTreeNode) => {
    for (const node of nodes) {
      if (parent) parentMap.set(String(node.key), parent);
      if (node.children?.length) buildParentMap(node.children as TocTreeNode[], node);
    }
  };
  buildParentMap(tocTree);

  const checkedKeysSet = new Set(checkedKeys);
  const topLevelSelected: TocTreeNode[] = [];
  const childOfSelected: TocTreeNode[] = [];

  for (const node of checkedNodes) {
    const parent = parentMap.get(String(node.key));
    if (parent && checkedKeysSet.has(String(parent.key))) {
      childOfSelected.push(node);
    } else {
      topLevelSelected.push(node);
    }
  }

  const grouped: Record<string, ExtractSectionGroup> = {};
  for (const node of topLevelSelected) {
    const key = String(node.key);
    grouped[key] = {
      parent: {
        sectionNo: node.sectionNo || '',
        title: String(node.rawTitle || node.title || ''),
        level: node.level || 1,
      },
      children: [],
    };
  }

  for (const child of childOfSelected) {
    let cur: TocTreeNode | undefined = child;
    while (cur) {
      const p = parentMap.get(String(cur.key));
      if (!p) break;
      const parentKey = String(p.key);
      if (grouped[parentKey]) {
        grouped[parentKey].children.push({
          sectionNo: child.sectionNo || '',
          title: String(child.rawTitle || child.title || ''),
          level: child.level || 1,
        });
        break;
      }
      cur = p;
    }
  }

  return Object.values(grouped);
}

export function findEquipmentNameInTree(
  tree: EquipmentTreeNode[] | undefined,
  equipmentId: string,
): string {
  if (!tree?.length || !equipmentId) return equipmentId;
  for (const l1 of tree) {
    for (const l2 of l1.children || []) {
      for (const l3 of (l2 as EquipmentTreeNode).children || []) {
        const n = l3 as EquipmentTreeNode;
        if (String(n.id) === equipmentId) {
          return String(n.name || n.label || equipmentId);
        }
      }
    }
  }
  return equipmentId;
}

export function buildInitialEquipmentTabs(params: {
  graphCandidates?: GraphRecommendation[];
  selectedEquipmentId?: string | null;
  selectedEquipmentName?: string;
  equipmentRecommendations?: Array<{
    equipment_id: string;
    equipment_name: string;
    score?: number;
    projects?: Array<{ node_id: string; node_name?: string; score?: number }>;
    indicators?: Array<{ node_id: string; node_name?: string; score?: number }>;
  }>;
  /** LLM 推荐默认每类最多 3 条；加载已保存方法时传 0 表示不限制 */
  rowLimit?: number;
}): EquipmentTab[] {
  const {
    graphCandidates = [],
    selectedEquipmentId,
    selectedEquipmentName,
    equipmentRecommendations,
    rowLimit = 3,
  } = params;

  const buildRowsFromRec = (
    eqRec?: {
      projects?: Array<{ node_id: string; node_name?: string; score?: number }>;
      indicators?: Array<{ node_id: string; node_name?: string; score?: number }>;
    },
  ): EquipmentTab['rows'] => {
    const rows: EquipmentTab['rows'] = [];
    const projects = rowLimit > 0 ? (eqRec?.projects || []).slice(0, rowLimit) : (eqRec?.projects || []);
    const indicators = rowLimit > 0 ? (eqRec?.indicators || []).slice(0, rowLimit) : (eqRec?.indicators || []);
    if (projects.length) {
      for (const p of projects) {
        const r = p.score != null && !Number.isNaN(Number(p.score)) ? Number(Number(p.score).toFixed(2)) : null;
        rows.push({
          key: `row-proj-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
          node_type: 'TestProject',
          node_ids: [p.node_id],
          nodes: [{ id: p.node_id, name: p.node_name || p.node_id }],
          rate: r,
        });
      }
    }
    if (indicators.length) {
      for (const i of indicators) {
        const r = i.score != null && !Number.isNaN(Number(i.score)) ? Number(Number(i.score).toFixed(2)) : null;
        rows.push({
          key: `row-ind-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
          node_type: 'Indicator',
          node_ids: [i.node_id],
          nodes: [{ id: i.node_id, name: i.node_name || i.node_id }],
          rate: r,
        });
      }
    }
    return rows;
  };

  if (equipmentRecommendations?.length) {
    return equipmentRecommendations.map((eq) => ({
      key: `eq-${eq.equipment_id}`,
      equipment_id: eq.equipment_id,
      equipment_name: eq.equipment_name || eq.equipment_id,
      rows: buildRowsFromRec(eq),
    }));
  }

  if (selectedEquipmentId) {
    const eqRec = equipmentRecommendations?.find((e) => e.equipment_id === selectedEquipmentId);
    return [
      {
        key: `eq-${selectedEquipmentId}`,
        equipment_id: selectedEquipmentId,
        equipment_name: selectedEquipmentName || eqRec?.equipment_name || selectedEquipmentId,
        rows: buildRowsFromRec(eqRec),
      },
    ];
  }

  const fromRecs: GraphRecommendation[] =
    equipmentRecommendations?.map((e) => ({
      node_type: 'Equipment',
      node_id: e.equipment_id,
      node_name: e.equipment_name,
      score: e.score,
    })) || [];

  const merged = fromRecs.length ? fromRecs : graphCandidates;
  return applyRecommendationsToTabs([], merged);
}

export function flattenTocForTree(nodes: TocTreeNode[]): DataNode[] {
  return nodes as unknown as DataNode[];
}

/** 图库 File 节点 Id 统一为 WJ_ 前缀 */
export function normalizeFileNodeId(id?: string): string {
  const s = String(id || '').trim();
  if (!s) return '';
  if (s.toUpperCase().startsWith('WJ_')) return s;
  return `WJ_${s.replace(/^WJ_/i, '')}`;
}

export function serializeSourceSections(value: unknown): string {
  if (Array.isArray(value)) {
    return JSON.stringify(value.filter((x) => String(x || '').trim()));
  }
  if (typeof value === 'string' && value.trim()) {
    try {
      const parsed = JSON.parse(value);
      if (Array.isArray(parsed)) return JSON.stringify(parsed);
    } catch {
      /* keep raw below */
    }
    return value;
  }
  return '[]';
}

export function firstSourceSectionNo(value: unknown): string {
  if (Array.isArray(value)) {
    const hit = value.find((x) => String(x || '').trim());
    return hit ? String(hit).trim() : '';
  }
  if (typeof value === 'string' && value.trim()) {
    try {
      const parsed = JSON.parse(value);
      if (Array.isArray(parsed)) {
        const hit = parsed.find((x) => String(x || '').trim());
        return hit ? String(hit).trim() : '';
      }
    } catch {
      return value.trim();
    }
  }
  return '';
}

export function buildMethodSourceFileUrl(params: {
  sourceId?: string;
  fileIds?: string[];
  category?: string;
  sectionNo?: string;
}): string | null {
  const rawId = normalizeFileNodeId(params.sourceId || params.fileIds?.[0] || '');
  if (!rawId) return null;
  const docId = rawId.replace(/^WJ_/i, '');
  const category = String(params.category || '法规标准').trim() || '法规标准';
  const sectionNo = String(params.sectionNo || '').trim();
  const q = new URLSearchParams({ category });
  if (sectionNo) q.set('section', sectionNo);
  return `/ZSGL/WJGL/files/${encodeURIComponent(docId)}?${q.toString()}`;
}

/** FLGL/ZNWD 方法来源 → Standards 阅读页（左侧原文 + 右侧摘要/问答） */
export function buildMethodSourceReadUrl(params: {
  sourceId?: string;
  fileIds?: string[];
  sourceName?: string;
}): string | null {
  const rawId = normalizeFileNodeId(params.sourceId || params.fileIds?.[0] || '');
  if (!rawId) return null;
  const label = String(params.sourceName || '').trim() || rawId;
  const q = new URLSearchParams({
    source_id: rawId,
    source_name: label,
  });
  return `/Standards?${q.toString()}`;
}

/** 方法标识：来源文件名（去路径、去扩展名） */
export function labelFromSourceFilename(sourceName?: string): string {
  const base = String(sourceName || '').trim().replace(/^.*[\\/]/, '');
  if (!base) return '';
  const dot = base.lastIndexOf('.');
  if (dot > 0) return base.slice(0, dot);
  return base;
}

type MethodRelationRowLike = {
  key: string;
  targetType: string;
  targetId: string;
  targetLabel?: string;
  relType: string;
  rate?: number | null;
};

function newRelRowKey() {
  return `rel-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
}

/** 设备 Tab 关联 → 方法导入用的扁平 relation 行 */
export function equipmentTabsToRelationRows(tabs: EquipmentTab[]): MethodRelationRowLike[] {
  const rows: MethodRelationRowLike[] = [];
  const seen = new Set<string>();

  for (const tab of tabs || []) {
    const eqId = String(tab.equipment_id || '').trim();
    if (eqId) {
      const eqKey = `Equipment:${eqId}`;
      if (!seen.has(eqKey)) {
        seen.add(eqKey);
        rows.push({
          key: newRelRowKey(),
          targetType: 'Equipment',
          targetId: eqId,
          targetLabel: tab.equipment_name || eqId,
          relType: 'CON',
          rate: null,
        });
      }
    }

    for (const r of tab.rows || []) {
      const nodeType = String(r.node_type || '').trim();
      if (!nodeType || nodeType === 'Equipment') continue;
      const ids = r.node_ids?.length ? r.node_ids : [];
      const nameMap = Object.fromEntries((r.nodes || []).map((n) => [n.id, n.name]));
      for (const nid of ids) {
        if (!nid) continue;
        const dedupe = `${nodeType}:${nid}`;
        if (seen.has(dedupe)) continue;
        seen.add(dedupe);
        rows.push({
          key: newRelRowKey(),
          targetType: nodeType,
          targetId: nid,
          targetLabel: nameMap[nid] || nid,
          relType: nodeType === 'File' || nodeType === 'TestMethod' ? 'BELONGS_TO' : 'CON',
          rate: r.rate ?? null,
        });
      }
    }
  }

  return rows;
}

export function mergeRelationRowsWithEquipmentTabs(
  equipmentTabs: EquipmentTab[],
  extraRows: MethodRelationRowLike[] = [],
): MethodRelationRowLike[] {
  const fromTabs = equipmentTabsToRelationRows(equipmentTabs);
  const extraTypes = new Set(['File', 'TestMethod']);
  const extras = extraRows.filter((r) => extraTypes.has(r.targetType) && r.targetId);
  const tabKeys = new Set(fromTabs.map((r) => `${r.targetType}:${r.targetId}`));
  const merged = [...fromTabs];
  for (const r of extras) {
    const k = `${r.targetType}:${r.targetId}`;
    if (!tabKeys.has(k)) merged.push(r);
  }
  return merged;
}

type GraphRelationLike = {
  rel_type: string;
  target_labels: string[];
  target_id: string;
  target_name: string;
  direction: 'outgoing' | 'incoming';
  rate?: number | null;
};

const METHOD_GRAPH_LINK_LABELS = new Set([
  'Equipment',
  'TestProject',
  'Indicator',
  'Environment',
  'TestMethod',
  'File',
]);

/** 从图库 relations API 还原全部关联行（设备/项目/指标/环境/文件等） */
export function rowsFromAllMethodGraphRelations(relations: GraphRelationLike[]): MethodRelationRowLike[] {
  const out: MethodRelationRowLike[] = [];
  let seq = 0;
  for (const r of relations || []) {
    const rel = String(r.rel_type || '').toUpperCase();
    if (!['CON', 'HAS', 'BELONGS_TO'].includes(rel)) continue;
    const labels = r.target_labels || [];
    const tt = labels.find((l) => METHOD_GRAPH_LINK_LABELS.has(l));
    if (!tt || !r.target_id) continue;
    const rawRate = r.rate;
    const n = rawRate != null && rawRate !== ('' as unknown) ? Number(rawRate) : null;
    const relType =
      rel === 'BELONGS_TO' || tt === 'File' || tt === 'TestMethod' ? 'BELONGS_TO' : rel;
    out.push({
      key: `rel-${Date.now()}-${seq++}`,
      targetType: tt,
      targetId: String(r.target_id),
      targetLabel: r.target_name || String(r.target_id),
      relType,
      rate: n != null && !Number.isNaN(n) ? n : null,
    });
  }
  return out;
}

/** 将方法节点的图库关联还原为 DeviceLinkTabs 所需的设备 Tab 结构 */
export function buildEquipmentTabsFromMethodRelations(relations: GraphRelationLike[]): EquipmentTab[] {
  const UNASSIGNED = '__unassigned__';
  const tabs = new Map<string, EquipmentTab>();
  let rowSeq = 0;

  const ensureTab = (eqId: string, eqName: string): EquipmentTab => {
    if (!tabs.has(eqId)) {
      tabs.set(eqId, {
        key: `eq-${eqId}`,
        equipment_id: eqId,
        equipment_name: eqName || eqId,
        rows: [],
      });
    }
    return tabs.get(eqId)!;
  };

  const addLink = (
    tab: EquipmentTab,
    nodeType: string,
    nodeId: string,
    nodeName: string,
    rate?: number | null,
  ) => {
    if (!nodeId || !nodeType || nodeType === 'Equipment') return;
    let row = tab.rows.find((r) => r.node_type === nodeType);
    if (!row) {
      row = {
        key: `row-${Date.now()}-${rowSeq++}`,
        node_type: nodeType,
        node_ids: [],
        nodes: [],
        rate: rate ?? null,
      };
      tab.rows.push(row);
    }
    if (!row.node_ids.includes(nodeId)) {
      row.node_ids.push(nodeId);
      row.nodes.push({ id: nodeId, name: nodeName || nodeId });
    }
    if (rate != null && row.rate == null) row.rate = rate;
  };

  for (const r of relations || []) {
    const labels = r.target_labels || [];
    if (labels.includes('Equipment') && r.target_id) {
      ensureTab(r.target_id, r.target_name || r.target_id);
    }
  }

  const pickTab = (): EquipmentTab => {
    if (tabs.size === 1) return [...tabs.values()][0];
    if (tabs.size === 0) return ensureTab(UNASSIGNED, '未绑定设备');
    return ensureTab(UNASSIGNED, '未绑定设备');
  };

  for (const r of relations || []) {
    const rel = String(r.rel_type || '').toUpperCase();
    if (!['CON', 'HAS', 'BELONGS_TO'].includes(rel)) continue;
    const labels = r.target_labels || [];
    const nodeType = labels.find((l) => METHOD_GRAPH_LINK_LABELS.has(l) && l !== 'Equipment');
    if (!nodeType || !r.target_id) continue;
    if (nodeType === 'File' || nodeType === 'TestMethod') continue;
    const tab = pickTab();
    addLink(tab, nodeType, r.target_id, r.target_name || r.target_id, r.rate ?? null);
  }

  return [...tabs.values()];
}

/** 编辑已保存方法：优先 API 还原，缺失时用图库 relations 补全 Tab 行 */
export function mergeSavedMethodEquipmentTabs(
  fromApi: EquipmentTab[],
  fromRelations: EquipmentTab[],
): EquipmentTab[] {
  if (!fromApi.length) return fromRelations;
  if (!fromRelations.length) return fromApi;

  const relMap = new Map(fromRelations.map((t) => [t.equipment_id, t]));
  let rowSeq = 0;

  const mergeRows = (primary: EquipmentTab['rows'], secondary: EquipmentTab['rows']) => {
    const out = [...(primary || [])];
    const seen = new Set(out.flatMap((r) => (r.node_ids || []).map((id) => `${r.node_type}:${id}`)));
    for (const r of secondary || []) {
      for (const nid of r.node_ids || []) {
        const k = `${r.node_type}:${nid}`;
        if (seen.has(k)) continue;
        seen.add(k);
        let row = out.find((x) => x.node_type === r.node_type);
        if (!row) {
          row = {
            key: `row-${Date.now()}-${rowSeq++}`,
            node_type: r.node_type,
            node_ids: [],
            nodes: [],
            rate: r.rate ?? null,
          };
          out.push(row);
        }
        row.node_ids.push(nid);
        const node = (r.nodes || []).find((n) => n.id === nid);
        row.nodes.push(node || { id: nid, name: nid });
        if (row.rate == null && r.rate != null) row.rate = r.rate;
      }
    }
    return out;
  };

  return fromApi.map((tab) => {
    const fallback = relMap.get(tab.equipment_id);
    if (!fallback?.rows?.length) return tab;
    if (!tab.rows?.length) return { ...tab, rows: fallback.rows };
    return { ...tab, rows: mergeRows(tab.rows, fallback.rows) };
  });
}

export function graphCandidatesFromMethodRelations(
  relations: GraphRelationLike[],
): GraphRecommendation[] {
  const out: GraphRecommendation[] = [];
  const seen = new Set<string>();
  for (const r of relations || []) {
    const labels = r.target_labels || [];
    const nodeType = labels.find((l) => METHOD_GRAPH_LINK_LABELS.has(l));
    const nodeId = String(r.target_id || '').trim();
    if (!nodeType || !nodeId) continue;
    const key = `${nodeType}:${nodeId}`;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push({
      node_type: nodeType,
      node_id: nodeId,
      node_name: r.target_name || nodeId,
      score: r.rate ?? undefined,
    });
  }
  return out;
}
