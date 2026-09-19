import type { EnvironmentTableRow, IndicatorNode } from '@/components/EnvironmentTable';

export interface SearchGraphNode {
  id: string;
  label: string;
  group: string;
  properties?: Record<string, unknown>;
}

export interface SearchGraphEdge {
  id: string;
  from: string;
  to: string;
  label: string;
}

export interface SearchGraphData {
  nodes: SearchGraphNode[];
  edges: SearchGraphEdge[];
}

/** 将打平或树形指标统一为根节点森林（含 type=category 无子级孤节点） */
function normalizeIndicatorRoots(indicators: IndicatorNode[]): IndicatorNode[] {
  if (!indicators.length) return [];

  const nestedChildIds = new Set<string>();
  for (const ind of indicators) {
    for (const ch of ind.children || []) {
      if (ch.id) nestedChildIds.add(ch.id);
    }
  }

  if (nestedChildIds.size > 0) {
    const roots = indicators.filter((i) => i.id && !nestedChildIds.has(i.id));
    if (roots.length > 0) return roots;
  }

  const byId = new Map<string, IndicatorNode & { children: IndicatorNode[] }>();
  for (const raw of indicators) {
    if (!raw.id) continue;
    byId.set(raw.id, { ...raw, children: [] });
  }

  const roots: IndicatorNode[] = [];
  for (const node of byId.values()) {
    const pid = String((node as IndicatorNode & { parent_id?: string }).parent_id || '').trim();
    const parent = pid ? byId.get(pid) : undefined;
    if (parent && parent.id !== node.id) {
      parent.children.push(node);
    } else {
      roots.push(node);
    }
  }
  return roots;
}

/** 将表格中的层级指标补入图谱：CON 关联设备/项目，HAS 父子关系（与 SSYQ 一致） */
export function enrichGraphWithIndicatorHas(
  graph: SearchGraphData | null,
  tableRows: EnvironmentTableRow[],
): SearchGraphData | null {
  if (!graph) return graph;

  const nodes = [...graph.nodes];
  const edges = [...graph.edges];
  const seenNodeIds = new Set(nodes.map((n) => n.id));
  const seenEdgeIds = new Set(edges.map((e) => e.id));

  const addIndicatorNode = (id: string, name: string, props?: Record<string, unknown>) => {
    if (!id || seenNodeIds.has(id)) return;
    seenNodeIds.add(id);
    nodes.push({
      id,
      label: name || id,
      group: 'Indicator',
      properties: props || {},
    });
  };

  const addEdge = (fromId: string, toId: string, label: string) => {
    const edgeId = `${fromId}__${toId}__${label}`;
    if (!fromId || !toId || seenEdgeIds.has(edgeId)) return;
    seenEdgeIds.add(edgeId);
    edges.push({ id: edgeId, from: fromId, to: toId, label });
  };

  const walkIndicator = (sourceId: string, ind: IndicatorNode, parentId?: string) => {
    if (!ind.id) return;
    addIndicatorNode(ind.id, ind.name, ind as unknown as Record<string, unknown>);
    if (parentId) {
      addEdge(parentId, ind.id, 'HAS');
    } else if (sourceId) {
      addEdge(sourceId, ind.id, 'CON');
    }
    for (const child of ind.children || []) {
      walkIndicator(sourceId, child, ind.id);
    }
  };

  for (const row of tableRows) {
    const sourceId = row.equipmentTypeId || '';
    if (!sourceId) continue;
    const roots = normalizeIndicatorRoots(row.indicators || []);
    for (const root of roots) {
      walkIndicator(sourceId, root);
    }
  }

  return { nodes, edges };
}
