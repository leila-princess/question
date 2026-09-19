'use client';
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Upload, Button, message, Drawer, Space, Typography, Spin, Modal, Form, Input, Tabs, Select, Table, Row, Col, Card, Tag, Popconfirm } from 'antd';
import { UploadOutlined, PlusOutlined, DeleteOutlined, EditOutlined, LinkOutlined, SearchOutlined } from '@ant-design/icons';
import { batchDeleteNodes, useBatchRowSelection } from '../hooks/useBatchRowSelection';
import RelationModal from './RelationModal';
import MethodExtractWizard, { type MethodExtractWizardResult } from './MethodExtractWizard';
import DeviceLinkTabs from '@/app/ZSGL/WJGL/components/DeviceLinkTabs';
import type { EquipmentTab, GraphRecommendation } from '@/app/ZSGL/WJGL/components/semanticTypes';
import MethodSynrEditor from './MethodSynrEditor';
import MathAwareTextArea from '@/components/MathAwareTextArea';
import MixedContent from '@/components/ThreeColumnLayout/MixedContent';
import { normalizeLatexSegment } from '@/lib/latexUtils';
import { SYNR_PREVIEW_STYLES, synrMarkdownToHtml, slateOrJsonContentToHtml, isBrOnlyPseudoHtml, normalizeSynrForWangEditor } from '../utils/synrHtmlUtils';
import {
  importMethod,
  fetchNodes,
  fetchMethodsByRelation,
  fetchNodeRelations,
  uploadMethodAttachment,
  checkMethodNameDuplicate,
  fetchMethodDraftId,
  uploadMethodImage,
  recommendMethodRelations,
  fetchMethodEquipmentTabs,
  deleteNode,
  type GraphNode,
  type NodeRelation,
  type NodeType,
  type EquipmentTreeNode,
} from '../api';
import {
  buildInitialEquipmentTabs,
  buildEquipmentTabsFromMethodRelations,
  mergeSavedMethodEquipmentTabs,
  findEquipmentNameInTree,
  graphCandidatesFromMethodRelations,
  labelFromSourceFilename,
  mergeRelationRowsWithEquipmentTabs,
  rowsFromAllMethodGraphRelations,
  normalizeFileNodeId,
  serializeSourceSections,
  firstSourceSectionNo,
  buildMethodSourceReadUrl,
} from '../utils/methodExtractUtils';
import {
  createMethodEditorFromZnwdDraft,
  type MethodCreateFromZnwdDraft,
} from '../utils/methodZnwdDraftUtils';
import { formatTableDateDisplay } from '../utils/tableDateDisplay';
import EquipmentNameLinks from './EquipmentNameLinks';
import { useTablePagination } from '@/utils/useTablePagination';
import {
  DEFAULT_FLGL_STATUS,
  FLGL_STATUS_OPTIONS,
  normalizeStatusValue,
  StatusTag,
} from '../utils/statusDisplay';

const { Title, Paragraph, Text } = Typography;
function getCurrentUserName(): string {
  if (typeof window === 'undefined') return '管理员';
  const userStr = localStorage.getItem('userInfo');
  if (userStr) {
    try {
      const u = JSON.parse(userStr) as { userName?: string; name?: string };
      return u.userName || u.name || '管理员';
    } catch {
      return '管理员';
    }
  }
  return localStorage.getItem('userName') || '管理员';
}

function getCurrentTimeString(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}:${String(d.getSeconds()).padStart(2, '0')}`;
}

const RELATION_TARGET_TYPES: { value: NodeType; label: string }[] = [
  { value: 'Equipment', label: '装备' },
  { value: 'TestProject', label: '项目' },
  { value: 'Indicator', label: '指标' },
  { value: 'TestMethod', label: '方法' },
  { value: 'File', label: '文件' },
];

const REL_TYPE_LABELS: Record<string, string> = {
  CON: '关联',
  HAS: '包含',
  BELONGS_TO: '属于',
};

const METHOD_REL_TYPES = [
  { value: 'CON' as const, label: '关联（CON）' },
  { value: 'HAS' as const, label: '包含（HAS）' },
  { value: 'BELONGS_TO' as const, label: '属于（BELONGS_TO）' },
];

const METHOD_STATUS_OPTIONS = FLGL_STATUS_OPTIONS;

const REL_TYPE_SET = new Set(['CON', 'HAS', 'BELONGS_TO'] as const);
const DEFAULT_CON_RATE = 0.5;

type RelationTargetType = (typeof RELATION_TARGET_TYPES)[number]['value'];
type RelTypeKind = (typeof METHOD_REL_TYPES)[number]['value'];

function newRelRowKey() {
  return `rel-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
}

function defaultMethodRelTypeForTarget(targetType: RelationTargetType): RelTypeKind {
  if (targetType === 'File' || targetType === 'TestMethod') return 'BELONGS_TO';
  if (targetType === 'TestProject' || targetType === 'Indicator') return 'CON';
  return 'CON';
}

/** 与 backend method_block_markdown 对齐：结构化块 → Markdown，避免整段 JSON 展示 */
function stripHtmlToPlain(html: string): string {
  if (!html || !String(html).trim()) return '';
  let s = String(html);
  s = s.replace(/<\s*br\s*\/?>/gi, '\n');
  s = s.replace(/<\/(tr|h[1-6]|p|div|li|th|td)>/gi, ' | ');
  s = s.replace(/<[^>]+>/g, ' ');
  s = s.replace(/[ \t]+/g, ' ');
  s = s.replace(/\s*\|\s*/g, ' | ');
  s = s.replace(/\n\s*\n+/g, '\n\n');
  return s.trim();
}

function textBlockToMarkdown(block: unknown): string {
  if (block == null) return '';
  if (typeof block === 'string') return block.trim();
  if (typeof block !== 'object' || Array.isArray(block)) return '';
  const o = block as { text?: string; tables_html?: string[]; formulas_latex?: string[] };
  const text = String(o.text ?? '').trim();
  const tables = Array.isArray(o.tables_html) ? o.tables_html : [];
  const formulas = Array.isArray(o.formulas_latex) ? o.formulas_latex : [];
  const parts: string[] = [];
  if (text) {
    if (/<\s*(p|div|table|ul|ol|li|img|h[1-6]|br|strong|figure)\b/i.test(text)) {
      parts.push(text);
    } else {
      parts.push(text);
    }
  }
  formulas.forEach((raw, i) => {
    const t = normalizeLatexSegment(String(raw ?? '').trim());
    if (t) {
      parts.push(`**公式 ${i + 1}**\n\n$$${t}$$`);
    }
  });
  tables.forEach((raw, i) => {
    const h = String(raw ?? '').trim();
    if (!h) return;
    if (/<table/i.test(h)) {
      parts.push(h);
    } else {
      const plain = stripHtmlToPlain(h);
      if (plain) {
        parts.push(`**表格 ${i + 1}**（自 HTML 提取为文本）\n\n${plain}`);
      } else {
        const cut = h.length > 12000 ? `${h.slice(0, 12000)}…` : h;
        parts.push(`**表格 ${i + 1}**（无可用文本，原 HTML 见下）\n\n\`\`\`html\n${cut}\n\`\`\``);
      }
    }
  });
  return parts.join('\n\n').trim();
}

interface MethodRelationRow {
  key: string;
  targetType: RelationTargetType;
  targetId: string;
  /** 推荐行展示用名称（图谱选项加载前也可显示） */
  targetLabel?: string;
  relType: RelTypeKind;
  rate?: number | null;
}

/** 从 GET /nodes/TestMethod/:id/relations 结果中挑出「项目/指标 → 方法」的 CON 边，用于编辑弹窗内图谱关联表 */
function rowsFromMethodGraphRelations(relations: NodeRelation[]): MethodRelationRow[] {
  const allow = new Set<string>(['TestProject', 'Indicator']);
  const out: MethodRelationRow[] = [];
  let i = 0;
  for (const r of relations) {
    if ((r.rel_type || '').toUpperCase() !== 'CON') continue;
    if (r.direction !== 'incoming') continue;
    const labels = r.target_labels || [];
    const tt = labels.find((l) => allow.has(l)) as RelationTargetType | undefined;
    if (!tt) continue;
    const rawRate = r.rate;
    const n = rawRate != null && rawRate !== ('' as unknown) ? Number(rawRate) : null;
    out.push({
      key: newRelRowKey() + i++,
      targetType: tt,
      targetId: String(r.target_id || ''),
      relType: 'CON',
      rate: n != null && !Number.isNaN(n) ? n : null,
    });
  }
  return out;
}

function rowsFromRecommended(
  recs?: Array<{
    target_type?: string;
    target_id?: string;
    rel_type?: string;
    score?: number;
    rate?: number;
    node_name?: string;
  }>,
): MethodRelationRow[] {
  if (!recs?.length) return [];
  const allow = new Set<string>(['Equipment', 'TestProject', 'Indicator', 'TestMethod', 'File']);
  const sliced = recs.slice(0, 15);
  return sliced.map((r, i) => {
    const tt = (r.target_type && allow.has(r.target_type) ? r.target_type : 'TestProject') as RelationTargetType;
    const rt = (r.rel_type && REL_TYPE_SET.has(r.rel_type as RelTypeKind) ? r.rel_type : 'CON') as RelTypeKind;
    const rawRate = r.rate ?? r.score;
    const n = rawRate != null && rawRate !== ('' as unknown) ? Number(rawRate) : null;
    const tid = String(r.target_id || '').trim();
    const nm = String(r.node_name || '').trim();
    return {
      key: newRelRowKey() + i,
      targetType: tt,
      targetId: tid,
      targetLabel: nm || tid,
      relType: rt,
      rate: n != null && !Number.isNaN(n) ? n : null,
    };
  });
}

function rowsFromExtractRecommendations(method: {
  recommended_relations?: MethodResult['recommended_relations'];
  equipment_recommendations?: Array<{
    equipment_id: string;
    equipment_name: string;
    score?: number;
    projects?: Array<{ node_id: string; node_name?: string; score?: number }>;
    indicators?: Array<{ node_id: string; node_name?: string; score?: number }>;
  }>;
}): MethodRelationRow[] {
  const eqRecs = method.equipment_recommendations;
  if (eqRecs?.length) {
    const rows: MethodRelationRow[] = [];
    for (const eq of eqRecs) {
      rows.push({
        key: newRelRowKey(),
        targetType: 'Equipment',
        targetId: eq.equipment_id,
        targetLabel: eq.equipment_name || eq.equipment_id,
        relType: 'CON',
        rate: eq.score != null && !Number.isNaN(Number(eq.score)) ? Number(eq.score) : null,
      });
      for (const p of eq.projects || []) {
        rows.push({
          key: newRelRowKey(),
          targetType: 'TestProject',
          targetId: p.node_id,
          targetLabel: p.node_name || p.node_id,
          relType: 'CON',
          rate: p.score != null && !Number.isNaN(Number(p.score)) ? Number(p.score) : null,
        });
      }
      for (const ind of eq.indicators || []) {
        rows.push({
          key: newRelRowKey(),
          targetType: 'Indicator',
          targetId: ind.node_id,
          targetLabel: ind.node_name || ind.node_id,
          relType: 'CON',
          rate: ind.score != null && !Number.isNaN(Number(ind.score)) ? Number(ind.score) : null,
        });
      }
    }
    return rows;
  }
  return rowsFromRecommended(method.recommended_relations);
}

function buildRelationsPayload(rows: MethodRelationRow[]) {
  const out: { target_type: string; target_id: string; target_name?: string; rel_type: string; rate?: number }[] = [];
  for (const row of rows) {
    const tid = (row.targetId || '').trim();
    const tname = (row.targetLabel || '').trim();
    if (!tid && !tname) continue;
    if (!REL_TYPE_SET.has(row.relType)) continue;
    if (row.rate != null && (typeof row.rate === 'number' ? Number.isNaN(row.rate) : Number.isNaN(Number(row.rate)))) {
      continue;
    }
    const o: { target_type: string; target_id: string; target_name?: string; rel_type: string; rate?: number } = {
      target_type: row.targetType,
      target_id: tid,
      rel_type: row.relType,
    };
    if (!tid && tname) o.target_name = tname;
    if (row.rate != null && row.rate !== undefined) {
      const s = typeof row.rate === 'number' ? row.rate : Number(row.rate);
      if (Number.isNaN(s)) continue;
      o.rate = Number(Math.max(0, Math.min(1, s)).toFixed(2));
    } else if (row.relType === 'CON') {
      // 保存前兜底：关联关系缺失关联度时自动补默认值
      o.rate = DEFAULT_CON_RATE;
    }
    out.push(o);
  }
  return out;
}

interface RecommendedNode {
  node_type: string;
  node_id: string;
  node_name: string;
  score: number;
}

interface MethodResult {
  id?: string;
  name: string;
  type: string;
  /** 方法类别：总则 / 分则，与数据库属性 Class 对应；提取结果可能从 type 回退 */
  class?: string;
  label: string;
  description: string;
  sy: string;
  synr: string;
  sytj?: string;
  sygc?: string;
  jgpd?: string;
  source_name: string;
  source_id?: string;
  source_section_ids?: string[] | string;
  source_section_nos?: string[] | string;
  source_category?: string;
  extract_source?: string;
  recommended_nodes?: RecommendedNode[];
  /** 默认推荐：装备 → 项目/指标；表格中可增选六类 */
  recommended_relations?: Array<{
    target_type: string;
    target_id: string;
    node_name?: string;
    rel_type: string;
    score?: number;
    rate?: number;
    equipment_id?: string;
  }>;
  equipment_recommendations?: Array<{
    equipment_id: string;
    equipment_name: string;
    score?: number;
    projects?: Array<{ node_id: string; node_name?: string; score?: number }>;
    indicators?: Array<{ node_id: string; node_name?: string; score?: number }>;
  }>;
  equipment_candidates?: Array<{
    node_id: string;
    node_name: string;
    score?: number;
    llm_reason?: string;
  }>;
  method_index?: number;
  job_id?: string;
  document_dir?: string;
  graph_matches_ref?: boolean;
  name_duplicate?: { duplicate: boolean; existing?: GraphNode | null };
  content_slots?: { filled?: string[]; missing?: string[]; complete?: boolean };
}

interface ExtractMethodResponse {
  success?: boolean;
  data?: {
    methods?: MethodResult[];
    methods_count?: number;
    recommended_nodes?: RecommendedNode[];
    cache_hit?: boolean;
    extract_source?: string;
  } & Partial<MethodResult>;
  error?: string;
}

type MethodClassKind = '总则' | '分则';

function inferMethodClass(m: { type?: string; class?: string }): MethodClassKind {
  const c = (m.class || '').trim();
  if (c === '总则' || c === '分则') return c;
  const t = (m.type || '').trim();
  if (t === '总则' || t === '分则') return t;
  return '分则';
}

interface EditableMethod {
  id?: string;
  name: string;
  /** 图属性 Type，来自提取，表单不单独编辑 */
  type: string;
  methodClass: MethodClassKind;
  status: string;
  label: string;
  description: string;
  sy: string;
  synr: string;
  source_name: string;
  source_id?: string;
  source_section_ids?: string[] | string;
  source_section_nos?: string[] | string;
  source_category?: string;
  extract_source?: string;
}

type MethodEditorMode = 'edit' | 'create';

interface MethodEditorState {
  key: string;
  /** edit：表格编辑；create：新增或文件提取 */
  editorMode: MethodEditorMode;
  method: EditableMethod;
  savedNode?: GraphNode;
  relationRows: MethodRelationRow[];
  /** 多设备 Tab 关联（与文件管理上传一致） */
  equipmentTabs: EquipmentTab[];
  graphCandidates: GraphRecommendation[];
  nameDuplicate: { duplicate: boolean; existing?: GraphNode | null };
  attachmentRows: MethodRelationRow[];
  /** 提取流程遗留：覆盖库中节点；新增/提取重名弹窗不再依赖此项 */
  duplicateChoice: 'replace' | 'skip' | null;
  replaceNodeId?: string;
  showRenameInput: boolean;
  renameDraft: string;
  /** 流式提取：装备候选（展示/兜底） */
  equipmentCandidates?: MethodResult['equipment_candidates'];
  streamJobId?: string;
  streamMethodIndex?: number;
  documentDir?: string;
  /** LLM 推荐关联节点进行中 */
  recommending?: boolean;
}

function graphNodeSynr(node: GraphNode | null | undefined): string {
  if (!node) return '';
  return String(node.synr ?? node.SYNR ?? node.method_content ?? '').trim();
}

function normalizeContent(content: unknown): string {
  if (content == null) return '';
  if (typeof content === 'string') return content;
  if (typeof content === 'object' && !Array.isArray(content) && content !== null && 'text' in content) {
    const text = String((content as { text?: string }).text ?? '').trim();
    if (/<\s*(p|div|table|ul|ol|li|img|h[1-6]|br|strong|figure)\b/i.test(text)) {
      return text;
    }
    return textBlockToMarkdown(content);
  }
  if (Array.isArray(content)) {
    return content.map((item) => (typeof item === 'string' ? item : JSON.stringify(item, null, 2))).join('\n');
  }
  if (typeof content === 'object') {
    try {
      return JSON.stringify(content, null, 2);
    } catch {
      return String(content);
    }
  }
  return String(content);
}

function isRichHtmlContent(html: string): boolean {
  if (isBrOnlyPseudoHtml(html)) return false;
  return /<(?:p|div|table|ul|ol|li|img|h[1-6]|strong|figure|blockquote)\b/i.test(html)
    || /class=["'][^"']*doc-table/i.test(html)
    || /class=["'][^"']*wjgl-section-heading/i.test(html);
}

function normalizeSynrField(raw: unknown): string {
  if (raw == null) return '';
  const fromJson = slateOrJsonContentToHtml(raw);
  if (fromJson != null) return fromJson;
  return String(raw).trim();
}

function pickMethodSynrRaw(method: { synr?: unknown; SYNR?: unknown }): string {
  const raw = method.synr ?? (method as { SYNR?: unknown }).SYNR;
  return normalizeSynrField(raw);
}

function composeLegacyMethodContent(method: {
  synr?: unknown;
  SYNR?: unknown;
  sytj?: unknown;
  sygc?: unknown;
  jgpd?: unknown;
}): string {
  const synr = pickMethodSynrRaw(method);
  if (synr) {
    return normalizeSynrForWangEditor(synr);
  }
  const chunks: string[] = [];
  const sections: Array<[string, unknown]> = [
    ['1. 试验条件', method.sytj],
    ['2. 试验过程', method.sygc],
    ['3. 结果评定', method.jgpd],
  ];
  sections.forEach(([title, raw]) => {
    const content = normalizeContent(raw);
    if (content) {
      if (isRichHtmlContent(content)) {
        chunks.push(`<h4>${title}</h4>${content}`);
      } else {
        chunks.push(`<h4>${title}</h4>${plainTextToHtml(content)}`);
      }
    }
  });
  if (chunks.length) return normalizeSynrForWangEditor(chunks.join(''));
  return normalizeSynrForWangEditor(synrMarkdownToHtml(synr));
}

function plainTextToHtml(text: string): string {
  const s = String(text || '').trim();
  if (!s) return '';
  if (isBrOnlyPseudoHtml(s)) return synrMarkdownToHtml(s);
  if (/<\s*(p|div|table|ul|ol|li|img|h[1-6]|strong)\b/i.test(s)) return s;
  return s.split(/\n\s*\n+/).filter(Boolean).map((p) => `<p>${p.replace(/\n/g, '<br>')}</p>`).join('');
}

async function ensureMethodId(editor: MethodEditorState): Promise<string> {
  const existing = String(editor.savedNode?.id || editor.method.id || editor.replaceNodeId || '').trim();
  if (existing) return existing;
  return fetchMethodDraftId();
}

function displayMethodSavedUser(editor: MethodEditorState): string {
  const n = editor.savedNode as GraphNode | undefined;
  if (!n) return '—';
  if (n.user_name != null && String(n.user_name).trim() !== '') return String(n.user_name);
  if (n.User_name != null && String(n.User_name).trim() !== '') return String(n.User_name);
  return '—';
}

async function persistMethodEditor(
  editor: MethodEditorState,
): Promise<{ ok: boolean; savedNode?: GraphNode; methodId?: string; error?: string }> {
  if (editor.duplicateChoice === 'skip') {
    return { ok: false, error: '已跳过' };
  }
  if (!editor.method.name?.trim()) {
    return { ok: false, error: '方法名称不能为空' };
  }

  const methodId = editor.replaceNodeId || (await ensureMethodId(editor));
  const nodeData = {
    Id: methodId || undefined,
    Name: editor.method.name,
    Type: editor.method.type || '分则',
    Class: editor.method.methodClass,
    Label: editor.method.label,
    Description: editor.method.description,
    Sy: editor.method.sy,
    SYNR: editor.method.synr,
    Source_name: editor.method.source_name,
    Source_id: normalizeFileNodeId(editor.method.source_id || ''),
    Source_section_ids: serializeSourceSections(editor.method.source_section_ids),
    Source_section_nos: serializeSourceSections(editor.method.source_section_nos),
    Source_category: editor.method.source_category || '',
    Extract_source: editor.method.extract_source || '',
    Status: normalizeStatusValue(editor.method.status),
    User_name: getCurrentUserName(),
    Create_time: getCurrentTimeString(),
  };
  const relations = buildRelationsPayload(
    mergeRelationRowsWithEquipmentTabs(
      editor.equipmentTabs || [],
      editor.relationRows.filter((r) => r.targetType === 'File' || r.targetType === 'TestMethod'),
    ) as MethodRelationRow[],
  );
  const relationDetails: Array<Record<string, unknown>> = [];
  for (const tab of editor.equipmentTabs || []) {
    const eqId = String(tab.equipment_id || '').trim();
    if (eqId && eqId !== '__unassigned__') {
      relationDetails.push({
        target_type: 'Equipment',
        target_id: eqId,
        node_name: tab.equipment_name || eqId,
        rel_type: 'CON',
        equipment_id: eqId,
        rate: null,
      });
    }
    for (const row of tab.rows || []) {
      const nodeType = String(row.node_type || '').trim();
      if (!nodeType || nodeType === 'Equipment') continue;
      const ids = row.node_ids?.length ? row.node_ids : [];
      const nameMap = Object.fromEntries((row.nodes || []).map((n) => [n.id, n.name]));
      for (const nid of ids) {
        if (!nid) continue;
        relationDetails.push({
          target_type: nodeType,
          target_id: nid,
          node_name: nameMap[nid] || nid,
          rel_type: nodeType === 'File' || nodeType === 'TestMethod' ? 'BELONGS_TO' : 'CON',
          equipment_id: eqId && eqId !== '__unassigned__' ? eqId : '',
          rate:
            row.rate != null
              ? Number(Math.max(0, Math.min(1, Number(row.rate))).toFixed(2))
              : nodeType !== 'File' && nodeType !== 'TestMethod'
                ? DEFAULT_CON_RATE
                : null,
        });
      }
    }
  }
  for (const r of editor.relationRows.filter((x) => x.targetType === 'File' || x.targetType === 'TestMethod')) {
    if (!r.targetId) continue;
    relationDetails.push({
      target_type: r.targetType,
      target_id: r.targetId,
      node_name: r.targetLabel || r.targetId,
      rel_type: r.relType,
      equipment_id: '',
      rate: r.rate ?? null,
    });
  }
  const soleEqId =
    (editor.equipmentTabs || []).length === 1
      ? String(editor.equipmentTabs![0].equipment_id || '').trim()
      : '';
  const detailKeys = new Set(
    relationDetails.map((d) => `${String(d.target_type || '')}:${String(d.target_id || '')}`),
  );
  for (const r of editor.relationRows) {
    if (r.targetType !== 'TestProject' && r.targetType !== 'Indicator') continue;
    if (!r.targetId) continue;
    const k = `${r.targetType}:${r.targetId}`;
    if (detailKeys.has(k)) continue;
    detailKeys.add(k);
    relationDetails.push({
      target_type: r.targetType,
      target_id: r.targetId,
      node_name: r.targetLabel || r.targetId,
      rel_type: 'CON',
      equipment_id: soleEqId && soleEqId !== '__unassigned__' ? soleEqId : '',
      rate: r.rate ?? null,
    });
  }
  const importBody: Record<string, unknown> = {
    method: nodeData,
    relations,
    relation_details: relationDetails,
  };
  if (editor.savedNode?.id) {
    importBody.import_mode = 'replace';
    importBody.replace_node_id = editor.savedNode.id;
  } else if (editor.editorMode === 'edit') {
    const replaceId = editor.replaceNodeId || editor.method.id;
    if (replaceId) {
      importBody.import_mode = 'replace';
      importBody.replace_node_id = replaceId;
    }
  }

  const result = await importMethod(importBody);
  if (!result?.success) {
    return { ok: false, error: result?.error || '保存失败' };
  }
  return {
    ok: true,
    savedNode: result?.data?.node as GraphNode | undefined,
    methodId,
  };
}

function applySavedNodeToEditor(
  editor: MethodEditorState,
  savedNode: GraphNode | undefined,
  methodId: string,
): MethodEditorState {
  if (!savedNode) {
    return { ...editor, savedNode };
  }
  const n = savedNode;
  const cls = n.class;
  const nextClass: MethodClassKind =
    cls === '总则' || cls === '分则' ? cls : editor.method.methodClass;
  return {
    ...editor,
    editorMode: 'edit',
    savedNode,
    replaceNodeId: n.id || editor.replaceNodeId || methodId,
    duplicateChoice: 'replace',
    nameDuplicate: { duplicate: false, existing: null },
    method: {
      ...editor.method,
      id: n.id || editor.method.id || methodId,
      status: normalizeStatusValue(
        n.status != null && String(n.status).trim() !== '' ? n.status : editor.method.status,
      ),
      methodClass: nextClass,
    },
  };
}

function displayMethodSavedTime(editor: MethodEditorState): string {
  const n = editor.savedNode as GraphNode | undefined;
  if (n == null || n.create_time == null) return '—';
  return formatTableDateDisplay(n.create_time);
}

function buildSourceFileRelationRows(
  method: Pick<MethodResult, 'source_id' | 'source_name' | 'recommended_relations'>,
  sourceFileId?: string,
): MethodRelationRow[] {
  const rows = rowsFromExtractRecommendations(method).filter((r) => r.targetType === 'File');
  const fid = normalizeFileNodeId(method.source_id || sourceFileId);
  if (fid && !rows.some((r) => r.targetId === fid)) {
    rows.unshift({
      key: newRelRowKey(),
      targetType: 'File',
      targetId: fid,
      targetLabel: method.source_name || fid,
      relType: 'BELONGS_TO',
      rate: null,
    });
  }
  return rows;
}

function createMethodEditorsFromExtract(
  rawMethods: MethodResult[],
  opts: {
    graphCandidates?: GraphRecommendation[];
    selectedEquipmentId?: string | null;
    equipmentTree?: EquipmentTreeNode[];
    sourceFileId?: string;
  },
): MethodEditorState[] {
  const eqName = opts.selectedEquipmentId
    ? findEquipmentNameInTree(opts.equipmentTree, opts.selectedEquipmentId)
    : undefined;
  const normalizedSourceId = normalizeFileNodeId(opts.sourceFileId || '');

  return rawMethods.map((method, methodIndex) => {
    const nd = method.name_duplicate ?? { duplicate: false, existing: null as GraphNode | null | undefined };
    const equipmentTabs = buildInitialEquipmentTabs({
      graphCandidates: opts.graphCandidates,
      selectedEquipmentId: opts.selectedEquipmentId,
      selectedEquipmentName: eqName,
      equipmentRecommendations: method.equipment_recommendations,
    });
    const graphCandidates = opts.graphCandidates?.length
      ? opts.graphCandidates
      : (method.equipment_recommendations || []).map((e) => ({
          node_type: 'Equipment',
          node_id: e.equipment_id,
          node_name: e.equipment_name,
          score: e.score,
        }));
    const relationRows = mergeRelationRowsWithEquipmentTabs(
      equipmentTabs,
      buildSourceFileRelationRows(method, opts.sourceFileId),
    ) as MethodRelationRow[];

    return {
      key: `method-${Date.now()}-${methodIndex}`,
      editorMode: 'create' as const,
      method: {
        id: method.id || '',
        name: method.name || '',
        type: method.type || '',
        methodClass: inferMethodClass(method),
        status: DEFAULT_FLGL_STATUS,
        label: method.label || labelFromSourceFilename(method.source_name),
        description: method.description || '',
        sy: method.sy || '',
        synr: composeLegacyMethodContent(method),
        source_name: method.source_name || '',
        source_id: normalizeFileNodeId(method.source_id || normalizedSourceId),
        source_section_ids: method.source_section_ids,
        source_section_nos: method.source_section_nos,
        source_category: method.source_category,
        extract_source: method.extract_source,
      },
      relationRows,
      equipmentTabs,
      graphCandidates,
      attachmentRows: [],
      nameDuplicate: { duplicate: !!nd.duplicate, existing: nd.existing ?? null },
      duplicateChoice: null,
      replaceNodeId: undefined,
      showRenameInput: false,
      renameDraft: method.name || '',
    };
  });
}

function createMethodEditorFromStream(
  method: MethodResult,
  ctx: {
    jobId: string;
    methodIndex: number;
    documentDir: string;
    sourceFileId?: string;
    selectedEquipmentId?: string | null;
    equipmentTree?: EquipmentTreeNode[];
    graphCandidates?: GraphRecommendation[];
  },
): MethodEditorState {
  const eqName = ctx.selectedEquipmentId
    ? findEquipmentNameInTree(ctx.equipmentTree, ctx.selectedEquipmentId)
    : undefined;
  const normalizedSourceId = normalizeFileNodeId(ctx.sourceFileId || '');
  const nd = method.name_duplicate ?? { duplicate: false, existing: null as GraphNode | null | undefined };
  const equipmentCandidates = method.equipment_candidates || [];
  const graphCandidates = ctx.graphCandidates?.length
    ? ctx.graphCandidates
    : (method.equipment_recommendations?.length
        ? method.equipment_recommendations
        : equipmentCandidates
      ).map((e) => ({
        node_type: 'Equipment' as const,
        node_id: 'equipment_id' in e ? e.equipment_id : e.node_id,
        node_name: 'equipment_name' in e ? e.equipment_name : e.node_name,
        score: e.score,
      }));
  const equipmentTabs = buildInitialEquipmentTabs({
    graphCandidates,
    selectedEquipmentId: ctx.selectedEquipmentId,
    selectedEquipmentName: eqName,
    equipmentRecommendations: method.equipment_recommendations,
  });
  const relationRows = mergeRelationRowsWithEquipmentTabs(
    equipmentTabs,
    buildSourceFileRelationRows(method, ctx.sourceFileId),
  ) as MethodRelationRow[];

  return {
    key: `method-${ctx.jobId}-${ctx.methodIndex}`,
    editorMode: 'create' as const,
    method: {
      id: method.id || '',
      name: method.name || '',
      type: method.type || '',
      methodClass: inferMethodClass(method),
      status: DEFAULT_FLGL_STATUS,
      label: method.label || labelFromSourceFilename(method.source_name),
      description: method.description || '',
      sy: method.sy || '',
      synr: composeLegacyMethodContent(method),
      source_name: method.source_name || '',
      source_id: normalizeFileNodeId(method.source_id || normalizedSourceId),
      source_section_ids: method.source_section_ids,
      source_section_nos: method.source_section_nos,
      source_category: method.source_category,
      extract_source: method.extract_source,
    },
    relationRows,
    equipmentTabs,
    graphCandidates,
    attachmentRows: [],
    nameDuplicate: { duplicate: !!nd.duplicate, existing: nd.existing ?? null },
    duplicateChoice: null,
    replaceNodeId: undefined,
    showRenameInput: false,
    renameDraft: method.name || '',
    equipmentCandidates,
    streamJobId: ctx.jobId,
    streamMethodIndex: ctx.methodIndex,
    documentDir: ctx.documentDir,
  };
}

export default function MethodPanel({
  selectedEquipmentId, selectedEquipmentIds = [], equipmentTree, onRefresh, autoOpen, onLocateEquipment,
}: {
  allNodes: GraphNode[];
  selectedCategory: string | null;
  selectedEquipmentId?: string | null;
  selectedEquipmentIds?: string[];
  equipmentTree?: EquipmentTreeNode[];
  categoryNodes?: GraphNode[];
  onRefresh: () => void;
  onLocateEquipment?: (equipmentId: string) => void;
  methodCategoryMap?: Record<string, string[]>;
  autoOpen?: boolean;
}) {
  const [drawerVisible, setDrawerVisible] = useState(false);
  const [methodEditors, setMethodEditors] = useState<MethodEditorState[]>([]);
  const [activeMethodTab, setActiveMethodTab] = useState('method-0');
  const [relationModal, setRelationModal] = useState<{ open: boolean; node: GraphNode }>({ open: false, node: {} as GraphNode });
  const [importing, setImporting] = useState(false);
  const [redirecting, setRedirecting] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);
  const [methodRelationTab, setMethodRelationTab] = useState<'TestProject' | 'Indicator'>('TestProject');
  const [relatedMethods, setRelatedMethods] = useState<GraphNode[]>([]);
  const [relatedMethodsLoading, setRelatedMethodsLoading] = useState(false);
  const [fileNodes, setFileNodes] = useState<GraphNode[]>([]);
  const [nodeOptionsByType, setNodeOptionsByType] = useState<Partial<Record<NodeType, GraphNode[]>>>({});
  const nameDebounceRef = useRef<Record<string, number | null>>({});
  const [duplicateModal, setDuplicateModal] = useState<{
    open: boolean;
    tabKey: string;
    matches: GraphNode[];
  }>({ open: false, tabKey: '', matches: [] });
  const [wizardOpen, setWizardOpen] = useState(false);
  const [methodNameFilter, setMethodNameFilter] = useState('');
  const { selectedRowKeys, rowSelection, clearSelection } = useBatchRowSelection<GraphNode>();

  const importBlocked = useMemo(() => {
    if (!methodEditors.length) return true;
    return !methodEditors.some(
      (ed) => ed.method.name?.trim() && ed.duplicateChoice !== 'skip',
    );
  }, [methodEditors]);

  const loadRelatedMethods = useCallback(async () => {
    setRelatedMethodsLoading(true);
    try {
      const equipmentScope = selectedEquipmentIds.length > 0 ? selectedEquipmentIds : selectedEquipmentId ? [selectedEquipmentId] : [];
      if (equipmentScope.length <= 1) {
        const rows = await fetchMethodsByRelation(methodRelationTab, equipmentScope[0]);
        setRelatedMethods(Array.isArray(rows) ? rows : []);
      } else {
        const results = await Promise.all(equipmentScope.map((id) => fetchMethodsByRelation(methodRelationTab, id).catch(() => [])));
        const dedup = new Map<string, GraphNode>();
        results.flat().forEach((row) => {
          if (row?.id) dedup.set(String(row.id), row);
        });
        setRelatedMethods(Array.from(dedup.values()));
      }
    } catch (e: any) {
      message.error(e?.message || '加载方法列表失败');
    } finally {
      setRelatedMethodsLoading(false);
    }
  }, [methodRelationTab, selectedEquipmentId, selectedEquipmentIds]);

  const handleDeleteMethod = async (record: GraphNode) => {
    const id = String(record.id || '').trim();
    if (!id) return;
    try {
      await deleteNode('TestMethod', id);
      message.success('方法已删除');
      setMethodEditors((prev) => {
        const next = prev.filter(
          (ed) => ed.replaceNodeId !== id && ed.savedNode?.id !== id && ed.method.id !== id,
        );
        if (next.length !== prev.length) {
          setDrawerVisible(next.length > 0);
          if (next.length) setActiveMethodTab(next[0].key);
        }
        return next;
      });
      setRefreshKey((k) => k + 1);
      onRefresh();
    } catch (e: unknown) {
      message.error(e instanceof Error ? e.message : '删除失败');
    }
  };

  const handleBatchDeleteMethods = async () => {
    const ids = selectedRowKeys.map(String).filter(Boolean);
    if (!ids.length) return;
    setRelatedMethodsLoading(true);
    try {
      const { ok, failed } = await batchDeleteNodes('TestMethod', ids, (t, id) => deleteNode(t as NodeType, id));
      if (ok) message.success(`已删除 ${ok} 个方法`);
      if (failed.length) message.warning(`${failed.length} 个方法删除失败`);
      clearSelection();
      setRefreshKey((k) => k + 1);
      onRefresh();
    } finally {
      setRelatedMethodsLoading(false);
    }
  };

  const filteredRelatedMethods = useMemo(() => {
    const kw = methodNameFilter.trim().toLowerCase();
    if (!kw) return relatedMethods;
    return relatedMethods.filter((m) => String(m.name || '').toLowerCase().includes(kw));
  }, [relatedMethods, methodNameFilter]);

  useEffect(() => {
    void loadRelatedMethods();
  }, [loadRelatedMethods, refreshKey]);

  useEffect(() => {
    if (!drawerVisible) return;
    (async () => {
      const types: NodeType[] = [
        'EquipmentType',
        'Equipment',
        'Environment',
        'TestProject',
        'Indicator',
        'TestMethod',
        'File',
      ];
      try {
        const results = await Promise.all(types.map((t) => fetchNodes(t).catch(() => [])));
        const map: Partial<Record<NodeType, GraphNode[]>> = {};
        types.forEach((t, i) => {
          map[t] = (Array.isArray(results[i]) ? results[i] : []) as GraphNode[];
        });
        setNodeOptionsByType(map);
        setFileNodes(map.File || []);
      } catch {
        /* 下拉可选，失败忽略 */
      }
    })();
  }, [refreshKey, drawerVisible]);

  useEffect(() => {
    if (!autoOpen) return;
    const rawExtract = sessionStorage.getItem('methodExtractResult');
    if (rawExtract) {
      try {
        const resultData = JSON.parse(rawExtract);
        sessionStorage.removeItem('methodExtractResult');
        if (resultData && resultData.methods && resultData.methods.length > 0) {
          const editors = createMethodEditorsFromExtract(resultData.methods as MethodResult[], {
            graphCandidates: resultData.graphCandidates,
            selectedEquipmentId,
            equipmentTree,
            sourceFileId: resultData.source_file_id,
          });
          setMethodEditors(editors);
          setActiveMethodTab(editors[0]?.key || 'method-0');
          setDrawerVisible(true);
        }
      } catch {
        /* 解析失败，忽略 */
      }
      return;
    }
    const rawZnwd = sessionStorage.getItem('methodCreateFromZnwd');
    if (!rawZnwd) return;
    try {
      const draft = JSON.parse(rawZnwd) as MethodCreateFromZnwdDraft;
      sessionStorage.removeItem('methodCreateFromZnwd');
      const editors = createMethodEditorFromZnwdDraft(draft);
      setMethodEditors(editors as MethodEditorState[]);
      setActiveMethodTab(editors[0]?.key || 'method-0');
      setDrawerVisible(true);
    } catch {
      /* 解析失败，忽略 */
    }
  }, [autoOpen, selectedEquipmentId, equipmentTree]);

  const updateEquipmentTabs = (tabKey: string, tabs: EquipmentTab[]) => {
    setMethodEditors((prev) =>
      prev.map((ed) => {
        if (ed.key !== tabKey) return ed;
        const fileRows = ed.relationRows.filter((r) => r.targetType === 'File' || r.targetType === 'TestMethod');
        return {
          ...ed,
          equipmentTabs: tabs,
          relationRows: mergeRelationRowsWithEquipmentTabs(tabs, fileRows) as MethodRelationRow[],
        };
      }),
    );
  };

  const handleLlmRecommend = async (tabKey: string) => {
    const editor = methodEditors.find((e) => e.key === tabKey);
    if (!editor) return;
    const { method } = editor;
    if (!method.name?.trim() && !method.synr?.trim()) {
      message.warning('请先填写方法名称或 SYNR');
      return;
    }
    setMethodEditors((prev) =>
      prev.map((ed) => (ed.key === tabKey ? { ...ed, recommending: true } : ed)),
    );
    const hide = message.loading('LLM 推荐关联节点中…', 0);
    try {
      const equipmentIds = selectedEquipmentId ? [selectedEquipmentId] : [];
      const res = await recommendMethodRelations({
        method: {
          name: method.name,
          description: method.description,
          synr: method.synr,
          type: method.type,
          label: method.label,
          source_name: method.source_name,
        },
        equipmentIds,
        documentDir: editor.documentDir,
        methodIndex: editor.streamMethodIndex,
        methodName: method.name,
      });
      hide();
      if (!res.success || !res.data) {
        message.error(res.error || '推荐失败');
        return;
      }
      const data = res.data;
      const eqRecs = data.equipment_recommendations || [];
      const graphCandidates: GraphRecommendation[] = eqRecs.length
        ? eqRecs.map((e) => ({
            node_type: 'Equipment' as const,
            node_id: e.equipment_id,
            node_name: e.equipment_name,
            score: e.score,
          }))
        : (data.equipment_candidates || data.recommended_nodes || []).map((e) => ({
            node_type: 'Equipment' as const,
            node_id: e.node_id,
            node_name: e.node_name || e.node_id,
            score: e.score,
          }));
      const eqName = selectedEquipmentId
        ? findEquipmentNameInTree(equipmentTree, selectedEquipmentId)
        : undefined;
      const equipmentTabs = buildInitialEquipmentTabs({
        graphCandidates,
        selectedEquipmentId,
        selectedEquipmentName: eqName,
        equipmentRecommendations: eqRecs,
      });
      let projectCount = 0;
      let indicatorCount = 0;
      for (const eq of eqRecs) {
        projectCount += (eq.projects || []).length;
        indicatorCount += (eq.indicators || []).length;
      }
      if (!eqRecs.length) {
        for (const tab of equipmentTabs) {
          for (const row of tab.rows || []) {
            if (row.node_type === 'TestProject') projectCount += 1;
            if (row.node_type === 'Indicator') indicatorCount += 1;
          }
        }
      }
      setMethodEditors((prev) =>
        prev.map((ed) => {
          if (ed.key !== tabKey) return ed;
          const fileRows = ed.relationRows.filter(
            (r) => r.targetType === 'File' || r.targetType === 'TestMethod',
          );
          const equipmentCandidates = (data.equipment_candidates || []).map((c) => ({
            node_id: c.node_id,
            node_name: c.node_name || c.node_id,
            score: c.score,
          }));
          return {
            ...ed,
            equipmentTabs,
            graphCandidates,
            equipmentCandidates: equipmentCandidates.length ? equipmentCandidates : ed.equipmentCandidates,
            relationRows: mergeRelationRowsWithEquipmentTabs(equipmentTabs, fileRows) as MethodRelationRow[],
            recommending: false,
          };
        }),
      );
      const scopeHint = selectedEquipmentId && eqName
        ? `已在设备「${eqName}」下`
        : equipmentTabs.length > 1
          ? `已为 ${equipmentTabs.length} 个设备`
          : equipmentTabs[0]?.equipment_name
            ? `已在设备「${equipmentTabs[0].equipment_name}」下`
            : '已';
      if (projectCount + indicatorCount === 0) {
        message.info(`${scopeHint}未匹配到推荐项目/指标，请检查方法内容或手动添加`);
      } else {
        message.success(
          `${scopeHint}推荐 ${projectCount} 个项目、${indicatorCount} 个指标`,
        );
      }
    } catch (e: unknown) {
      hide();
      message.error(e instanceof Error ? e.message : '推荐失败');
    } finally {
      setMethodEditors((prev) =>
        prev.map((ed) => (ed.key === tabKey ? { ...ed, recommending: false } : ed)),
      );
    }
  };

  const filteredNodeOptionsByType = useMemo(() => {
    const equipmentScope = selectedEquipmentIds.length > 0 ? selectedEquipmentIds : selectedEquipmentId ? [selectedEquipmentId] : [];
    if (equipmentScope.length === 0) return nodeOptionsByType;
    const inCurrentEquipment = (n: GraphNode) =>
      Array.isArray(n.equipment_ids) && equipmentScope.some((id) => n.equipment_ids.includes(id));
    return {
      ...nodeOptionsByType,
      TestProject: (nodeOptionsByType.TestProject || []).filter(inCurrentEquipment),
      Indicator: (nodeOptionsByType.Indicator || []).filter(inCurrentEquipment),
    };
  }, [nodeOptionsByType, selectedEquipmentId, selectedEquipmentIds]);

  const fileSelectOptions = useMemo(
    () =>
      fileNodes.map((f) => ({
        value: f.id,
        label: (f.name || f.label || f.id) as string,
      })),
    [fileNodes],
  );

  const updateFileSource = (tabKey: string, fileId: string | null) => {
    const f = fileId ? fileNodes.find((n) => n.id === fileId) : undefined;
    const normalizedId = normalizeFileNodeId(fileId || '');
    setMethodEditors((prev) =>
      prev.map((editor) => {
        if (editor.key !== tabKey) return editor;
        const fileRows = normalizedId
          ? ([{
              key: newRelRowKey(),
              targetType: 'File' as const,
              targetId: normalizedId,
              targetLabel: f ? String(f.name || f.label || '') : normalizedId,
              relType: 'BELONGS_TO' as const,
              rate: null,
            }])
          : [];
        const otherRows = editor.relationRows.filter(
          (r) => r.targetType !== 'File' || (normalizedId && r.targetId !== normalizedId),
        );
        return {
          ...editor,
          method: {
            ...editor.method,
            source_id: normalizedId,
            source_name: f ? String(f.name || f.label || '') : '',
          },
          relationRows: normalizedId
            ? mergeRelationRowsWithEquipmentTabs(editor.equipmentTabs || [], [
                ...otherRows.filter((r) => r.targetType === 'File' || r.targetType === 'TestMethod'),
                ...fileRows,
              ]) as MethodRelationRow[]
            : otherRows,
        };
      }),
    );
  };

  const updateRelationRow = (tabKey: string, rowKey: string, patch: Partial<MethodRelationRow>) => {
    setMethodEditors((prev) =>
      prev.map((ed) => {
        if (ed.key !== tabKey) return ed;
        return {
          ...ed,
          relationRows: ed.relationRows.map((r) => {
            if (r.key !== rowKey) return r;
            const next: MethodRelationRow = { ...r, ...patch };
            if (Object.prototype.hasOwnProperty.call(patch, 'targetType') && patch.targetType !== r.targetType) {
              next.targetId = '';
              next.targetLabel = '';
            }
            return next;
          }),
        };
      }),
    );
  };

  const addRelationRow = (tabKey: string) => {
    setMethodEditors((prev) =>
      prev.map((ed) => {
        if (ed.key !== tabKey) return ed;
        return {
          ...ed,
          relationRows: [
            ...ed.relationRows,
            {
              key: newRelRowKey(),
              targetType: 'TestProject',
              targetId: '',
              targetLabel: '',
              relType: 'CON',
              rate: null,
            },
          ],
        };
      }),
    );
  };

  const removeRelationRow = (tabKey: string, rowKey: string) => {
    setMethodEditors((prev) =>
      prev.map((ed) => (ed.key === tabKey ? { ...ed, relationRows: ed.relationRows.filter((r) => r.key !== rowKey) } : ed)),
    );
  };

  const addAttachmentRow = (tabKey: string, fileNode: GraphNode) => {
    const fileId = String(fileNode.id || '').trim();
    if (!fileId) return;
    const row: MethodRelationRow = {
      key: newRelRowKey(),
      targetType: 'File',
      targetId: fileId,
      relType: 'BELONGS_TO',
      rate: null,
    };
    setMethodEditors((prev) =>
      prev.map((ed) => {
        if (ed.key !== tabKey) return ed;
        if (ed.attachmentRows.some((r) => r.targetId === fileId)) return ed;
        return {
          ...ed,
          attachmentRows: [...ed.attachmentRows, row],
          relationRows: ed.relationRows.some((r) => r.targetId === fileId)
            ? ed.relationRows
            : [...ed.relationRows, row],
        };
      }),
    );
  };

  const removeAttachmentRow = (tabKey: string, fileId: string) => {
    setMethodEditors((prev) =>
      prev.map((ed) => ed.key === tabKey
        ? {
            ...ed,
            attachmentRows: ed.attachmentRows.filter((r) => r.targetId !== fileId),
            relationRows: ed.relationRows.filter((r) => !(r.targetType === 'File' && r.targetId === fileId)),
          }
        : ed),
    );
  };

  const getOrCreateEditorMethodId = async (tabKey: string): Promise<string> => {
    const editor = methodEditors.find((ed) => ed.key === tabKey);
    if (!editor) throw new Error('未找到当前方法');
    const existing = await ensureMethodId(editor);
    if (existing && existing !== editor.method.id) {
      setMethodEditors((prev) => prev.map((ed) => ed.key === tabKey ? { ...ed, method: { ...ed.method, id: existing } } : ed));
    }
    return existing;
  };

  const handleMethodImageUpload = async (tabKey: string, file: File) => {
    const methodId = await getOrCreateEditorMethodId(tabKey);
    const result = await uploadMethodImage(file, methodId, getCurrentUserName());
    const fileNode = result.file_node || ({ id: result.file_id, name: file.name } as GraphNode);
    setFileNodes((prev) => [fileNode, ...prev.filter((n) => n.id !== fileNode.id)]);
    setNodeOptionsByType((prev) => ({
      ...prev,
      File: [fileNode, ...(prev.File || []).filter((n) => n.id !== fileNode.id)],
    }));
    addAttachmentRow(tabKey, fileNode);
    return { url: result.url, alt: file.name, href: result.url };
  };

  const handleAttachmentUpload = async (tabKey: string, file: File): Promise<GraphNode | null> => {
    try {
      const result = await uploadMethodAttachment(file, getCurrentUserName());
      const fileNode = result.file_node || ({ id: result.file_id, name: file.name } as GraphNode);
      setFileNodes((prev) => [fileNode, ...prev.filter((n) => n.id !== fileNode.id)]);
      setNodeOptionsByType((prev) => ({
        ...prev,
        File: [fileNode, ...(prev.File || []).filter((n) => n.id !== fileNode.id)],
      }));
      addAttachmentRow(tabKey, fileNode);
      message.success('附件上传成功');
      return fileNode;
    } catch (e: any) {
      message.error(e?.message || '附件上传失败');
      return null;
    }
  };

  const handleWizardComplete = (result: MethodExtractWizardResult) => {
    const rawMethods = (result.methods || []) as MethodResult[];
    if (!rawMethods.length) {
      message.warning('未提取到方法');
      return;
    }
    const editors = createMethodEditorsFromExtract(rawMethods, {
      graphCandidates: result.graphCandidates,
      selectedEquipmentId,
      equipmentTree,
      sourceFileId: result.source_file_id,
    });
    setMethodEditors(editors);
    setActiveMethodTab(editors[0]?.key || 'method-0');
    const firstName = editors[0]?.method.name?.trim();
    if (firstName && editors[0]?.nameDuplicate.duplicate) {
      void checkMethodNameDuplicate(firstName).then((dupRes) => {
        if (!dupRes?.success || !dupRes.data?.duplicate) return;
        const matches =
          (Array.isArray(dupRes.data.matches) && dupRes.data.matches.length
            ? dupRes.data.matches
            : dupRes.data.existing
              ? [dupRes.data.existing]
              : []) as GraphNode[];
        if (matches.length && editors[0]) {
          setDuplicateModal({ open: true, tabKey: editors[0].key, matches });
        }
      });
    }
    setDrawerVisible(true);
  };

  const appendMethodEditorFromStream = useCallback((method: MethodResult, ctx: {
    jobId: string;
    methodIndex: number;
    documentDir: string;
    sourceFileId?: string;
  }) => {
    const editor = createMethodEditorFromStream(method, {
      ...ctx,
      selectedEquipmentId,
      equipmentTree,
    });
    setMethodEditors((prev) => {
      const exists = prev.find((e) => e.key === editor.key);
      if (exists) {
        return prev.map((e) => (e.key === editor.key ? editor : e));
      }
      return [...prev, editor];
    });
    setActiveMethodTab(editor.key);
    setDrawerVisible(true);
    setWizardOpen(false);
  }, [selectedEquipmentId, equipmentTree]);

  const handleStreamStart = useCallback(() => {
    setMethodEditors([]);
    setDrawerVisible(true);
    setWizardOpen(false);
  }, []);

  const handleStreamDone = useCallback((ctx: { methodsCount?: number }) => {
    if (ctx.methodsCount != null && ctx.methodsCount > 0) {
      message.success(`LLM 提取完成，共 ${ctx.methodsCount} 个方法`);
    }
  }, []);

  const updateEditableField = (tabKey: string, field: keyof EditableMethod, value: string) => {
    setMethodEditors((prev) =>
      prev.map((editor) =>
        editor.key === tabKey ? { ...editor, method: { ...editor.method, [field]: value } } : editor,
      ),
    );
  };

  const applyNameDuplicateFromServer = useCallback((tabKey: string, name: string) => {
    if (!name.trim()) {
      setMethodEditors((prev) =>
        prev.map((ed) =>
          ed.key === tabKey
            ? { ...ed, nameDuplicate: { duplicate: false, existing: null }, duplicateChoice: null, replaceNodeId: undefined }
            : ed,
        ),
      );
      setDuplicateModal((m) => (m.tabKey === tabKey ? { ...m, open: false, matches: [] } : m));
      return;
    }
    void checkMethodNameDuplicate(name).then((res) => {
      if (!res?.success || res.data == null) return;
      const dup = res.data.duplicate;
      const matches =
        (Array.isArray(res.data.matches) && res.data.matches.length
          ? res.data.matches
          : res.data.existing
            ? [res.data.existing]
            : []) as GraphNode[];
      let shouldOpenModal = false;
      setMethodEditors((prev) => {
        const next = prev.map((ed) => {
          if (ed.key !== tabKey) return ed;
          if (ed.method.name?.trim() !== name.trim()) return ed;
          if (dup && ed.editorMode === 'create' && !ed.savedNode?.id && matches.length) {
            shouldOpenModal = true;
          }
          return {
            ...ed,
            nameDuplicate: { duplicate: dup, existing: res.data!.existing ?? null },
            duplicateChoice: dup ? ed.duplicateChoice : null,
            replaceNodeId: dup ? ed.replaceNodeId : undefined,
          };
        });
        return next;
      });
      if (shouldOpenModal) {
        setDuplicateModal({ open: true, tabKey, matches });
      }
    });
  }, []);

  const handleCopySynrFromDuplicate = useCallback(() => {
    const { tabKey, matches } = duplicateModal;
    const src = matches[0];
    const synr = graphNodeSynr(src);
    if (!synr) {
      message.warning('库中同名方法暂无 SYNR 内容可复制');
      return;
    }
    setMethodEditors((prev) =>
      prev.map((ed) => (ed.key === tabKey ? { ...ed, method: { ...ed.method, synr } } : ed)),
    );
    setDuplicateModal({ open: false, tabKey: '', matches: [] });
  }, [duplicateModal]);

  const onMethodNameChange = (tabKey: string, value: string) => {
    setMethodEditors((prev) =>
      prev.map((ed) =>
        ed.key === tabKey
          ? {
              ...ed,
              method: { ...ed.method, name: value },
              duplicateChoice: null,
              replaceNodeId: undefined,
              showRenameInput: false,
              renameDraft: value,
            }
          : ed,
      ),
    );
    const tprev = nameDebounceRef.current[tabKey];
    if (tprev) window.clearTimeout(tprev);
    nameDebounceRef.current[tabKey] = window.setTimeout(() => {
      applyNameDuplicateFromServer(tabKey, value.trim());
    }, 450);
  };

  const handleOpenRelationModal = (editor: MethodEditorState) => {
    if (!editor.savedNode?.id) {
      message.warning('请先保存当前方法，再管理关联');
      return;
    }
    setRelationModal({
      open: true,
      node: editor.savedNode,
    });
  };

  const handleImport = async () => {
    if (!methodEditors.length) return false;

    const targets = methodEditors.filter(
      (ed) => ed.method.name?.trim() && ed.duplicateChoice !== 'skip',
    );
    if (!targets.length) {
      message.warning('没有可保存的方法');
      return false;
    }

    setImporting(true);
    try {
      const savedMap = new Map<string, { savedNode?: GraphNode; methodId: string }>();
      const errors: string[] = [];

      for (const editor of targets) {
        const result = await persistMethodEditor(editor);
        if (result.ok) {
          savedMap.set(editor.key, {
            savedNode: result.savedNode,
            methodId: result.methodId || editor.method.id || '',
          });
        } else if (result.error && result.error !== '已跳过') {
          errors.push(`${editor.method.name?.trim() || '未命名方法'}：${result.error}`);
        }
      }

      if (savedMap.size > 0) {
        setMethodEditors((prev) =>
          prev.map((editor) => {
            const hit = savedMap.get(editor.key);
            if (!hit) return editor;
            return applySavedNodeToEditor(editor, hit.savedNode, hit.methodId);
          }),
        );
        setRefreshKey((k) => k + 1);
        onRefresh();
      }

      if (savedMap.size === 0) {
        message.error(errors[0] || '保存失败');
        return false;
      }
      if (errors.length > 0) {
        message.warning(`已保存 ${savedMap.size} 个方法，${errors.length} 个失败：${errors.join('；')}`);
      } else if (savedMap.size > 1) {
        message.success(`已保存 ${savedMap.size} 个方法`);
      } else {
        message.success('保存成功');
      }
      setDrawerVisible(false);
      setMethodEditors([]);
      setActiveMethodTab('');
      return true;
    } catch (e: any) {
      console.error('method-import failed:', e);
      message.error(e?.message || '导入失败');
      return false;
    } finally {
      setImporting(false);
    }
  };

  const handleImportAndUploadFile = async () => {
    const ok = await handleImport();
    if (ok) {
      setRedirecting(true);
      message.loading({ content: '正在跳转至文件管理界面...', key: 'redirect-file-page', duration: 0 });
      window.setTimeout(() => {
        window.location.href = '/ZSGL/WJGL';
      }, 300);
    }
  };


  const extraButtons = (
    <Space>
      <Input
        placeholder="按方法名称筛选"
        prefix={<SearchOutlined />}
        value={methodNameFilter}
        onChange={(e) => setMethodNameFilter(e.target.value)}
        allowClear
        style={{ width: 180 }}
      />
      <Button icon={<UploadOutlined />} onClick={() => setWizardOpen(true)}>
        从文件提取方法
      </Button>
    </Space>
  );

  const relatedColumns = [
    {
      title: 'ID',
      dataIndex: 'id',
      key: 'id',
      width: 100,
      sorter: (a: GraphNode, b: GraphNode) =>
        String(a.id ?? '').localeCompare(String(b.id ?? ''), undefined, { numeric: true }),
    },
    { title: '名称', dataIndex: 'name', key: 'name', width: 160, ellipsis: true },
    {
      title: methodRelationTab === 'TestProject' ? '关联项目名称' : '关联指标名称',
      dataIndex: 'related_names',
      key: 'related_names',
      width: 180,
      ellipsis: true,
      render: (names: string[]) => Array.isArray(names) && names.length ? names.join('、') : '—',
    },
    {
      title: '关联装备',
      dataIndex: 'equipment_names',
      key: 'equipment_names',
      width: 160,
      ellipsis: true,
      render: (_: unknown, record: GraphNode) => (
        <EquipmentNameLinks
          equipmentIds={record.equipment_ids as string[] | undefined}
          equipmentNames={record.equipment_names as string[] | undefined}
          onLocate={onLocateEquipment}
        />
      ),
    },
    { title: '描述', dataIndex: 'description', key: 'description', ellipsis: true },
    {
      title: '来源文件',
      dataIndex: 'source_name',
      key: 'source_name',
      width: 180,
      ellipsis: true,
      render: (_: unknown, record: GraphNode) => {
        const sectionNo = firstSourceSectionNo(record.source_section_nos);
        const href = buildMethodSourceReadUrl({
          sourceId: String(record.source_id || ''),
          fileIds: record.file_ids as string[] | undefined,
          sourceName: String(record.source_name || record.file_names?.[0] || '').trim(),
        });
        const label = String(record.source_name || record.file_names?.[0] || '').trim() || '—';
        if (!href) return label;
        return (
          <a
            href={href}
            target="_blank"
            rel="noreferrer"
            onClick={(e) => e.stopPropagation()}
            title={sectionNo ? `打开阅读页（来源章节 §${sectionNo}）` : '打开阅读页'}
          >
            {label}
            {sectionNo ? ` §${sectionNo}` : ''}
          </a>
        );
      },
    },
    {
      title: '状态', dataIndex: 'status', key: 'status', width: 80,
      render: (val: string) => <StatusTag value={val} />,
    },
    { title: '编辑用户', dataIndex: 'user_name', key: 'user_name', width: 100 },
    { title: '编辑时间', dataIndex: 'create_time', key: 'create_time', width: 130, render: (v: string) => <Text>{formatTableDateDisplay(v)}</Text> },
    {
      title: '操作', key: 'action', width: 120,
      render: (_: unknown, record: GraphNode) => (
        <Space>
          <Button
            type="link"
            size="small"
            icon={<EditOutlined />}
            onClick={async () => {
              let relationRows: MethodRelationRow[] = [];
              let equipmentTabs: EquipmentTab[] = [];
              let graphCandidates: GraphRecommendation[] = [];
              const mid = String(record.id || '').trim();
              if (mid) {
                try {
                  const rels = await fetchNodeRelations('TestMethod', mid);
                  relationRows = rowsFromAllMethodGraphRelations(rels) as MethodRelationRow[];
                  const tabPayload = await fetchMethodEquipmentTabs(mid);
                  const eqRecs = tabPayload?.equipment_recommendations || [];
                  const fromRelations = buildEquipmentTabsFromMethodRelations(rels);
                  if (eqRecs.length) {
                    const fromApi = buildInitialEquipmentTabs({
                      equipmentRecommendations: eqRecs,
                      rowLimit: 0,
                    });
                    equipmentTabs = mergeSavedMethodEquipmentTabs(fromApi, fromRelations);
                    graphCandidates = eqRecs.map((e) => ({
                      node_type: 'Equipment' as const,
                      node_id: e.equipment_id,
                      node_name: e.equipment_name,
                      score: e.score,
                    }));
                  } else {
                    equipmentTabs = fromRelations;
                    graphCandidates = graphCandidatesFromMethodRelations(rels);
                  }
                } catch {
                  message.warning('加载图谱关联失败');
                }
              }
              const attachmentRows = relationRows.filter(
                (r) => r.targetType === 'File' || r.targetType === 'TestMethod',
              );
              const editor: MethodEditorState = {
                key: 'method-edit-0',
                editorMode: 'edit',
                method: {
                  id: record.id || '',
                  name: record.name || '',
                  type: record.type || '',
                  methodClass: inferMethodClass(record),
                  status: normalizeStatusValue(record.status),
                  label: record.label || '',
                  description: record.description || '',
                  sy: record.sy || '',
                  synr: composeLegacyMethodContent(record as any),
                  source_name: record.source_name || '',
                  source_id: normalizeFileNodeId(record.source_id),
                  source_section_ids: record.source_section_ids,
                  source_section_nos: record.source_section_nos,
                  source_category: record.source_category,
                  extract_source: record.extract_source,
                },
                savedNode: record,
                relationRows: mergeRelationRowsWithEquipmentTabs(equipmentTabs, attachmentRows) as MethodRelationRow[],
                equipmentTabs,
                graphCandidates,
                attachmentRows,
                nameDuplicate: { duplicate: false, existing: null },
                duplicateChoice: 'replace',
                replaceNodeId: record.id,
                showRenameInput: false,
                renameDraft: record.name || '',
              };
              setMethodEditors([editor]);
              setActiveMethodTab(editor.key);
              setDrawerVisible(true);
            }}
          >
            编辑
          </Button>
          <Popconfirm
            title="确定删除该方法？"
            description="将删除图数据库中的方法节点及其全部关联关系，不会删除来源文件。"
            onConfirm={() => void handleDeleteMethod(record)}
          >
            <Button type="link" danger size="small" icon={<DeleteOutlined />}>删除</Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  const relatedMethodsPagination = useTablePagination(filteredRelatedMethods.length, {
    resetDeps: [methodRelationTab, selectedEquipmentId, selectedEquipmentIds, methodNameFilter],
  });

  return (
    <>
      <Card
        title="方法管理"
        extra={<Space>
          {extraButtons}
          {selectedRowKeys.length > 0 && (
            <Popconfirm title={`确定删除选中的 ${selectedRowKeys.length} 个方法？`} onConfirm={() => void handleBatchDeleteMethods()}>
              <Button danger icon={<DeleteOutlined />}>批量删除</Button>
            </Popconfirm>
          )}
          <Button type="primary" icon={<PlusOutlined />} onClick={() => {
          if (!selectedEquipmentId) {
            message.warning('请先在左侧选择三级设备');
            return;
          }
          const initTabs = buildInitialEquipmentTabs({
            selectedEquipmentId,
            selectedEquipmentName: findEquipmentNameInTree(equipmentTree, selectedEquipmentId),
          });
          const editor: MethodEditorState = {
            key: 'method-new-0',
            editorMode: 'create',
            method: {
              id: '',
              name: '',
              type: '分则',
              methodClass: '分则',
              status: DEFAULT_FLGL_STATUS,
              label: '',
              description: '',
              sy: '',
              synr: '',
              source_name: '',
              source_id: '',
            },
            relationRows: mergeRelationRowsWithEquipmentTabs(initTabs, []) as MethodRelationRow[],
            equipmentTabs: initTabs,
            graphCandidates: [],
            attachmentRows: [],
            nameDuplicate: { duplicate: false, existing: null },
            duplicateChoice: null,
            replaceNodeId: undefined,
            showRenameInput: false,
            renameDraft: '',
          };
          setMethodEditors([editor]);
          setActiveMethodTab(editor.key);
          setDrawerVisible(true);
        }}>新增</Button></Space>}
      >
        <Tabs
          activeKey={methodRelationTab}
          onChange={(key) => setMethodRelationTab(key as 'TestProject' | 'Indicator')}
          items={[
            {
              key: 'TestProject',
              label: '项目相关',
              children: <Table columns={relatedColumns} dataSource={filteredRelatedMethods} rowKey="id" rowSelection={rowSelection} loading={relatedMethodsLoading} size="small" pagination={relatedMethodsPagination} scroll={{ x: 1200 }} />,
            },
            {
              key: 'Indicator',
              label: '指标相关',
              children: <Table columns={relatedColumns} dataSource={filteredRelatedMethods} rowKey="id" rowSelection={rowSelection} loading={relatedMethodsLoading} size="small" pagination={relatedMethodsPagination} scroll={{ x: 1200 }} />,
            },
          ]}
        />
      </Card>

      <MethodExtractWizard
        open={wizardOpen}
        onClose={() => setWizardOpen(false)}
        onComplete={handleWizardComplete}
        onStreamStart={handleStreamStart}
        onStreamMethod={(method, ctx) => appendMethodEditorFromStream(method as MethodResult, ctx)}
        onStreamDone={handleStreamDone}
      />

      <Drawer
        title="方法详情"
        open={drawerVisible}
        onClose={() => setDrawerVisible(false)}
        size={1200}
        footer={
          <div style={{ textAlign: 'right' }}>
            <Space>
              <Button onClick={() => setDrawerVisible(false)}>取消</Button>
              <Button
                type="primary"
                onClick={() => {
                  void handleImport();
                }}
                disabled={importBlocked}
                loading={importing || redirecting}
              >
                {methodEditors.length > 1 ? `保存全部（${methodEditors.length}）` : '保存'}
              </Button>
              {/* <Button
                onClick={() => {
                  void handleImportAndUploadFile();
                }}
                disabled={importBlocked}
                loading={importing || redirecting}
              >
                保存并添加文件
              </Button> */}
            </Space>
          </div>
        }
      >
        {!methodEditors.length ? (
          <Spin description="暂无数据" />
        ) : (
          <Tabs
            activeKey={activeMethodTab}
            onChange={setActiveMethodTab}
            destroyOnHidden
            items={methodEditors.map((editor, index) => {
              const slots = (editor.method as MethodResult & { content_slots?: { missing?: string[] } }).content_slots;
              const incomplete = slots?.missing?.length && !slots?.complete;
              return {
              key: editor.key,
              label: (
                <Space size={4}>
                  <span>{editor.method.name?.trim() || `方法${index + 1}`}</span>
                  {incomplete ? <Tag color="orange" style={{ margin: 0 }}>部分缺失</Tag> : null}
                </Space>
              ),
              children: (
                <div>
                  <Title level={5}>方法信息</Title>
                  <Form layout="vertical" style={{ marginBottom: 24 }}>
                    <Row gutter={16}>
                      <Col xs={24} md={12}>
                        <Form.Item label="名称" required>
                          <Input value={editor.method.name} onChange={(e) => onMethodNameChange(editor.key, e.target.value)} />
                        </Form.Item>
                      </Col>
                      <Col xs={24} md={12}>
                        <Form.Item label="标识">
                          <Input value={editor.method.label} onChange={(e) => updateEditableField(editor.key, 'label', e.target.value)} />
                        </Form.Item>
                      </Col>
                    </Row>
                    <Form.Item label="摘要">
                      <MathAwareTextArea
                        rows={3}
                        value={editor.method.description}
                        onChange={(e) => updateEditableField(editor.key, 'description', e.target.value)}
                      />
                    </Form.Item>


                    <Form.Item label="方法内容">
                      <div style={{ maxWidth: '100%', overflowX: 'auto' }}>
                        <MethodSynrEditor
                          value={editor.method.synr}
                          onChange={(v) => updateEditableField(editor.key, 'synr', v)}
                          uploadImage={(file) => handleMethodImageUpload(editor.key, file)}
                          height={360}
                          active={drawerVisible && activeMethodTab === editor.key}
                        />
                      </div>
                    </Form.Item>

                    {editor.attachmentRows.length > 0 ? (
                      <Form.Item label="附件">
                        <Space wrap>
                          {editor.attachmentRows.map((r) => {
                            const file = (nodeOptionsByType.File || []).find((f) => f.id === r.targetId);
                            const label = String(file?.name || file?.label || r.targetId);
                            return (
                              <Tag key={r.key} closable onClose={(e) => { e.preventDefault(); removeAttachmentRow(editor.key, r.targetId); }}>
                                {label}
                              </Tag>
                            );
                          })}
                        </Space>
                      </Form.Item>
                    ) : null}
                    <Row gutter={16}>
                      <Col xs={24} md={12}>
                        <Form.Item label="来源文件">
                          <Space.Compact style={{ width: '100%' }}>
                            <Select
                              showSearch
                              allowClear
                              virtual={false}
                              placeholder="选择已有文件"
                              style={{ width: '100%' }}
                              optionFilterProp="label"
                              value={editor.method.source_id || undefined}
                              options={fileSelectOptions}
                              onChange={(id) => updateFileSource(editor.key, (id as string) || null)}
                            />
                            <Upload
                              showUploadList={false}
                              beforeUpload={(file) => {
                                void handleAttachmentUpload(editor.key, file);
                                return false;
                              }}
                            >
                              <Button icon={<UploadOutlined />}>上传</Button>
                            </Upload>
                            {(() => {
                              const sectionNo = firstSourceSectionNo(editor.method.source_section_nos);
                              const href = buildMethodSourceReadUrl({
                                sourceId: editor.method.source_id,
                                sourceName: editor.method.source_name,
                              });
                              if (!href) return null;
                              return (
                                <Button
                                  icon={<LinkOutlined />}
                                  title={sectionNo ? `打开阅读页（来源章节 §${sectionNo}）` : '打开阅读页'}
                                  onClick={() => window.open(href, '_blank', 'noopener,noreferrer')}
                                />
                              );
                            })()}
                          </Space.Compact>
                        </Form.Item>
                      </Col>
                      <Col xs={24} md={6}>
                        <Form.Item label="方法类别">
                          <Select
                            value={editor.method.methodClass}
                            options={[{ label: '分则', value: '分则' }, { label: '总则', value: '总则' }]}
                            onChange={(v) => updateEditableField(editor.key, 'methodClass', v)}
                          />
                        </Form.Item>
                      </Col>
                      <Col xs={24} md={6}>
                        <Form.Item label="状态">
                          <Select
                            value={normalizeStatusValue(editor.method.status)}
                            options={METHOD_STATUS_OPTIONS}
                            onChange={(v) => updateEditableField(editor.key, 'status', v)}
                          />
                        </Form.Item>
                      </Col>
                    </Row>
                    <Row gutter={16}>
                      <Col xs={24} md={8}>
                        <Form.Item label="编辑用户">
                          <Input readOnly value={editor.savedNode ? displayMethodSavedUser(editor) : getCurrentUserName()} />
                        </Form.Item>
                      </Col>
                      <Col xs={24} md={8}>
                        <Form.Item label="编辑时间">
                          <Input readOnly value={editor.savedNode ? displayMethodSavedTime(editor) : getCurrentTimeString()} />
                        </Form.Item>
                      </Col>
                    </Row>
                  </Form>

                  <Title level={5} style={{ marginTop: 0 }}>
                    图谱关联
                  </Title>
                  <Paragraph type="secondary" style={{ marginBottom: 12, marginTop: 0 }}>
                    章节提取会自动预填推荐；也可在编辑方法内容后点击「LLM 推荐关联节点」重新生成。新增方法时按左侧所选设备推荐项目与指标。
                  </Paragraph>
                  <DeviceLinkTabs
                    equipmentTabs={editor.equipmentTabs}
                    recommendations={editor.graphCandidates}
                    onChange={(tabs) => updateEquipmentTabs(editor.key, tabs)}
                  />
                  <div style={{ marginTop: 12 }}>
                    <Button
                      type="default"
                      loading={editor.recommending}
                      disabled={!!editor.recommending}
                      onClick={() => void handleLlmRecommend(editor.key)}
                    >
                      LLM 推荐关联节点
                    </Button>
                  </div>

                  {/* <div style={{ marginBottom: 16, padding: 12, background: '#f5f5f5', borderRadius: 8, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <div>
                      <Text strong>关联管理：</Text>
                      <div style={{ marginTop: 8 }}>
                        <Text type="secondary">
                          保存方法后，可在此用「管理关联」在图库中继续维护与设备、环境、项目、指标、文件等关系。
                        </Text>
                      </div>
                    </div>
                    <Button icon={<LinkOutlined />} onClick={() => handleOpenRelationModal(editor)}>
                      管理关联
                    </Button>
                  </div> */}
                </div>
              ),
            };
            })}
          />
        )}
      </Drawer>

      <Modal
        title="发现同名试验方法"
        open={duplicateModal.open}
        width={760}
        onCancel={() => setDuplicateModal({ open: false, tabKey: '', matches: [] })}
        footer={[
          <Button key="cancel" onClick={() => setDuplicateModal({ open: false, tabKey: '', matches: [] })}>
            取消
          </Button>,
          <Button key="copy" type="primary" onClick={handleCopySynrFromDuplicate}>
            一键复制
          </Button>,
        ]}
      >
        <Paragraph type="secondary" style={{ marginBottom: 12 }}>
          库中已存在同名试验方法。可点击「一键复制」将已有方法的 SYNR 填入当前表单；若需新建，请修改名称后再点保存。
        </Paragraph>
        {duplicateModal.matches.map((m) => {
          const synr = graphNodeSynr(m);
          return (
            <Card key={String(m.id)} size="small" style={{ marginBottom: 12 }}>
              <div style={{ marginBottom: 8 }}>
                <Text strong>{m.name || '—'}</Text>
                {m.id ? (
                  <Text type="secondary" style={{ marginLeft: 8, fontSize: 12 }}>
                    {m.id}
                  </Text>
                ) : null}
              </div>
              <Text type="secondary" style={{ display: 'block', marginBottom: 6, fontSize: 12 }}>
                SYNR（方法内容）
              </Text>
              {synr ? (
                <div
                  className="flgl-method-synr-preview"
                  style={{
                    maxHeight: 280,
                    overflow: 'auto',
                    padding: 12,
                    background: '#fafafa',
                    border: '1px solid #f0f0f0',
                    borderRadius: 6,
                    fontSize: 13,
                    lineHeight: 1.6,
                  }}
                >
                  <style>{SYNR_PREVIEW_STYLES}</style>
                  <MixedContent content={synr} />
                </div>
              ) : (
                <Text type="secondary">（无 SYNR 内容）</Text>
              )}
            </Card>
          );
        })}
      </Modal>

      <RelationModal
        open={relationModal.open}
        nodeId={relationModal.node.id}
        nodeType="TestMethod"
        nodeName={relationModal.node.name || ''}
        onClose={() => setRelationModal({ open: false, node: {} as GraphNode })}
        onSaved={() => { onRefresh(); }}
      />
    </>
  );
}
