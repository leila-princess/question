/**
 * 将扁平选中的项目/指标列表重建为父子森林（与 backend project_tree_utils 语义一致）。
 */

import {
  isGenericEnvironmentName,
  pickFirstEnvSegment,
  resolveProjectEnvironmentName,
} from '@/lib/resolveProjectEnvironment';

export type ProjectTreeNode = {
  id: string;
  name: string;
  description?: string;
  type?: string;
  children?: ProjectTreeNode[];
  methods?: unknown[];
  link_ids?: string[];
  [key: string]: unknown;
};

function normId(v: unknown): string {
  return String(v ?? '').trim();
}

export function normProjectType(node: Record<string, unknown> | undefined): 'category' | 'test' {
  const t = String(node?.type ?? node?.Type ?? '').trim().toLowerCase();
  if (t === 'category' || t === '父级' || t === 'parent') return 'category';
  if (t === 'test' || t === '子级' || t === 'child') return 'test';
  const children = node?.children;
  if (Array.isArray(children) && children.length > 0) {
    const hasTestChild = children.some((c) => {
      const ct = String((c as Record<string, unknown>)?.type ?? '').toLowerCase();
      return ct === 'test' || ct === '子级' || ct === 'child';
    });
    if (hasTestChild) return 'category';
  }
  return 'test';
}

function indexNodes(nodes: ProjectTreeNode[]): Map<string, ProjectTreeNode> {
  const idx = new Map<string, ProjectTreeNode>();
  const walk = (arr: ProjectTreeNode[]) => {
    for (const n of arr) {
      const id = normId(n.id);
      if (id && !idx.has(id)) idx.set(id, n);
      walk((n.children || []) as ProjectTreeNode[]);
    }
  };
  walk(nodes);
  return idx;
}

function parentMapFromPayloadFields(nodes: ProjectTreeNode[]): Map<string, string> {
  const parentOf = new Map<string, string>();
  const keys = [
    'categoryId',
    'category_id',
    'parent_project_id',
    'parentProjectId',
    'parent_indicator_id',
    'parentIndicatorId',
    'parent_id',
    'parentId',
  ];
  for (const n of nodes) {
    const id = normId(n.id);
    if (!id) continue;
    for (const k of keys) {
      const pid = normId(n[k]);
      if (pid && pid !== id) {
        parentOf.set(id, pid);
        break;
      }
    }
  }
  return parentOf;
}

function indexProjectsFromTableRows(
  tableRows: Array<{ projectsByEnv?: Record<string, ProjectTreeNode[]> }> | undefined,
): Map<string, ProjectTreeNode> {
  const apiById = new Map<string, ProjectTreeNode>();
  if (!tableRows?.length) return apiById;
  for (const row of tableRows) {
    for (const list of Object.values(row.projectsByEnv || {})) {
      for (const p of list || []) {
        const id = normId(p.id);
        if (id) apiById.set(id, p);
        for (const ch of (p.children || []) as ProjectTreeNode[]) {
          const cid = normId(ch.id);
          if (cid) apiById.set(cid, ch);
        }
      }
    }
  }
  return apiById;
}

function indexIndicatorsFromTableRows(
  tableRows: Array<{ indicators?: ProjectTreeNode[] }> | undefined,
): Map<string, ProjectTreeNode> {
  const apiById = new Map<string, ProjectTreeNode>();
  if (!tableRows?.length) return apiById;
  for (const row of tableRows) {
    for (const ind of row.indicators || []) {
      const id = normId(ind.id);
      if (id) apiById.set(id, ind);
      for (const ch of (ind.children || []) as ProjectTreeNode[]) {
        const cid = normId(ch.id);
        if (cid) apiById.set(cid, ch);
      }
    }
  }
  return apiById;
}

function inferIndicatorParentId(
  childId: string,
  tableRows: Array<{ indicators?: ProjectTreeNode[] }> | undefined,
): string {
  const cid = normId(childId);
  if (!cid || !tableRows?.length) return '';
  for (const row of tableRows) {
    for (const cat of row.indicators || []) {
      if (normProjectType(cat as Record<string, unknown>) !== 'category') continue;
      const hasChild = (cat.children || []).some((c) => normId(c.id) === cid);
      if (hasChild) return normId(cat.id);
    }
  }
  return '';
}

function parentMapFromChildren(nodes: ProjectTreeNode[]): Map<string, string> {
  const parentOf = new Map<string, string>();
  const walk = (arr: ProjectTreeNode[], parentId = '') => {
    for (const n of arr) {
      const id = normId(n.id);
      if (id && parentId) parentOf.set(id, parentId);
      walk((n.children || []) as ProjectTreeNode[], id);
    }
  };
  walk(nodes);
  return parentOf;
}

/** 从 ConfigModal tableRows 补全 type/children（API 返回的 category.children） */
export function enrichProjectsFromTableRows(
  flat: ProjectTreeNode[],
  tableRows: Array<{ projectsByEnv?: Record<string, ProjectTreeNode[]> }> | undefined,
): ProjectTreeNode[] {
  const apiById = indexProjectsFromTableRows(tableRows);
  return flat.map((p) => {
    const api = apiById.get(normId(p.id));
    if (!api) return p;
    return {
      ...p,
      type: p.type || api.type,
      children: (p.children?.length ? p.children : api.children) as ProjectTreeNode[] | undefined,
      name: p.name || api.name,
    };
  });
}

export function enrichIndicatorsFromTableRows(
  flat: ProjectTreeNode[],
  tableRows: Array<{ indicators?: ProjectTreeNode[] }> | undefined,
): ProjectTreeNode[] {
  const apiById = indexIndicatorsFromTableRows(tableRows);
  return flat.map((p) => {
    const api = apiById.get(normId(p.id));
    if (!api) return p;
    return {
      ...p,
      type: p.type || api.type,
      children: (p.children?.length ? p.children : api.children) as ProjectTreeNode[] | undefined,
      name: p.name || api.name,
    };
  });
}

export function buildJyllmProjectsPayload(
  selectedProjects: Array<{ projectId: string; categoryId?: string; nodeData?: Record<string, unknown> }>,
  tableRows: Array<{ projectsByEnv?: Record<string, ProjectTreeNode[]> }> | undefined,
  projectEnvironmentById: Record<string, string> = {},
): ProjectTreeNode[] {
  const apiById = indexProjectsFromTableRows(tableRows);
  const flat: ProjectTreeNode[] = [];
  const seen = new Set<string>();

  const pushNode = (node: ProjectTreeNode) => {
    const id = normId(node.id);
    if (!id || seen.has(id)) return;
    seen.add(id);
    flat.push(node);
  };

  for (const entry of selectedProjects) {
    const pid = normId(entry.projectId);
    const base = (entry.nodeData || { id: pid, name: pid }) as ProjectTreeNode;
    const api = apiById.get(pid);
    const parentId =
      entry.categoryId && normId(entry.categoryId) !== pid ? normId(entry.categoryId) : '';
    const childEnv =
      projectEnvironmentById[pid] ||
      pickFirstEnvSegment(String(base.environmentName || base.env_name || ''));
    pushNode({
      ...base,
      id: pid,
      name: String(base.name || api?.name || pid),
      type: base.type || api?.type,
      environmentName: childEnv || base.environmentName,
      categoryId: parentId || undefined,
      parent_project_id: parentId || undefined,
      children: (base.children?.length ? base.children : api?.children) as ProjectTreeNode[] | undefined,
    });
    if (parentId && !seen.has(parentId)) {
      const apiParent = apiById.get(parentId);
      const inheritedEnv =
        projectEnvironmentById[parentId] ||
        childEnv ||
        pickFirstEnvSegment(String(apiParent?.environmentName || apiParent?.env_name || ''));
      pushNode({
        id: parentId,
        name: String(apiParent?.name || parentId),
        type: 'category',
        environmentName: inheritedEnv || undefined,
        children: apiParent?.children as ProjectTreeNode[] | undefined,
      });
    }
  }

  return buildProjectForest(enrichProjectsFromTableRows(flat, tableRows));
}

export function buildJyllmIndicatorsPayload(
  selectedIndicators: Array<{ indicatorId: string; categoryId?: string; nodeData?: Record<string, unknown> }>,
  tableRows: Array<{ indicators?: ProjectTreeNode[] }> | undefined,
): ProjectTreeNode[] {
  const apiById = indexIndicatorsFromTableRows(tableRows);
  const expandedEntries: Array<{
    indicatorId: string;
    categoryId?: string;
    nodeData?: Record<string, unknown>;
  }> = [];
  const seenEntryIds = new Set<string>();

  const pushEntry = (entry: {
    indicatorId: string;
    categoryId?: string;
    nodeData?: Record<string, unknown>;
  }) => {
    const id = normId(entry.indicatorId);
    if (!id || seenEntryIds.has(id)) return;
    seenEntryIds.add(id);
    expandedEntries.push(entry);
  };

  for (const entry of selectedIndicators) {
    pushEntry(entry);
    const iid = normId(entry.indicatorId);
    const api = apiById.get(iid) || (entry.nodeData as ProjectTreeNode | undefined);
    if (normProjectType(api as Record<string, unknown>) === 'category') {
      for (const ch of ((api?.children || []) as ProjectTreeNode[])) {
        const cid = normId(ch.id);
        if (!cid) continue;
        pushEntry({
          indicatorId: cid,
          categoryId: iid,
          nodeData: ch,
        });
      }
    }
  }

  const flat: ProjectTreeNode[] = [];
  const seen = new Set<string>();

  const pushNode = (node: ProjectTreeNode) => {
    const id = normId(node.id);
    if (!id || seen.has(id)) return;
    seen.add(id);
    flat.push(node);
  };

  for (const entry of expandedEntries) {
    const iid = normId(entry.indicatorId);
    const base = (entry.nodeData || { id: iid, name: iid }) as ProjectTreeNode;
    const api = apiById.get(iid);
    const parentId =
      (entry.categoryId && normId(entry.categoryId) !== iid ? normId(entry.categoryId) : '') ||
      inferIndicatorParentId(iid, tableRows);
    const nodeType = String(base.type || api?.type || '').trim().toLowerCase();
    const isCategory = nodeType === 'category' || normProjectType({ ...base, type: nodeType }) === 'category';
    pushNode({
      ...base,
      id: iid,
      name: String(base.name || api?.name || iid),
      type: isCategory ? 'category' : (base.type || api?.type || 'test'),
      categoryId: parentId || undefined,
      parent_indicator_id: parentId || undefined,
      children: (base.children?.length ? base.children : api?.children) as ProjectTreeNode[] | undefined,
    });
    if (parentId && !seen.has(parentId)) {
      const apiParent = apiById.get(parentId);
      pushNode({
        id: parentId,
        name: String(apiParent?.name || parentId),
        type: 'category',
        children: apiParent?.children as ProjectTreeNode[] | undefined,
      });
    }
  }

  return buildIndicatorForest(enrichIndicatorsFromTableRows(flat, tableRows));
}

export function buildProjectForest(flatNodes: ProjectTreeNode[]): ProjectTreeNode[] {
  const raw = (flatNodes || []).filter((n) => normId(n.id));
  if (!raw.length) return [];

  const indexed = indexNodes(raw);
  const parentOf = parentMapFromChildren(raw);
  for (const [cid, pid] of parentMapFromPayloadFields(raw)) {
    if (cid && pid && cid !== pid) parentOf.set(cid, pid);
  }
  const selected = new Set(raw.map((n) => normId(n.id)));

  for (const cid of [...selected]) {
    const pid = parentOf.get(cid);
    if (pid && !selected.has(pid)) {
      if (!indexed.has(pid)) {
        indexed.set(pid, { id: pid, name: pid, type: 'category' });
      }
      selected.add(pid);
    }
  }

  const childrenMap = new Map<string, string[]>();
  for (const cid of selected) {
    const pid = parentOf.get(cid);
    if (pid && selected.has(pid)) {
      const arr = childrenMap.get(pid) || [];
      if (!arr.includes(cid)) arr.push(cid);
      childrenMap.set(pid, arr);
    }
  }

  const roots: string[] = [];
  for (const nid of selected) {
    const pid = parentOf.get(nid);
    if (!pid || !selected.has(pid)) roots.push(nid);
  }

  const order = new Map(raw.map((n, i) => [normId(n.id), i]));
  roots.sort((a, b) => (order.get(a) ?? 9999) - (order.get(b) ?? 9999));

  const build = (nid: string): ProjectTreeNode => {
    const src = indexed.get(nid) || { id: nid, name: nid };
    const childIds = (childrenMap.get(nid) || []).sort(
      (a, b) => (order.get(a) ?? 9999) - (order.get(b) ?? 9999),
    );
    const { children: _c, ...rest } = src;
    return {
      ...rest,
      id: nid,
      type: normProjectType(src as Record<string, unknown>),
      children: childIds.map(build),
    };
  };

  return roots.map(build);
}

export function buildIndicatorForest(flatNodes: ProjectTreeNode[]): ProjectTreeNode[] {
  return buildProjectForest(flatNodes);
}

/** 解析单个项目节点所属环境名（配置表映射 > payload > 图库 _env） */
export function resolveNodeEnvironment(
  node: ProjectTreeNode,
  projectEnvironmentById: Record<string, string>,
  tableRows?: Array<{ projectsByEnv?: Record<string, ProjectTreeNode[]> }>,
  parentCategory?: ProjectTreeNode,
): string {
  const id = normId(node.id);
  const fromMap = projectEnvironmentById[id];
  if (fromMap && !isGenericEnvironmentName(fromMap)) return fromMap;

  const fromPayload = pickFirstEnvSegment(
    String(node.environmentName || node.env_name || node.environment_name || ''),
  );
  if (fromPayload && !isGenericEnvironmentName(fromPayload)) return fromPayload;

  const apiNode = indexProjectsFromTableRows(tableRows).get(id);
  const resolved = resolveProjectEnvironmentName(
    (apiNode || node) as Record<string, unknown>,
    parentCategory as Record<string, unknown> | undefined,
  );
  if (resolved && !isGenericEnvironmentName(resolved)) return resolved;

  return '';
}

/**
 * 按环境拆分父子森林：每个环境 bucket 含「父 category + 该环境下的子 test」。
 * 与配置表矩阵、JYGZ render 语义一致。
 */
export function splitProjectForestByEnvironment(
  forest: ProjectTreeNode[],
  envOrder: string[],
  projectEnvironmentById: Record<string, string>,
  tableRows?: Array<{ projectsByEnv?: Record<string, ProjectTreeNode[]> }>,
): Map<string, ProjectTreeNode[]> {
  const result = new Map<string, ProjectTreeNode[]>();
  for (const envName of envOrder) {
    if (envName && !isGenericEnvironmentName(envName)) {
      result.set(envName, []);
    }
  }

  const pushToEnv = (envName: string, node: ProjectTreeNode) => {
    if (!envName || isGenericEnvironmentName(envName)) return;
    if (!result.has(envName)) result.set(envName, []);
    result.get(envName)!.push(node);
  };

  for (const root of forest) {
    const rootType = normProjectType(root as Record<string, unknown>);
    const children = (root.children || []) as ProjectTreeNode[];

    if (rootType === 'category' && children.length > 0) {
      for (const envName of envOrder) {
        if (!envName || isGenericEnvironmentName(envName)) continue;
        const childrenInEnv = children.filter(
          (ch) =>
            resolveNodeEnvironment(ch, projectEnvironmentById, tableRows, root) === envName,
        );
        if (childrenInEnv.length > 0) {
          const { children: _c, ...rest } = root;
          pushToEnv(envName, {
            ...rest,
            children: childrenInEnv,
          });
        }
      }
      continue;
    }

    const envName = resolveNodeEnvironment(root, projectEnvironmentById, tableRows);
    if (envName) {
      pushToEnv(envName, { ...root });
    }
  }

  return result;
}
