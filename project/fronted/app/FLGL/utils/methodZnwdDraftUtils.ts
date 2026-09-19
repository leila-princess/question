import type { EquipmentTab, EquipmentTabLinkRow } from '@/app/ZSGL/WJGL/components/semanticTypes';
import { markdownToHtml } from '@/utils/exportUtils';
import { DEFAULT_FLGL_STATUS } from './statusDisplay';
import { mergeRelationRowsWithEquipmentTabs } from './methodExtractUtils';

export interface GraphEntityRef {
  id: string;
  name: string;
}

export interface MethodCreateFromZnwdDraft {
  source: 'znwd4';
  question: string;
  routeId: string;
  method: {
    name: string;
    label: string;
    description?: string;
    synrMarkdown: string;
  };
  relations: {
    equipments: GraphEntityRef[];
    projects: GraphEntityRef[];
    indicators: GraphEntityRef[];
  };
}

/** 与 MethodPanel 内 MethodEditorState 结构兼容（新建方法） */
export interface ZnwdMethodEditorSeed {
  key: string;
  editorMode: 'create';
  method: {
    id?: string;
    name: string;
    type: string;
    methodClass: '总则' | '分则';
    status: string;
    label: string;
    description: string;
    sy: string;
    synr: string;
    source_name: string;
    source_id?: string;
  };
  relationRows: Array<{
    key: string;
    targetType: string;
    targetId: string;
    targetLabel?: string;
    relType: string;
    rate?: number | null;
  }>;
  equipmentTabs: EquipmentTab[];
  graphCandidates: [];
  attachmentRows: [];
  nameDuplicate: { duplicate: boolean; existing?: null };
  duplicateChoice: null;
  replaceNodeId?: undefined;
  showRenameInput: boolean;
  renameDraft: string;
}

function newRowKey(prefix: string) {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`;
}

function buildProjectIndicatorRows(
  projects: GraphEntityRef[],
  indicators: GraphEntityRef[],
): EquipmentTabLinkRow[] {
  const rows: EquipmentTabLinkRow[] = [];
  for (const p of projects || []) {
    const id = String(p.id || '').trim();
    const name = String(p.name || id).trim();
    if (!name) continue;
    rows.push({
      key: newRowKey('row-proj'),
      node_type: 'TestProject',
      node_ids: id ? [id] : [],
      nodes: [{ id: id || name, name }],
      rate: null,
    });
  }
  for (const ind of indicators || []) {
    const id = String(ind.id || '').trim();
    const name = String(ind.name || id).trim();
    if (!name) continue;
    rows.push({
      key: newRowKey('row-ind'),
      node_type: 'Indicator',
      node_ids: id ? [id] : [],
      nodes: [{ id: id || name, name }],
      rate: null,
    });
  }
  return rows;
}

export function buildEquipmentTabsFromZnwdRelations(
  equipments: GraphEntityRef[],
  projects: GraphEntityRef[],
  indicators: GraphEntityRef[],
): EquipmentTab[] {
  const linkRows = buildProjectIndicatorRows(projects, indicators);
  const eqs = (equipments || []).filter((e) => e && (e.id || e.name));
  if (!eqs.length) {
    return [
      {
        key: 'eq-unspecified',
        equipment_id: '',
        equipment_name: '未指定设备',
        rows: linkRows,
      },
    ];
  }
  return eqs.map((eq) => ({
    key: `eq-${eq.id || eq.name}`,
    equipment_id: String(eq.id || '').trim(),
    equipment_name: String(eq.name || eq.id || '').trim(),
    rows: linkRows.map((r) => ({ ...r, key: newRowKey(r.key) })),
  }));
}

export function createMethodEditorFromZnwdDraft(draft: MethodCreateFromZnwdDraft): ZnwdMethodEditorSeed[] {
  const rel = draft.relations || { equipments: [], projects: [], indicators: [] };
  const equipmentTabs = buildEquipmentTabsFromZnwdRelations(
    rel.equipments || [],
    rel.projects || [],
    rel.indicators || [],
  );
  const relationRows = mergeRelationRowsWithEquipmentTabs(equipmentTabs, []) as ZnwdMethodEditorSeed['relationRows'];
  const synrHtml = markdownToHtml(String(draft.method?.synrMarkdown || '').trim());
  const methodName = String(draft.method?.name || '').trim();

  return [
    {
      key: `method-znwd-${Date.now()}`,
      editorMode: 'create',
      method: {
        name: methodName,
        type: '分则',
        methodClass: '分则',
        status: DEFAULT_FLGL_STATUS,
        label: String(draft.method?.label || '').trim(),
        description: String(draft.method?.description || '').trim(),
        sy: '',
        synr: synrHtml,
        source_name: '智能问答辅助生成',
        source_id: '',
      },
      relationRows,
      equipmentTabs,
      graphCandidates: [],
      attachmentRows: [],
      nameDuplicate: { duplicate: false, existing: null },
      duplicateChoice: null,
      showRenameInput: false,
      renameDraft: methodName,
    },
  ];
}
