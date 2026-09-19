import type { Key } from 'react';
import type { DataNode } from 'antd/es/tree';
import type { EquipmentTreeNode, GraphNode } from '../api';

export type EquipmentTreePath = {
  level1Key: string;
  eqtypeKey: string;
  equipmentKey: string;
  equipmentId: string;
  equipmentName: string;
};

/** 从树节点 title 提取可检索文本（去掉末尾「（N）」计数） */
export function treeNodeSearchText(title: DataNode['title']): string {
  const raw = typeof title === 'string' ? title : '';
  return raw.replace(/（\d+）\s*$/, '').trim().toLowerCase();
}

/** 按关键词过滤设备树：仅保留命中叶子或含命中后代的祖先路径 */
export function filterEquipmentTreeByKeyword(nodes: DataNode[], keyword: string): DataNode[] {
  const kw = keyword.trim().toLowerCase();
  if (!kw) return nodes;

  const walk = (list: DataNode[]): DataNode[] =>
    list.flatMap((node) => {
      const children = (node.children || []) as DataNode[];
      const filteredChildren = children.length ? walk(children) : [];
      const isLeaf = node.isLeaf === true || children.length === 0;
      const selfMatch = treeNodeSearchText(node.title).includes(kw);

      if (filteredChildren.length > 0) {
        return [{ ...node, children: filteredChildren }];
      }
      if (isLeaf && selfMatch) {
        return [{ ...node, children: [] }];
      }
      return [];
    });

  return walk(nodes);
}

/** 检索模式下：非叶子不可选，避免误选二级导致全选 */
export function withSearchSelectability(nodes: DataNode[], keyword: string): DataNode[] {
  const kw = keyword.trim();
  if (!kw) return nodes;

  const walk = (list: DataNode[]): DataNode[] =>
    list.map((node) => {
      const children = (node.children || []) as DataNode[];
      const isLeaf = node.isLeaf === true || children.length === 0;
      return {
        ...node,
        selectable: isLeaf,
        children: children.length ? walk(children) : children,
      };
    });

  return walk(nodes);
}

export function collectTreeKeys(nodes: DataNode[]): Key[] {
  return nodes.flatMap((node) => [node.key, ...collectTreeKeys((node.children || []) as DataNode[])]);
}

export function buildFlglSidebarTreeData(tree: EquipmentTreeNode[]): DataNode[] {
  return tree.map((level1) => {
    const level2Nodes = (level1.children || []) as EquipmentTreeNode[];
    const level2Children = level2Nodes.map((level2) => {
      const level3Nodes = (level2.children || []) as GraphNode[];
      return {
        title: `${level2.name || level2.id}（${level3Nodes.length}）`,
        key: `eqtype_${level2.id}`,
        selectable: true,
        children: level3Nodes.map((eq) => ({
          title: eq.name || eq.id,
          key: `equipment_${eq.id}`,
          selectable: true,
          isLeaf: true,
        })),
      } as DataNode;
    });
    return {
      title: `${level1.name}（${level2Nodes.length}）`,
      key: `level1_${level1.name}`,
      selectable: true,
      children: level2Children,
    } as DataNode;
  });
}

/** graph-picker 三级树 → Ant Tree（叶子 key 为 equipment id） */
export function buildGraphPickerTreeData(
  raw: Array<{
    id: string;
    name: string;
    children: Array<{
      id: string;
      name: string;
      children: Array<{ id: string; name: string }>;
    }>;
  }>,
  excludeEquipmentIds: Set<string> = new Set(),
): DataNode[] {
  return raw
    .map((l1) => ({
      key: l1.id,
      title: l1.name,
      selectable: false,
      children: (l1.children || [])
        .map((l2) => ({
          key: l2.id,
          title: l2.name,
          selectable: false,
          children: (l2.children || [])
            .filter((eq) => !excludeEquipmentIds.has(eq.id))
            .map((eq) => ({
              key: eq.id,
              title: eq.name || eq.id,
              isLeaf: true,
              selectable: true,
            })),
        }))
        .filter((l2) => (l2.children as DataNode[])?.length),
    }))
    .filter((n) => (n.children as DataNode[])?.length);
}

export function findEquipmentTreePath(
  tree: EquipmentTreeNode[],
  equipmentId: string,
): EquipmentTreePath | null {
  const target = String(equipmentId || '').trim();
  if (!target) return null;

  for (const level1 of tree) {
    for (const level2 of (level1.children || []) as EquipmentTreeNode[]) {
      for (const eq of (level2.children || []) as GraphNode[]) {
        if (String(eq.id || '').trim() === target) {
          return {
            level1Key: `level1_${level1.name}`,
            eqtypeKey: `eqtype_${level2.id}`,
            equipmentKey: `equipment_${eq.id}`,
            equipmentId: String(eq.id),
            equipmentName: String(eq.name || eq.id),
          };
        }
      }
    }
  }
  return null;
}

export function selectedEquipmentIdFromCategory(key: string | null): string | null {
  if (!key?.startsWith('equipment_')) return null;
  return key.slice('equipment_'.length) || null;
}

export function selectedEquipmentTypeIdFromCategory(key: string | null): string | null {
  if (!key?.startsWith('eqtype_')) return null;
  return key.slice('eqtype_'.length) || null;
}

export function selectedLevel1FromCategory(key: string | null): string | null {
  if (!key?.startsWith('level1_')) return null;
  return key.slice('level1_'.length) || null;
}

export function equipmentIdsFromTreeSelection(key: string | null, tree: EquipmentTreeNode[]): string[] {
  if (!key) return [];
  if (key.startsWith('equipment_')) {
    const id = key.slice('equipment_'.length).trim();
    return id ? [id] : [];
  }
  if (key.startsWith('eqtype_')) {
    const typeId = key.slice('eqtype_'.length).trim();
    for (const level1 of tree) {
      for (const level2 of (level1.children || []) as EquipmentTreeNode[]) {
        if (String(level2.id).trim() === typeId) {
          return ((level2.children || []) as GraphNode[]).map((eq) => String(eq.id || '').trim()).filter(Boolean);
        }
      }
    }
    return [];
  }
  if (key.startsWith('level1_')) {
    const level1Name = key.slice('level1_'.length).trim();
    const level1 = tree.find((item) => String(item.name || '').trim() === level1Name);
    return ((level1?.children || []) as EquipmentTreeNode[]).flatMap((level2) =>
      ((level2.children || []) as GraphNode[]).map((eq) => String(eq.id || '').trim()).filter(Boolean),
    );
  }
  return [];
}

export function treeNodeTitleText(title: DataNode['title']): string {
  if (typeof title === 'string') return title;
  return '';
}
