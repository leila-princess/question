"use client";
import React, { useState, useEffect, useCallback, useMemo, Suspense } from 'react';
import { 
  Card, Input, Button, Space, Modal, message, Typography, Row, Col, 
  Empty, Spin, Tag, Popconfirm,
} from 'antd';
import {
  SaveOutlined, HistoryOutlined,
  ThunderboltOutlined, EyeOutlined, CloudUploadOutlined,
  ArrowLeftOutlined, ArrowRightOutlined, DeleteOutlined,
} from '@ant-design/icons';
import SchemeExportDropdown from '../components/SchemeExportDropdown';
import SchemeKmSyncModal from '../components/SchemeKmSyncModal';
import { buildJygzKmMeta, type SchemeExportFormat } from '../lib/schemeKmMeta';
import { downloadBlob, exportJygzBlob, fileFromExport } from '../lib/schemeKmSync';
import type { UploadFileMeta } from '@/app/ZSGL/WJGL/utils/wjglUploadPipeline';
import { create } from 'zustand';
import { useSearchParams } from 'next/navigation';
import ConfigModal, { type EquipmentInfo } from '../JYLLM/components/ConfigModal';
import { isGenericEnvironmentName } from '@/lib/resolveProjectEnvironment';
import { useTypewriterReveal } from '../JYLLM/hooks/useTypewriterReveal';
import JygzSchemePanel from './components/JygzSchemePanel';
import { convertEmbeddedHtmlToMarkdown } from './lib/embedHtmlToMarkdown';
import { buildIndicatorForest, buildProjectForest, type ProjectTreeNode } from '../lib/projectTree';
import 'katex/dist/katex.min.css';
import { InlineMath, BlockMath } from 'react-katex';

const { Title, Text } = Typography;
const { TextArea } = Input;

interface EnvSettingsItem {
  env_name: string;
  type: string;
  class: string;
  limit: string;
  request: string;
  combination: string;
}

interface EnvironmentData {
  id: string;
  name: string;
  type: string;
  class: string;
}

interface EnvironmentConfig {
  env_id: string;
  env_name: string;
}

interface EnvSettingState {
  type: string;
  class: string;
  limit: string;
  request: string;
  combination: string;
}

/** 默认任务来源（编制依据旁字段） */
const DEFAULT_TASK_SOURCE = '依据项目研制任务，开展环境适应性试验';

/** 从已选方法关联的文件节点名称收集「编制依据」多行文本（不含方法 source_id/source_name） */
function collectStandardBasisLines(
  methodsData: Array<{
    id: string;
    files?: Array<{ name?: string; minio_id?: unknown }>;
  }>,
  selectedIds: Set<string>,
): string {
  const lines: string[] = [];
  const seen = new Set<string>();
  const add = (s: string) => {
    const t = String(s || '').trim();
    if (!t || seen.has(t)) return;
    seen.add(t);
    lines.push(t);
  };
  for (const m of methodsData) {
    if (!selectedIds.has(String(m.id))) continue;
    const files = m.files;
    if (Array.isArray(files)) {
      for (const f of files) {
        add(f?.name || '');
      }
    }
  }
  return lines.join('\n');
}

/** 保存到配置、随 render 提交的「方法」载荷（与项目/指标行绑定） */
interface MethodPayload {
  id: string;
  name: string;
  SYTJ: string;
  SYGC: string;
  JGPD: string;
  /** 方法正文（图库 SYNR），与 Jinja 模板 method.SYNR 一致 */
  SYNR?: string;
  synr?: string;
  description: string;
  imageurls?: string | string[];
  image_urls?: string | string[];
  files?: Array<{ name?: string; minio_id?: unknown }>;
  relatedProjects?: Array<{ id: string; name: string }>;
  relatedIndicators?: Array<{ id: string; name: string }>;
}

import { collectMethodImageUrls } from '@/lib/methodImageUrls';

function buildMethodPayloadFromApiNode(m: Record<string, unknown>): MethodPayload {
  const rpRaw = Array.isArray(m.relatedProjects)
    ? m.relatedProjects
    : Array.isArray(m.related_projects)
      ? m.related_projects
      : [];
  const riRaw = Array.isArray(m.relatedIndicators)
    ? m.relatedIndicators
    : Array.isArray(m.related_indicators)
      ? m.related_indicators
      : [];
  const rp = rpRaw as Array<{ id: string; name?: string }>;
  const ri = riRaw as Array<{ id: string; name?: string }>;
  const synr = String(m.SYNR ?? m.synr ?? m.Sy ?? '');
  const filesRaw = Array.isArray(m.files) ? (m.files as Array<Record<string, unknown>>) : undefined;
  const files = filesRaw
    ? filesRaw.map((f) => ({
        name: String(f?.name ?? '').trim(),
        minio_id: f.minio_id ?? f.minIo_id,
      })).filter((f) => f.name || f.minio_id)
    : undefined;
  const imageurls = collectMethodImageUrls(
    synr,
    filesRaw,
    (m.imageurls ?? m.image_urls ?? m.ImageUrls) as string | string[] | undefined,
  );
  return {
    id: String(m.id ?? m.methodId ?? '').trim(),
    name: String(m.name ?? m.methodName ?? ''),
    SYTJ: String(m.SYTJ ?? m.sytj ?? ''),
    SYGC: String(m.SYGC ?? m.sygc ?? ''),
    JGPD: String(m.JGPD ?? m.jgpd ?? ''),
    SYNR: synr,
    imageurls: imageurls.length > 0 ? imageurls : undefined,
    description: String(m.description ?? ''),
    files,
    relatedProjects: rp.map((x) => ({ id: String(x.id), name: String(x.name ?? '') })),
    relatedIndicators: ri.map((x) => ({ id: String(x.id), name: String(x.name ?? '') })),
  };
}

/**
 * 按图库返回的 linkedProjectIds / linkedIndicatorIds 写入「展示行 id → 方法列表」。
 * 展示行 id 为 projects_data[].id（含分类行）；匹配时用该行 id ∪ link_ids 与图库项目 Id 求交。
 */
function buildMethodsByProjectAndIndicator(
  methodNodes: Record<string, unknown>[],
  selectedMethodIds: string[],
  projectsData: Array<{ id: string; name: string; link_ids?: string[] }>,
  indicatorsData: Array<{ id: string; name: string }>,
): { methods_by_project: Record<string, MethodPayload[]>; methods_by_indicator: Record<string, MethodPayload[]> } {
  const methods_by_project: Record<string, MethodPayload[]> = {};
  const methods_by_indicator: Record<string, MethodPayload[]> = {};
  const selected = new Set(selectedMethodIds.map(String));

  for (const raw of methodNodes) {
    const m = raw as Record<string, unknown>;
    const mid = String(m.id ?? m.methodId ?? '').trim();
    if (!mid || !selected.has(mid)) continue;

    const row = buildMethodPayloadFromApiNode(m);
    const lp = (
      Array.isArray(m.linkedProjectIds)
        ? m.linkedProjectIds
        : Array.isArray(m.linked_project_ids)
          ? m.linked_project_ids
          : []
    ).map(String);
    const li = (
      Array.isArray(m.linkedIndicatorIds)
        ? m.linkedIndicatorIds
        : Array.isArray(m.linked_indicator_ids)
          ? m.linked_indicator_ids
          : []
    ).map(String);
    const rpIds = (Array.isArray(m.relatedProjects) ? (m.relatedProjects as Array<{ id: string }>) : []).map((x) =>
      String(x.id),
    );
    const riIds = (Array.isArray(m.relatedIndicators) ? (m.relatedIndicators as Array<{ id: string }>) : []).map((x) =>
      String(x.id),
    );
    const lpIds = new Set([...lp, ...rpIds]);
    const liIds = new Set([...li, ...riIds]);

    for (const proj of projectsData) {
      const candidateIds = new Set([proj.id, ...(proj.link_ids || [])].map(String));
      let hit = Array.from(lpIds).some((id) => candidateIds.has(id));
      if (!hit && lpIds.size === 0 && proj.name) {
        const pn = proj.name;
        const label = String(m.label ?? '');
        const desc = row.description;
        hit =
          !!(row.name && row.name.includes(pn)) ||
          !!(label && label.includes(pn)) ||
          !!(desc && desc.includes(pn));
      }
      if (hit) {
        if (!methods_by_project[proj.id]) methods_by_project[proj.id] = [];
        if (!methods_by_project[proj.id].some((x) => x.id === row.id)) methods_by_project[proj.id].push({ ...row });
      }
    }

    for (const ind of indicatorsData) {
      let hit = liIds.has(ind.id);
      if (!hit && liIds.size === 0 && ind.name) {
        const iname = ind.name;
        const label = String(m.label ?? '');
        const desc = row.description;
        hit =
          !!(row.name && row.name.includes(iname)) ||
          !!(label && label.includes(iname)) ||
          !!(desc && desc.includes(iname));
      }
      if (hit) {
        if (!methods_by_indicator[ind.id]) methods_by_indicator[ind.id] = [];
        if (!methods_by_indicator[ind.id].some((x) => x.id === row.id)) methods_by_indicator[ind.id].push({ ...row });
      }
    }
  }

  return { methods_by_project, methods_by_indicator };
}

interface SchemeConfig {
  equipment_id: string;
  equipment_name: string;
  equipment_list: EquipmentInfo[];
  doc_number: string;
  task_source: string;
  basis_standard: string;
  environments: EnvironmentConfig[];
  selected_projects: string[];
  selected_indicators: string[];
  selected_methods: string[];
  projects_data: Array<{
    id: string;
    name: string;
    description: string;
    link_ids?: string[];
    env_name?: string;
    type?: string;
    children?: Array<{ id: string; name: string; description?: string; type?: string }>;
  }>;
  indicators_data: Array<{id: string; name: string; description: string; type: string}>;
  methods_data: Array<{
    type: string;
    JGPD: string;
    SYGC: string;
    name: string;
    SYTJ: string;
    SYNR?: string;
    synr?: string;
    id: string;
    description: string;
    sy: string;
    label?: string;
    linkedProjectIds?: string[];
    linkedIndicatorIds?: string[];
    relatedProjects?: Array<{ id: string; name: string }>;
    relatedIndicators?: Array<{ id: string; name: string }>;
    files?: Array<{ name?: string; minio_id?: unknown }>;
  }>;
  env_settings_map: Record<string, EnvSettingState>;
  project_env_map: Record<string, string[]>;
  /** 展示行项目 id → 已选方法（保存基础配置时写入，render 优先使用） */
  methods_by_project: Record<string, MethodPayload[]>;
  /** 指标 id → 已选方法 */
  methods_by_indicator: Record<string, MethodPayload[]>;
}

/** 带 markdown 的导出（与 /export/pdf、/export/word 优先分支一致） */
interface JygzMarkdownExportPayload {
  markdown: string;
  title?: string;
  equipment_name?: string;
  doc_number?: string;
}

interface RenderPayload {
  equipment_name: string;
  doc_number: string;
  task_source: string;
  basis_standard: string;
  methods: Array<{
    id?: string;
    name: string;
    SYTJ: string;
    SYGC: string;
    JGPD: string;
    description: string;
    type: string;
    related_projects?: Array<{ id: string; name: string }>;
    related_indicators?: Array<{ id: string; name: string }>;
  }>;
  environments: Array<{
    env_name: string;
    settings: { type: string; class: string; limit: string; request: string; combination: string };
    projects: Array<{
      id?: string;
      name: string;
      description?: string;
      methods: Array<{
        id?: string;
        name: string;
        SYTJ: string;
        SYGC: string;
        JGPD: string;
        parent_project_id?: string;
        parent_project_name?: string;
      }>;
      children?: Array<{
        id?: string;
        name: string;
        SYTJ: string;
        SYGC: string;
        JGPD: string;
        parent_project_id?: string;
        parent_project_name?: string;
      }>;
    }>;
  }>;
  indicators: Array<{
    id?: string;
    name: string;
    description: string;
    methods: Array<{
      id?: string;
      name: string;
      SYTJ: string;
      SYGC: string;
      JGPD: string;
      parent_indicator_id?: string;
      parent_indicator_name?: string;
    }>;
    children?: Array<{
      id?: string;
      name: string;
      SYTJ: string;
      SYGC: string;
      JGPD: string;
      parent_indicator_id?: string;
      parent_indicator_name?: string;
    }>;
  }>;
}

interface SavedConfig {
  name: string;
  created_at: string;
  updated_at: string;
}

interface SchemeStore {
  config: SchemeConfig;
  renderedMarkdown: string;
  loading: boolean;
  updateConfig: (updates: Partial<SchemeConfig>) => void;
  setConfig: (config: SchemeConfig) => void;
  setRenderedMarkdown: (markdown: string) => void;
  setLoading: (loading: boolean) => void;
  resetConfig: () => void;
}

const initialConfig: SchemeConfig = {
  equipment_id: '',
  equipment_name: '',
  equipment_list: [],
  doc_number: '',
  task_source: DEFAULT_TASK_SOURCE,
  basis_standard: '',
  environments: [],
  selected_projects: [],
  selected_indicators: [],
  selected_methods: [],
  projects_data: [],
  indicators_data: [],
  methods_data: [],
  env_settings_map: {},
  project_env_map: {},
  methods_by_project: {},
  methods_by_indicator: {},
};

function mergeLoadedSchemeConfig(d: Partial<SchemeConfig> | SchemeConfig): SchemeConfig {
  const merged: SchemeConfig = {
    ...initialConfig,
    ...d,
    // 显式转换以确保类型兼容，防止后端返回数据结构微小差异导致的状态更新失败
    methods_by_project: (d.methods_by_project as SchemeConfig['methods_by_project']) ?? {},
    methods_by_indicator: (d.methods_by_indicator as SchemeConfig['methods_by_indicator']) ?? {},
    project_env_map: (d.project_env_map as SchemeConfig['project_env_map']) ?? {},
    env_settings_map: (d.env_settings_map as SchemeConfig['env_settings_map']) ?? {},
  };
  if (!String(merged.task_source || '').trim()) {
    merged.task_source = DEFAULT_TASK_SOURCE;
  }
  return merged;
}

const useSchemeStore = create<SchemeStore>((set) => ({
  config: { ...initialConfig }, renderedMarkdown: '', loading: false,
  updateConfig: (updates) => set((state) => ({ config: { ...state.config, ...updates } })),
  setConfig: (config) => set({ config }),
  setRenderedMarkdown: (markdown) => set({ renderedMarkdown: markdown }),
  setLoading: (loading) => set({ loading }),
  resetConfig: () => set({ config: { ...initialConfig }, renderedMarkdown: '' }),
}));

const API_BASE = '/api/fzsj/jygz';

async function apiRequest<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, { ...options, headers: { 'Content-Type': 'application/json', ...options?.headers } });
  const result = await response.json();
  if (!result.success) throw new Error(result.error || '请求失败');
  return result.data;
}

const schemeApi = {
  getEquipmentTree: () => apiRequest<any[]>('/equipment-tree'),
  getEnvironments: () => apiRequest<EnvironmentData[]>('/environments'),
  getEnvSettings: (envNames: string[]) => apiRequest<EnvSettingsItem[]>('/environment-settings', { method: 'POST', body: JSON.stringify({ env_names: envNames }) }),
  render: (payload: RenderPayload) => apiRequest<string>('/render', { method: 'POST', body: JSON.stringify(payload) }),
  exportPdf: (payload: JygzMarkdownExportPayload) =>
    fetch(`${API_BASE}/export/pdf`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) }),
  exportWord: (payload: JygzMarkdownExportPayload) =>
    fetch(`${API_BASE}/export/word`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) }),
  saveConfig: (name: string, payload: SchemeConfig) => apiRequest('/config/save', { method: 'POST', body: JSON.stringify({ name, data: payload, user_id: 'admin' }) }),
  loadConfig: (name: string) => apiRequest<SchemeConfig>(`/config/load/${name}?user_id=admin`),
  listConfigs: () => apiRequest<SavedConfig[]>('/config/list?user_id=admin'),
  deleteConfig: (name: string) => apiRequest(`/config/delete/${name}?user_id=admin`, { method: 'DELETE' }),
  /** 基于规则页：POST /methods，返回含 relatedProjects / relatedIndicators */
  postMethodsBatch: (params: { projectIds: string[]; indicatorIds: string[] }) =>
    apiRequest<unknown[]>('/methods', {
      method: 'POST',
      body: JSON.stringify({ projectIds: params.projectIds, indicatorIds: params.indicatorIds }),
    }),
  saveJygzHistory: (body: {
    scheme_name: string;
    scheme_description?: string;
    config: SchemeConfig;
    editing_env_settings: Record<string, EnvSettingState>;
    env_settings_data: EnvSettingsItem[];
    generated_content: string;
  }) =>
    apiRequest<{ task_id: string }>('/history/save', {
      method: 'POST',
      body: JSON.stringify({
        user_id: 'admin',
        scheme_name: body.scheme_name,
        scheme_description: body.scheme_description || undefined,
        generated_content: body.generated_content,
        word_count: (body.generated_content || '').length,
        config_snapshot: {
          config: body.config,
          editing_env_settings: body.editing_env_settings,
          env_settings_data: body.env_settings_data,
        },
        generation_status: 'complete',
      }),
    }),
  loadJygzHistory: async (taskId: string) => {
    const res = await fetch(`${API_BASE}/history/${encodeURIComponent(taskId)}?user_id=admin`);
    const r = (await res.json()) as { success?: boolean; data?: unknown; error?: string };
    if (res.ok && r.success && r.data) return r.data as JygzHistoryData;
    return null;
  },
  listJygzHistory: () => apiRequest<JygzHistoryListItem[]>('/history/list?user_id=admin'),
  deleteJygzHistory: async (taskId: string) => {
    const res = await fetch(`${API_BASE}/history/${encodeURIComponent(taskId)}?user_id=admin`, { method: 'DELETE' });
    const r = (await res.json()) as { success?: boolean; error?: string };
    if (!res.ok || !r.success) throw new Error(r.error || '删除失败');
  },
};

/** 后端 /history/<task_id> 返回的 data 形态 */
interface JygzHistoryData {
  task_id: string;
  config_snapshot: {
    config?: Partial<SchemeConfig> & Record<string, unknown>;
    editing_env_settings?: Record<string, EnvSettingState>;
    env_settings_data?: EnvSettingsItem[];
  } | null;
  generated_content: string;
}

interface JygzHistoryListItem {
  task_id: string;
  scheme_name: string;
  scheme_description?: string;
  word_count: number;
  generation_status: string;
  created_at: string;
  updated_at: string;
}

function MixedContent({ content }: { content: string }) {
  if (!content) return <Text type="secondary">无数据</Text>;

  const segments: Array<{ start: number; end: number; type: 'latex' }> = [];
  let regexMatch: RegExpExecArray | null;

  const blockLatexRe = /\$\$([\s\S]*?)\$\$/g;
  while ((regexMatch = blockLatexRe.exec(content)) !== null) {
    segments.push({ start: regexMatch.index, end: regexMatch.index + regexMatch[0].length, type: 'latex' });
  }

  const inlineLatexRe = /\$([^$\n]+?)\$/g;
  while ((regexMatch = inlineLatexRe.exec(content)) !== null) {
    if (!segments.some(s => regexMatch!.index >= s.start && regexMatch!.index < s.end)) {
      segments.push({ start: regexMatch.index, end: regexMatch.index + regexMatch[0].length, type: 'latex' });
    }
  }
  segments.sort((a, b) => a.start - b.start);

  if (segments.length === 0) {
    return <div className="mixed-content-html" dangerouslySetInnerHTML={{ __html: content }} />;
  }

  const parts: Array<{ type: 'html' | 'latex'; content: string }> = [];
  let pos = 0;
  for (const seg of segments) {
    if (seg.start > pos) {
      const text = content.slice(pos, seg.start).trim();
      if (text) parts.push({ type: 'html', content: text });
    }
    parts.push({ type: 'latex', content: content.slice(seg.start, seg.end) });
    pos = seg.end;
  }
  const tail = content.slice(pos).trim();
  if (tail) parts.push({ type: 'html', content: tail });

  return (
    <div style={{ fontSize: 14, lineHeight: 1.6 }}>
      {parts.map((part, idx) => {
        if (part.type === 'html') {
          const isBlock = /<(div|p|table|h[1-6]|ul|ol|li)/i.test(part.content);
          const isTable = /<table/i.test(part.content);
          // @ts-ignore
          const Tag = isBlock ? 'div' : 'span';
          return <Tag key={idx} className={isTable ? 'html-table-wrapper' : 'mixed-content-html'} dangerouslySetInnerHTML={{ __html: part.content }} style={isBlock ? { margin: '4px 0' } : {}} />;
        }
        if (part.type === 'latex') {
          const isBlock = part.content.startsWith('$$');
          const mathStr = isBlock ? part.content.slice(2, -2).trim() : part.content.slice(1, -1).trim();
          try {
            return isBlock 
              ? <div key={idx} style={{ margin: '8px 0', textAlign: 'center' }}><BlockMath math={mathStr} /></div>
              : <InlineMath key={idx} math={mathStr} />;
          } catch {
            return <span key={idx} style={{ color: '#ff4d4f', fontSize: 12 }}>{part.content}</span>;
          }
        }
      })}
    </div>
  );
}

const CATEGORY_CONFIG: { key: keyof EnvSettingState; label: string }[] = [
  { key: 'type', label: '分类' }, { key: 'class', label: '分级' }, { key: 'limit', label: '约束' },
  { key: 'combination', label: '组合' }, { key: 'request', label: '要求' },
];

// function formatRenderedMarkdown(markdown: string): string {
//   if (!markdown) return '';
//   let result = markdown;
  
//   // Convert HTML tables in the markdown to proper markdown table format
//   const tableRegex = /<table[^>]*>([\s\S]*?)<\/table>/gi;
//   result = result.replace(tableRegex, (match) => {
//     let tableHtml = match;
//     const rows: string[][] = [];
//     const rowRegex = /<tr[^>]*>([\s\S]*?)<\/tr>/gi;
//     let rowMatch: RegExpExecArray | null;
//     while ((rowMatch = rowRegex.exec(tableHtml)) !== null) {
//       const rowHtml = rowMatch[1];
//       const cells: string[] = [];
//       const cellRegex = /<(?:th|td)[^>]*>([\s\S]*?)<\/(?:th|td)>/gi;
//       let cellMatch: RegExpExecArray | null;
//       while ((cellMatch = cellRegex.exec(rowHtml)) !== null) {
//         let cellContent = cellMatch[1];
//         cellContent = cellContent.replace(/<[^>]+>/g, '').replace(/&nbsp;/g, ' ').replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&').trim();
//         cells.push(cellContent);
//       }
//       if (cells.length > 0) rows.push(cells);
//     }
//     if (rows.length === 0) return match;
    
//     let mdTable = '\n';
//     const maxCols = Math.max(...rows.map(r => r.length));
//     mdTable += '| ' + rows[0].map(c => c || '').join(' | ') + ' |\n';
//     mdTable += '| ' + rows[0].map(() => '---').join(' | ') + ' |\n';
//     for (let i = 1; i < rows.length; i++) {
//       const paddedRow = [...rows[i]];
//       while (paddedRow.length < maxCols) paddedRow.push('');
//       mdTable += '| ' + paddedRow.join(' | ') + ' |\n';
//     }
//     mdTable += '\n';
//     return mdTable;
//   });
  
//   // Convert HTML line breaks to markdown newlines
//   result = result.replace(/<br\s*\/?>/gi, '\n');
  
//   // Convert HTML paragraphs to markdown
//   result = result.replace(/<p[^>]*>([\s\S]*?)<\/p>/gi, '\n$1\n');
  
//   // Remove remaining HTML tags that don't affect formatting
//   result = result.replace(/<\/?(?:div|span)[^>]*>/gi, '');
  
//   // Decode common HTML entities
//   result = result.replace(/&nbsp;/g, ' ')
//                  .replace(/&lt;/g, '<')
//                  .replace(/&gt;/g, '>')
//                  .replace(/&amp;/g, '&')
//                  .replace(/&quot;/g, '"')
//                  .replace(/&#39;/g, "'");
  
//   // Clean up excessive newlines
//   result = result.replace(/\n{3,}/g, '\n\n').trim();
  
//   return result;
// }

/** 分类节点下的方法挂在子「试验项目」上：用子项目 Id 与 linkedProjectIds 对齐 */
function collectProjectLinkIds(nodeData: Record<string, unknown> | undefined, projectId: string): string[] {
  const children = nodeData?.children;
  if (Array.isArray(children) && children.length > 0) {
    const ids = children
      .map((c: unknown) => {
        const o = c as Record<string, unknown>;
        return String(o?.id ?? o?.Id ?? '').trim();
      })
      .filter(Boolean);
    if (ids.length > 0) return ids;
  }
  const sid = String(projectId || '').trim();
  return sid ? [sid] : [];
}

/** GFM：标题与表格之间需空行；行首缩进的 | 会被当成代码块原样显示 */
function normalizeMarkdownTablesForGfm(md: string): string {
  const s = md.replace(/\r\n/g, '\n').replace(/\r/g, '\n');
  const lines = s.split('\n');
  const out: string[] = [];
  for (let i = 0; i < lines.length; i++) {
    let line = lines[i];
    if (/^\s+\|/.test(line)) {
      line = line.replace(/^\s+/, '');
    }
    const prev = i > 0 ? lines[i - 1] : '';
    if (/^\|/.test(line) && prev && /^#{1,6}\s/.test(prev)) {
      out.push('');
    }
    out.push(line);
  }
  return out.join('\n');
}

/** 去掉以 | 开头的表格行之间的多余空行（与后端 _compact_gfm_table_row_gaps 一致） */
function compactMarkdownTableRowGaps(md: string): string {
  if (!md) return md;
  return md.replace(/(\|[^\n]+\|)\n\n+(?=\|)/g, '$1\n');
}

function formatRenderedMarkdown(markdown: string): string {
  if (!markdown) return '';
  
  let result = convertEmbeddedHtmlToMarkdown(markdown);
  result = normalizeMarkdownTablesForGfm(result);
  
  // 解码HTML实体，保留LaTeX数学分隔符
  result = result.replace(/&nbsp;/g, ' ')
                 .replace(/&lt;/g, '<')
                 .replace(/&gt;/g, '>')
                 .replace(/&amp;/g, '&')
                 .replace(/&quot;/g, '"')
                 .replace(/&#39;/g, "'")
                 .replace(/&mdash;/g, '—')
                 .replace(/&ndash;/g, '–')
                 .replace(/&laquo;/g, '«')
                 .replace(/&raquo;/g, '»');
  
  // 转换HTML表格到markdown，保留单元格内容中的LaTeX
  const tableRegex = /<table[^>]*>([\s\S]*?)<\/table>/gi;
  result = result.replace(tableRegex, (match) => {
    let tableHtml = match;
    const rows: string[][] = [];
    const rowRegex = /<tr[^>]*>([\s\S]*?)<\/tr>/gi;
    let rowMatch: RegExpExecArray | null;
    while ((rowMatch = rowRegex.exec(tableHtml)) !== null) {
      const rowHtml = rowMatch[1];
      const cells: string[] = [];
      const cellRegex = /<(?:th|td)[^>]*>([\s\S]*?)<\/(?:th|td)>/gi;
      let cellMatch: RegExpExecArray | null;
      while ((cellMatch = cellRegex.exec(rowHtml)) !== null) {
        let cellContent = cellMatch[1];
        cellContent = cellContent.replace(/&nbsp;/g, ' ')
                                .replace(/&lt;/g, '<')
                                .replace(/&gt;/g, '>')
                                .replace(/&amp;/g, '&')
                                .replace(/&quot;/g, '"')
                                .replace(/&#39;/g, "'")
                                .trim();
        cells.push(cellContent);
      }
      if (cells.length > 0) rows.push(cells);
    }
    if (rows.length === 0) return match;
    
    let mdTable = '\n';
    const maxCols = Math.max(...rows.map(r => r.length));
    mdTable += '| ' + rows[0].map(c => c || '').join(' | ') + ' |\n';
    mdTable += '| ' + rows[0].map(() => '---').join(' | ') + ' |\n';
    for (let i = 1; i < rows.length; i++) {
      const paddedRow = [...rows[i]];
      while (paddedRow.length < maxCols) paddedRow.push('');
      mdTable += '| ' + paddedRow.join(' | ') + ' |\n';
    }
    mdTable += '\n';
    return mdTable;
  });
  
  result = convertEmbeddedHtmlToMarkdown(result);

  // 清理多余的换行符
  result = result.replace(/\n{3,}/g, '\n\n').trim();
  result = compactMarkdownTableRowGaps(result);

  return result;
}

function JYGZPageContent() {
  const searchParams = useSearchParams();
  const taskId = searchParams.get('task_id') || '';
  const { config, renderedMarkdown, loading, updateConfig, setConfig, setRenderedMarkdown, setLoading } = useSchemeStore();
  const [equipmentTree, setEquipmentTree] = useState<any[]>([]);
  const [allEnvironments, setAllEnvironments] = useState<EnvironmentData[]>([]);
  const [savedConfigs, setSavedConfigs] = useState<SavedConfig[]>([]);
  const [dataLoading, setDataLoading] = useState(true);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [basicInfoCollapsed, setBasicInfoCollapsed] = useState(false);
  const [envSettingsData, setEnvSettingsData] = useState<EnvSettingsItem[]>([]);
  const [envConfigModalVisible, setEnvConfigModalVisible] = useState(false);
  const [editingEnvSettings, setEditingEnvSettings] = useState<Record<string, EnvSettingState>>({});
  const [configModalOpen, setConfigModalOpen] = useState(false);
  const [formattedMarkdown, setFormattedMarkdown] = useState('');
  const [schemePreviewMode, setSchemePreviewMode] = useState<'preview' | 'edit'>('preview');
  const [previewRevealActive, setPreviewRevealActive] = useState(false);
  const [previewStreamTarget, setPreviewStreamTarget] = useState('');
  const typewriterShown = useTypewriterReveal(previewStreamTarget, previewRevealActive && schemePreviewMode === 'preview', {
    maxStep: 96,
    intervalMs: 6,
  });
  const [exportingKind, setExportingKind] = useState<SchemeExportFormat | null>(null);
  const [syncingToKm, setSyncingToKm] = useState(false);
  const [kmSyncModalOpen, setKmSyncModalOpen] = useState(false);
  const [kmSyncFile, setKmSyncFile] = useState<File | null>(null);
  const [kmSyncMeta, setKmSyncMeta] = useState<UploadFileMeta | null>(null);
  const [saveSchemeModalOpen, setSaveSchemeModalOpen] = useState(false);
  const [saveSchemeName, setSaveSchemeName] = useState('');
  const [saveSchemeDescription, setSaveSchemeDescription] = useState('');
  const [savingScheme, setSavingScheme] = useState(false);
  const [jygzHistoryList, setJygzHistoryList] = useState<JygzHistoryListItem[]>([]);
  const [jygzHistoryDeleting, setJygzHistoryDeleting] = useState<string | null>(null);

  const loadHistoryFromUrl = useMemo(() => searchParams.get('load_history') === 'true', [searchParams]);

  const fetchEnvSettings = useCallback(async (envNames: string[]) => {
    /* 环境配置步骤已下线：原从 Neo4j 拉取环境 Type/Class 等并填充编辑态的逻辑保留备恢复
    if (envNames.length === 0) return;
    try {
      const settings = await schemeApi.getEnvSettings(envNames);
      setEnvSettingsData(settings || []);
      const initial: Record<string, EnvSettingState> = {};
      for (const s of settings || []) {
        initial[s.env_name] = { type: s.type, class: s.class, limit: s.limit, request: s.request, combination: s.combination };
      }
      setEditingEnvSettings(initial);
    } catch {
      message.error('获取环境设置失败');
    }
    */
    void envNames;
  }, []);

  const fetchJygzMethods = useCallback(
    (params: { projectIds: string[]; indicatorIds: string[] }) =>
      schemeApi.postMethodsBatch(params) as Promise<unknown[]>,
    [],
  );

  const applyJygzHistoryData = useCallback(
    (h: JygzHistoryData) => {
      const snap = h.config_snapshot || {};
      if (snap.config) {
        setConfig(mergeLoadedSchemeConfig(snap.config as SchemeConfig));
      }
      if (snap.editing_env_settings && typeof snap.editing_env_settings === 'object') {
        setEditingEnvSettings(snap.editing_env_settings);
      }
      if (Array.isArray(snap.env_settings_data) && snap.env_settings_data.length > 0) {
        setEnvSettingsData(snap.env_settings_data);
      }
      /* else {
        const envs = (snap.config as SchemeConfig | undefined)?.environments;
        if (envs?.length) void fetchEnvSettings(envs.map((e) => e.env_name));
      } */
      if (h.generated_content) {
        setRenderedMarkdown(h.generated_content);
        const f = formatRenderedMarkdown(h.generated_content);
        setFormattedMarkdown(f);
        setPreviewStreamTarget(f);
        setPreviewRevealActive(false);
        setSchemePreviewMode('preview');
      }
    },
    [setConfig, setRenderedMarkdown, setFormattedMarkdown, fetchEnvSettings],
  );

  const refreshJygzHistoryList = useCallback(async () => {
    try {
      const list = await schemeApi.listJygzHistory();
      setJygzHistoryList(list || []);
    } catch {
      setJygzHistoryList([]);
    }
  }, []);

  useEffect(() => {
    const loadInitData = async () => {
      setDataLoading(true);
      try {
        const [tree, envs, configs, hist] = await Promise.all([
          schemeApi.getEquipmentTree().catch(() => []),
          schemeApi.getEnvironments().catch(() => []),
          schemeApi.listConfigs().catch(() => []),
          schemeApi.listJygzHistory().catch(() => [] as JygzHistoryListItem[]),
        ]);
        setEquipmentTree(tree || []);
        setAllEnvironments(envs || []);
        setSavedConfigs(configs || []);
        setJygzHistoryList(hist || []);
      } catch (err) { console.error('初始化数据加载失败:', err); } finally { setDataLoading(false); }
    };
    loadInitData();
  }, []);

  useEffect(() => {
    if (!taskId) return;
    (async () => {
      setLoading(true);
      try {
        if (loadHistoryFromUrl) {
          const h = await schemeApi.loadJygzHistory(taskId);
          if (h) {
            applyJygzHistoryData(h);
            message.success('已加载历史方案');
            return;
          }
        }
        const d = await schemeApi.loadConfig(taskId);
        setConfig(mergeLoadedSchemeConfig(d));
        message.success('已加载方案配置');
      } catch {
        message.error('加载方案失败');
      } finally {
        setLoading(false);
      }
    })();
  }, [taskId, loadHistoryFromUrl, setConfig, setLoading, applyJygzHistoryData]);

    const handleConfigModalSave = async (data: { selectedEquipmentKeys: string[]; selectedEquipmentInfo: any[]; selectedProjects: any[]; selectedIndicators: any[]; selectedMethodIds: string[]; envNames: string[]; selectedMethodNodes?: any[]; tableRows?: any[]; projectEnvironmentById?: Record<string, string> }) => {
    setConfigModalOpen(false);
    
    let equipmentList = data.selectedEquipmentInfo || [];
    let firstEqName = equipmentList.length > 0 ? equipmentList[0].name : '';
    let allEqNames = equipmentList.length > 0 ? equipmentList.map(eq => eq.name).join('、') : '';

    if (equipmentList.length === 0 && data.tableRows && data.tableRows.length > 0) {
      const eqNames = data.tableRows.map((row: any) => row.equipmentTypeName).filter(Boolean);
      allEqNames = eqNames.join('、');
      firstEqName = eqNames[0] || '';
      equipmentList = data.tableRows.map((row: any) => ({
        key: `eq_${row.equipmentTypeId}`,
        id: row.equipmentTypeId,
        name: row.equipmentTypeName,
        equipmentTypeId: '',
        equipmentTypeName: row.equipmentTypeName,
        categoryId: '',
        categoryName: '',
      }));
    }

    let equipmentName = firstEqName || allEqNames;
    const eqKeys = data.selectedEquipmentKeys.filter((k: string) => k.startsWith('eq_'));
    const firstEqId = eqKeys.length > 0 ? eqKeys[0].replace('eq_', '') : '';
    if (!equipmentName && firstEqId) equipmentName = `设备${firstEqId}`;
    if (!equipmentName) equipmentName = '未知设备';

    const projectEnvironmentById = data.projectEnvironmentById || {};
    const projectEnvNames = new Set<string>();
    Object.values(projectEnvironmentById).forEach((n) => {
      if (n && !isGenericEnvironmentName(n)) projectEnvNames.add(n);
    });
    if (data.tableRows && data.tableRows.length > 0) {
      data.tableRows.forEach((row: any) => {
        Object.entries(row.projectsByEnv || {}).forEach(([envName, projects]: [string, any]) => {
          const arr = Array.isArray(projects) ? projects : [];
          if (isGenericEnvironmentName(envName)) return;
          if (arr.some((proj: any) => data.selectedProjects.some((p: any) => p.projectId === proj.id))) {
            projectEnvNames.add(envName);
          }
        });
      });
    }

    const newEnvs: EnvironmentConfig[] = [];
    const uniqueEnvNames = Array.from(projectEnvNames);
    for (let i = 0; i < uniqueEnvNames.length; i++) {
      const envName = uniqueEnvNames[i];
      const m = allEnvironments.find((e) => e.name === envName);
      if (m) newEnvs.push({ env_id: m.id, env_name: m.name });
      else newEnvs.push({ env_id: `user_env:${envName}`, env_name: envName });
    }

    const methodNodes = data.selectedMethodNodes || [];
    const selectedMethodIdSet = new Set(data.selectedMethodIds.map(String));
    const methodsData = methodNodes.map((m: any) => {
      const base = buildMethodPayloadFromApiNode(m as Record<string, unknown>);
      return {
        id: base.id,
        name: base.name,
        description: base.description,
        type: m.type || '',
        label: m.label || '',
        sy: m.sy || '',
        SYTJ: base.SYTJ,
        SYGC: base.SYGC,
        JGPD: base.JGPD,
        SYNR: base.SYNR,
        imageurls: base.imageurls ?? base.image_urls,
        files: base.files,
        linkedProjectIds: Array.isArray(m.linkedProjectIds)
          ? m.linkedProjectIds.map(String)
          : Array.isArray(m.linked_project_ids)
            ? m.linked_project_ids.map(String)
            : [],
        linkedIndicatorIds: Array.isArray(m.linkedIndicatorIds)
          ? m.linkedIndicatorIds.map(String)
          : Array.isArray(m.linked_indicator_ids)
            ? m.linked_indicator_ids.map(String)
            : [],
        relatedProjects: base.relatedProjects,
        relatedIndicators: base.relatedIndicators,
      };
    });
    const projectsData = data.selectedProjects.map((p: any) => {
      const pid = String(p.projectId || p.id || '').trim();
      const nd = (p.nodeData || {}) as Record<string, unknown>;
      const fromMap = projectEnvironmentById[pid] || '';
      const fromNode = (nd._env as string) || (nd.env_name as string) || '';
      const envName = fromMap || String(fromNode).split('、')[0].trim();
      const childNodes = Array.isArray(nd.children) ? nd.children : [];
      return {
        id: pid,
        link_ids: collectProjectLinkIds(p.nodeData as Record<string, unknown> | undefined, pid),
        name: (nd.name as string) || p.projectName || p.name || '',
        description: (nd.description as string) || p.description || '',
        env_name: envName && !isGenericEnvironmentName(envName) ? envName : '',
        type: (nd.type as string) || (nd.Type as string) || undefined,
        children: childNodes
          .filter((ch: any) => ch && typeof ch === 'object')
          .map((ch: any) => ({
            id: String(ch.id || '').trim(),
            name: String(ch.name || ''),
            description: String(ch.description || ''),
            type: String(ch.type || ch.Type || 'test'),
          })),
      };
    });

    const indicatorsData = data.selectedIndicators.map((i: any) => ({
      id: String(i.indicatorId || i.id || '').trim(),
      name: (i.nodeData && (i.nodeData as any).name) || i.indicatorName || i.name || '',
      description: (i.nodeData && (i.nodeData as any).description) || i.description || '',
      type: (i.nodeData && (i.nodeData as any).type) || i.type || '',
    }));

    const projectEnvMap: Record<string, string[]> = {};
    if (data.tableRows && data.tableRows.length > 0) {
      data.tableRows.forEach((row: any) => {
        Object.entries(row.projectsByEnv || {}).forEach(([envName, projects]: [string, any]) => {
          projects.forEach((proj: any) => {
            const projId = String(proj.id || proj.projectId || '').trim();
            if (!projId) return;
            if (!projectEnvMap[projId]) {
              projectEnvMap[projId] = [];
            }
            if (!projectEnvMap[projId].includes(envName)) {
              projectEnvMap[projId].push(envName);
            }
          });
        });
      });
    }

    const { methods_by_project, methods_by_indicator } = buildMethodsByProjectAndIndicator(
      methodNodes as Record<string, unknown>[],
      data.selectedMethodIds,
      projectsData,
      indicatorsData,
    );

    const basisFromMethods = collectStandardBasisLines(methodsData, selectedMethodIdSet);
    const manualBasis = (config.basis_standard || '').trim();
    let nextBasis = '待补充';
    if (basisFromMethods.trim() && manualBasis) nextBasis = `${basisFromMethods.trim()}\n${manualBasis}`;
    else if (basisFromMethods.trim()) nextBasis = basisFromMethods.trim();
    else if (manualBasis) nextBasis = manualBasis;

    const nextTask = (config.task_source && config.task_source.trim()) ? config.task_source : DEFAULT_TASK_SOURCE;

    updateConfig({ 
      equipment_id: firstEqId, 
      equipment_name: equipmentName, 
      equipment_list: equipmentList,
      environments: newEnvs, 
      selected_projects: projectsData.map(p => p.id), 
      selected_indicators: indicatorsData.map(i => i.id), 
      selected_methods: data.selectedMethodIds,
      projects_data: projectsData,
      indicators_data: indicatorsData,
      methods_data: methodsData,
      project_env_map: projectEnvMap,
      methods_by_project,
      methods_by_indicator,
      basis_standard: nextBasis,
      task_source: nextTask,
    });
    message.success('已通过基础配置更新项目、指标和方法');
    // if (newEnvs.length > 0) await fetchEnvSettings(newEnvs.map(e => e.env_name));
  };

  const handleRender = async () => {
    if (!config.equipment_name) { message.warning('请先点击基础配置选择设备'); return; }
    if (config.selected_methods.length === 0) { message.warning('请先选择方法'); return; }
    try {
      setLoading(true);
      setPreviewRevealActive(false);

      const selectedMethodSet = new Set(config.selected_methods.map(String));
      const allMethods = config.methods_data
        .filter((m) => selectedMethodSet.has(String(m.id)))
        .sort((a, b) => a.name.localeCompare(b.name));

      const normalizedMethods = allMethods.map(m => ({
        ...m,
        SYTJ: m.SYTJ || '',
        SYGC: m.SYGC || '',
        JGPD: m.JGPD || '',
        SYNR: m.SYNR || (m as { synr?: string }).synr || '',
        linkedProjectIds: (m.linkedProjectIds || []).map(String),
        linkedIndicatorIds: (m.linkedIndicatorIds || []).map(String),
      }));

      const methodBelongsToProject = (m: (typeof normalizedMethods)[0], projectId: string) => {
        const linked = m.linkedProjectIds || [];
        const proj = config.projects_data.find((p) => String(p.id) === String(projectId));
        const linkIds =
          proj?.link_ids && proj.link_ids.length > 0
            ? proj.link_ids.map(String)
            : [String(projectId)];
        const relP = m.relatedProjects;
        if (Array.isArray(relP) && relP.length > 0) {
          if (relP.some((r) => linkIds.includes(String(r.id)))) return true;
        }
        if (linked.length > 0) {
          return linkIds.some((lid) => linked.includes(lid));
        }
        const projName = proj?.name || '';
        if (!projName) return false;
        return (
          (m.name && m.name.includes(projName)) ||
          (m.label && m.label.includes(projName)) ||
          (m.description && m.description.includes(projName))
        );
      };

      const methodBelongsToIndicator = (m: (typeof normalizedMethods)[0], indId: string, indName: string) => {
        const linked = m.linkedIndicatorIds || [];
        const iid = String(indId);
        const relI = m.relatedIndicators;
        if (Array.isArray(relI) && relI.length > 0 && relI.some((r) => String(r.id) === iid)) return true;
        if (linked.length > 0) return linked.includes(iid);
        return (
          (m.label && indName && m.label.includes(indName)) ||
          (m.description && indName && m.description.includes(indName)) ||
          (m.name && indName && m.name.includes(indName))
        );
      };

      const byProj = config.methods_by_project ?? {};
      const byInd = config.methods_by_indicator ?? {};
      const selectedProjectSet = new Set(config.selected_projects.map(String));
      const selectedIndicatorSet = new Set(config.selected_indicators.map(String));
      const methodSynr = (row: MethodPayload) => String(row.SYNR ?? row.synr ?? '');

      const methodPayloadToNestedProject = (row: MethodPayload, proj: { id: string; name: string }) => ({
        id: row.id,
        name: row.name,
        SYTJ: row.SYTJ,
        SYGC: row.SYGC,
        JGPD: row.JGPD,
        SYNR: methodSynr(row),
        imageurls: row.imageurls ?? row.image_urls,
        parent_project_id: proj.id,
        parent_project_name: proj.name,
        relatedProjects:
          row.relatedProjects && row.relatedProjects.length > 0
            ? row.relatedProjects
            : [{ id: proj.id, name: proj.name }],
        relatedIndicators: row.relatedIndicators && row.relatedIndicators.length > 0 ? row.relatedIndicators : [],
      });

      const methodPayloadToNestedIndicator = (row: MethodPayload, ind: { id: string; name: string }) => ({
        id: row.id,
        name: row.name,
        SYTJ: row.SYTJ,
        SYGC: row.SYGC,
        JGPD: row.JGPD,
        SYNR: methodSynr(row),
        imageurls: row.imageurls ?? row.image_urls,
        parent_indicator_id: ind.id,
        parent_indicator_name: ind.name,
        relatedProjects: row.relatedProjects && row.relatedProjects.length > 0 ? row.relatedProjects : [],
        relatedIndicators:
          row.relatedIndicators && row.relatedIndicators.length > 0
            ? row.relatedIndicators
            : [{ id: ind.id, name: ind.name }],
      });

      const methodsPayload = normalizedMethods.map((m) => {
        const rp =
          m.relatedProjects && m.relatedProjects.length > 0
            ? m.relatedProjects
            : config.projects_data.filter((p) => (byProj[p.id] || []).some((x) => x.id === m.id)).map((p) => ({ id: p.id, name: p.name }));
        const ri =
          m.relatedIndicators && m.relatedIndicators.length > 0
            ? m.relatedIndicators
            : config.indicators_data.filter((i) => (byInd[i.id] || []).some((x) => x.id === m.id)).map((i) => ({ id: i.id, name: i.name }));
        return {
          id: m.id,
          name: m.name,
          SYTJ: m.SYTJ,
          SYGC: m.SYGC,
          JGPD: m.JGPD,
          SYNR: m.SYNR || (m as { synr?: string }).synr || '',
          imageurls: (m as { imageurls?: string | string[] }).imageurls
            ?? (m as { image_urls?: string | string[] }).image_urls,
          description: m.description || '',
          type: m.type || '',
          relatedProjects: rp,
          relatedIndicators: ri,
          related_projects: rp,
          related_indicators: ri,
        };
      });

      const emptySettings = { type: '', class: '', limit: '', request: '', combination: '' };

      const baseProjectNodes: ProjectTreeNode[] = config.projects_data
        .filter((p) => selectedProjectSet.has(String(p.id)))
        .map((proj) => ({
          id: proj.id,
          name: proj.name,
          description: proj.description,
          link_ids: proj.link_ids,
          type: proj.type,
          children: (proj.children || []).map((ch) => ({
            id: ch.id,
            name: ch.name,
            description: ch.description,
            type: ch.type,
          })),
        }));

      const projectForest = buildProjectForest(baseProjectNodes);

      const attachMethodsToProject = (node: ProjectTreeNode): ProjectTreeNode => {
        const proj = { id: String(node.id), name: String(node.name || '') };
        let rows: MethodPayload[] = byProj[String(node.id)] || [];
        if (rows.length === 0) {
          rows = normalizedMethods
            .filter((m) => methodBelongsToProject(m, String(node.id)))
            .map((m) => ({
              id: m.id,
              name: m.name,
              SYTJ: m.SYTJ || '',
              SYGC: m.SYGC || '',
              JGPD: m.JGPD || '',
              SYNR: m.SYNR || (m as { synr?: string }).synr || '',
              description: m.description || '',
              relatedProjects: m.relatedProjects,
              relatedIndicators: m.relatedIndicators,
            }));
        }
        const methods = rows.map((r) => methodPayloadToNestedProject(r, proj));
        return {
          ...node,
          methods,
          children: ((node.children || []) as ProjectTreeNode[]).map(attachMethodsToProject),
        };
      };

      const projectsWithMethods = projectForest.map(attachMethodsToProject);

      const resolveProjectEnvName = (projId: string): string => {
        const p = config.projects_data.find((x) => String(x.id) === String(projId));
        const fromProj = (p?.env_name || '').trim();
        if (fromProj && !isGenericEnvironmentName(fromProj)) return fromProj;
        const fromMap = config.project_env_map[projId];
        if (Array.isArray(fromMap) && fromMap[0] && !isGenericEnvironmentName(fromMap[0])) {
          return fromMap[0];
        }
        return '';
      };

      const projectsByEnvName = new Map<string, ProjectTreeNode[]>();
      const collectEnvIds = (node: ProjectTreeNode): string[] => {
        const self = resolveProjectEnvName(String(node.id));
        const fromKids = ((node.children || []) as ProjectTreeNode[]).flatMap(collectEnvIds);
        return self ? [self, ...fromKids] : fromKids;
      };
      for (const root of projectsWithMethods) {
        const envCandidates = collectEnvIds(root);
        const envName = envCandidates.find((e) => e && !isGenericEnvironmentName(e)) || '';
        if (!envName) {
          message.warning(`项目「${root.name}」未能解析所属环境，已跳过环境分组`);
          continue;
        }
        if (!projectsByEnvName.has(envName)) projectsByEnvName.set(envName, []);
        projectsByEnvName.get(envName)!.push(root);
      }

      const envOrder =
        config.environments.length > 0
          ? config.environments.map((e) => e.env_name).filter((n) => !isGenericEnvironmentName(n))
          : Array.from(projectsByEnvName.keys()).sort((a, b) => a.localeCompare(b, 'zh-CN'));

      const envPayload = envOrder
        .map((env_name) => ({
          env_name,
          settings:
            config.env_settings_map[env_name] ||
            editingEnvSettings[env_name] ||
            emptySettings,
          projects: projectsByEnvName.get(env_name) || [],
        }))
        .filter((e) => e.projects.length > 0);

      if (envPayload.length === 0 && projectsWithMethods.length > 0) {
        message.error('所选项目均未匹配到有效环境名称，请重新进行基础配置');
        setLoading(false);
        return;
      }

      const baseIndicatorNodes: ProjectTreeNode[] = config.indicators_data
        .filter((ind) => selectedIndicatorSet.has(String(ind.id)))
        .map((ind) => ({
          id: ind.id,
          name: ind.name,
          description: ind.description,
          type: ind.type,
        }));

      const indicatorForest = buildIndicatorForest(baseIndicatorNodes);

      const attachMethodsToIndicator = (node: ProjectTreeNode): ProjectTreeNode => {
        const ind = { id: String(node.id), name: String(node.name || '') };
        let rows: MethodPayload[] = byInd[String(node.id)] || [];
        if (rows.length === 0) {
          rows = normalizedMethods
            .filter((m) => methodBelongsToIndicator(m, String(node.id), ind.name))
            .map((m) => ({
              id: m.id,
              name: m.name,
              SYTJ: m.SYTJ || '',
              SYGC: m.SYGC || '',
              JGPD: m.JGPD || '',
              SYNR: m.SYNR || (m as { synr?: string }).synr || '',
              description: m.description || '',
              relatedProjects: m.relatedProjects,
              relatedIndicators: m.relatedIndicators,
            }));
        }
        const methods = rows.map((r) => methodPayloadToNestedIndicator(r, ind));
        return {
          ...node,
          methods,
          children: ((node.children || []) as ProjectTreeNode[]).map(attachMethodsToIndicator),
        };
      };

      const indicatorsPayload = indicatorForest.map(attachMethodsToIndicator);

      const payload = {
          equipment_name: config.equipment_name,
          doc_number: config.doc_number,
          task_source: config.task_source,
          basis_standard: config.basis_standard,
          methods: methodsPayload,
          environments: envPayload,
          indicators: indicatorsPayload
      };

      const result = await schemeApi.render(payload as RenderPayload);
      const formatted = formatRenderedMarkdown(result);
      setRenderedMarkdown(result);
      setFormattedMarkdown(formatted);
      setPreviewStreamTarget(formatted);
      setPreviewRevealActive(false);
      setSchemePreviewMode('preview');
      requestAnimationFrame(() => {
        setPreviewRevealActive(true);
      });
      message.success('方案生成成功');
    } catch (err: any) { message.error(`渲染失败: ${err.message}`); } finally { setLoading(false); }
  };

  const markdownForExport = () =>
    compactMarkdownTableRowGaps((formattedMarkdown || renderedMarkdown).trim());

  const buildJygzMetaForFile = (format: SchemeExportFormat): UploadFileMeta => {
    const ext = format === 'word' ? 'docx' : format === 'pdf' ? 'pdf' : 'md';
    return buildJygzKmMeta({
      equipmentName: config.equipment_name,
      docNumber: config.doc_number,
      taskId: taskId || undefined,
      environments: config.environments,
      projects: config.projects_data?.map((p) => p.name) || [],
      indicators: config.indicators_data?.map((i) => i.name) || [],
      schemeName: (config.equipment_name || config.doc_number || '').trim() || undefined,
      fileType: ext,
    });
  };

  const openKmSyncModal = (blob: Blob, filename: string, format: SchemeExportFormat) => {
    setKmSyncFile(fileFromExport(blob, filename));
    setKmSyncMeta(buildJygzMetaForFile(format));
    setKmSyncModalOpen(true);
  };

  const promptSyncAfterExport = (blob: Blob, filename: string, format: SchemeExportFormat) => {
    Modal.confirm({
      title: '是否同步到知识管理？',
      content: '可将刚导出的文件入库至知识管理「试验方案」，需确认元数据后提交。',
      okText: '同步',
      cancelText: '仅下载',
      onOk: () => {
        openKmSyncModal(blob, filename, format);
      },
    });
  };

  const handleExport = async (format: SchemeExportFormat) => {
    const md = markdownForExport();
    if (!md) {
      message.warning('请先生成方案');
      return;
    }
    try {
      setExportingKind(format);
      const { blob, filename } = await exportJygzBlob(md, format, config.equipment_name, config.doc_number);
      downloadBlob(blob, filename);
      message.success(`${format.toUpperCase()} 导出成功`);
      promptSyncAfterExport(blob, filename, format);
    } catch (err: unknown) {
      message.error(err instanceof Error ? err.message : '导出失败');
    } finally {
      setExportingKind(null);
    }
  };

  const handleSyncToKm = async () => {
    const md = markdownForExport();
    if (!md) {
      message.warning('请先生成方案');
      return;
    }
    try {
      setSyncingToKm(true);
      const { blob, filename } = await exportJygzBlob(md, 'word', config.equipment_name, config.doc_number);
      downloadBlob(blob, filename);
      openKmSyncModal(blob, filename, 'word');
    } catch (err: unknown) {
      message.error(err instanceof Error ? err.message : 'Word 生成失败');
    } finally {
      setSyncingToKm(false);
    }
  };

  const handleOpenSaveScheme = () => {
    if (!renderedMarkdown?.trim()) {
      message.warning('请先生成方案');
      return;
    }
    setSaveSchemeName((config.equipment_name || config.doc_number || '').trim());
    setSaveSchemeDescription('');
    setSaveSchemeModalOpen(true);
  };

  const handleConfirmSaveScheme = async () => {
    const name = saveSchemeName.trim();
    if (!name) {
      message.warning('请输入方案名称');
      return;
    }
    if (!renderedMarkdown?.trim()) {
      message.warning('请先生成方案');
      return;
    }
    setSavingScheme(true);
    try {
      await schemeApi.saveJygzHistory({
        scheme_name: name,
        scheme_description: saveSchemeDescription.trim() || undefined,
        config: { ...config, env_settings_map: { ...config.env_settings_map, ...editingEnvSettings } },
        editing_env_settings: editingEnvSettings,
        env_settings_data: envSettingsData,
        generated_content: renderedMarkdown,
      });
      message.success('方案已保存到历史，可在首页最近方案中打开');
      setSaveSchemeModalOpen(false);
      void refreshJygzHistoryList();
    } catch (err: unknown) {
      message.error(err instanceof Error ? err.message : '保存失败');
    } finally {
      setSavingScheme(false);
    }
  };

  const handleLoadConfig = async (name: string) => {
    try { const d = await schemeApi.loadConfig(name); setConfig(mergeLoadedSchemeConfig(d)); message.success('配置加载成功'); } catch (err: any) { message.error(`加载失败: ${err.message}`); }
  };

  const handleLoadJygzHistoryItem = async (tid: string) => {
    setLoading(true);
    try {
      const h = await schemeApi.loadJygzHistory(tid);
      if (!h) {
        message.error('未找到该历史方案');
        return;
      }
      applyJygzHistoryData(h);
      message.success('已加载历史方案');
    } catch (err: unknown) {
      message.error(err instanceof Error ? err.message : '加载失败');
    } finally {
      setLoading(false);
    }
  };

  const handleDeleteJygzHistory = async (tid: string) => {
    setJygzHistoryDeleting(tid);
    try {
      await schemeApi.deleteJygzHistory(tid);
      setJygzHistoryList((prev) => prev.filter((x) => x.task_id !== tid));
      message.success('已删除');
    } catch (err: unknown) {
      message.error(err instanceof Error ? err.message : '删除失败');
    } finally {
      setJygzHistoryDeleting(null);
    }
  };

  /** 环境配置 Modal 已下线；原先 Tab + MixedContent + editingEnvSettings 的实现保留在 Git 历史 */
  const renderEnvConfigModal = () => null;
  return (
    <div
      style={{
        display: 'flex',
        width: '100%',
        height: '100%',
        minHeight: 0,
        overflow: 'hidden',
        boxSizing: 'border-box',
      }}
    >
      {false ? <MixedContent content="" /> : null}
       
      <div
        style={{
          width: sidebarCollapsed ? 40 : 280,
          height: '100%',
          minHeight: 0,
          display: 'flex',
          flexDirection: 'column',
          borderRight: '1px solid #f0f0f0',
          transition: 'width 0.3s',
          position: 'relative',
          background: '#fff',
          flexShrink: 0,
        }}
      >
        <Button type="text" icon={sidebarCollapsed ? <ArrowRightOutlined /> : <ArrowLeftOutlined />} onClick={() => setSidebarCollapsed(!sidebarCollapsed)} style={{ position: 'absolute', top: 8, right: 8, zIndex: 10, fontSize: 12 }} />
        {!sidebarCollapsed && (<div style={{ flex: 1, minHeight: 0, overflow: 'auto', padding: '8px 0' }}>
          <div style={{ padding: '0 16px 8px 16px', fontWeight: 600, fontSize: 14, display: 'flex', alignItems: 'center', gap: 8 }}>
            <HistoryOutlined style={{ color: '#1890ff' }} /> 历史记录
          </div>
          {jygzHistoryList.length > 0 ? (
            <div style={{ fontSize: 12, color: '#888', padding: '0 16px 6px' }}>已保存到数据库的方案</div>
          ) : null}
          {jygzHistoryList.map((h) => (
            <div
              key={h.task_id}
              onClick={() => { void handleLoadJygzHistoryItem(h.task_id); }}
              style={{ cursor: 'pointer', padding: '8px 8px 8px 16px', borderBottom: '1px solid #f0f0f0', display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 4 }}
            >
              <div style={{ minWidth: 0, flex: 1 }}>
                <div style={{ fontSize: 13, fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={h.scheme_name}>
                  {h.scheme_name}
                </div>
                <div style={{ fontSize: 11, color: '#999' }}>{(h.updated_at || h.created_at || '').slice(0, 10)} · {h.word_count}字</div>
              </div>
              <Popconfirm
                title="删除该历史方案？"
                description="将从数据库中永久删除，不可恢复。"
                onConfirm={() => { void handleDeleteJygzHistory(h.task_id); }}
                okText="删除"
                okButtonProps={{ danger: true }}
              >
                <Button
                  type="text"
                  size="small"
                  danger
                  icon={<DeleteOutlined />}
                  loading={jygzHistoryDeleting === h.task_id}
                  onClick={(e) => e.stopPropagation()}
                  aria-label="删除"
                />
              </Popconfirm>
            </div>
          ))}
          {savedConfigs.length > 0 ? (
            <div style={{ fontSize: 12, color: '#888', padding: '10px 16px 6px' }}>本地已保存配置</div>
          ) : null}
          {savedConfigs.slice(0, 20).map((item) => (
            <div key={item.name} onClick={() => { void handleLoadConfig(item.name); }} style={{ cursor: 'pointer', padding: '10px 16px', borderBottom: '1px solid #f0f0f0' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <span style={{ fontSize: 13, fontWeight: 500 }}>{item.name}</span>
                <span style={{ fontSize: 11, color: '#999' }}>{item.updated_at?.slice(0, 10)}</span>
              </div>
            </div>
          ))}
          {jygzHistoryList.length === 0 && savedConfigs.length === 0 && !dataLoading ? (
            <div style={{ textAlign: 'center', padding: '40px 16px', color: '#999' }}><Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无历史与配置" /></div>
          ) : null}
        </div>)}
      </div>
      <div
        style={{
          flex: 1,
          minWidth: 0,
          minHeight: 0,
          // padding:1,
          paddingLeft:4,
          overflow: 'hidden',
          background: '#f5f5f5',
          display: 'flex',
          flexDirection: 'column',
          boxSizing: 'border-box',
        }}
      >
      
        <Row gutter={12} style={{ flex: 1, minHeight: 0, alignItems: 'stretch' }}>
          <Col
            span={basicInfoCollapsed ? 24 : 8}
            style={{
              height: '100%',
              minHeight: 0,
              transition: 'all 0.3s',
              overflow: 'hidden',
              display: 'flex',
              flexDirection: 'column',
            }}
          >
            
            <Card
              size="small"
              style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}
              styles={{
                body: {
                  flex: 1,
                  minHeight: 0,
                  overflow: 'auto',
                  padding: 12,
                  display: 'flex',
                  flexDirection: 'column',
                },
              }}
              title={<div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}><span style={{ fontWeight: 600 }}>基于规则的试验方案设计</span></div>}
              extra={<Button type="text" size="small" icon={basicInfoCollapsed ? <ArrowRightOutlined /> : <ArrowLeftOutlined />} onClick={() => setBasicInfoCollapsed(!basicInfoCollapsed)} />}
            >
               {/* <Title level={5} style={{ margin: '0 0 12px 0', flexShrink: 0 }}>基于规则的试验方案设计</Title> */}
              {!basicInfoCollapsed && (<>
                <Button type="primary" icon={<ThunderboltOutlined />} onClick={() => setConfigModalOpen(true)} block size="middle" style={{ marginBottom: 12 }}>基础配置</Button>
                {/* 环境配置步骤已下线
                <Button type="default" icon={<FireOutlined />} onClick={() => setEnvConfigModalVisible(true)} block style={{ marginBottom: 12 }} disabled={config.environments.length === 0}>环境配置</Button>
                */}
                <div style={{ marginBottom: 8 }}><Text strong style={{ fontSize: 12 }}>设备名称</Text><Input value={config.equipment_name} onChange={(e) => updateConfig({ equipment_name: e.target.value })} placeholder="设备名称" size="small" style={{ marginTop: 4 }} /></div>
                <div style={{ marginBottom: 8 }}><Text strong style={{ fontSize: 12 }}>文档编号</Text><Input value={config.doc_number} onChange={(e) => updateConfig({ doc_number: e.target.value })} placeholder="文档编号" size="small" style={{ marginTop: 4 }} /></div>
                <div style={{ marginBottom: 8 }}><Text strong style={{ fontSize: 12 }}>任务来源</Text><Input value={config.task_source} onChange={(e) => updateConfig({ task_source: e.target.value })} placeholder={DEFAULT_TASK_SOURCE} size="small" style={{ marginTop: 4 }} /></div>
                <div style={{ marginBottom: 12 }}><Text strong style={{ fontSize: 12 }}>编制依据</Text><TextArea value={config.basis_standard} onChange={(e) => updateConfig({ basis_standard: e.target.value })} placeholder="保存基础配置时自动填入所选方法关联的文件名称；也可手动补充，多条可用换行、顿号「、」或分号分隔" size="small" style={{ marginTop: 4 }} rows={4} /></div>
                <Button type="primary" icon={<SaveOutlined />} onClick={handleRender} loading={loading} block size="middle" style={{ marginTop: 8 }}>保存所有配置并生成方案</Button>
              </>)}
            </Card>
          </Col>
          <Col
            span={basicInfoCollapsed ? 24 : 16}
            style={{
              height: '100%',
              minHeight: 0,
              transition: 'all 0.3s',
              overflow: 'hidden',
              display: 'flex',
              flexDirection: 'column',
            }}
          >
            <Card
              size="small"
              title={<><EyeOutlined /> 方案预览</>}
              style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}
              styles={{
                body: {
                  flex: 1,
                  minHeight: 0,
                  overflow: 'auto',
                  padding: 12,
                },
              }}
              extra={
                <Space size={4} wrap>
                  <Button
                    size="small"
                    type="primary"
                    icon={<SaveOutlined />}
                    onClick={handleOpenSaveScheme}
                    disabled={!renderedMarkdown}
                  >
                    保存方案
                  </Button>
                  <SchemeExportDropdown
                    size="small"
                    onExport={handleExport}
                    loading={exportingKind !== null}
                    disabled={!renderedMarkdown}
                  />
                  <Button
                    size="small"
                    icon={<CloudUploadOutlined />}
                    loading={syncingToKm}
                    onClick={() => void handleSyncToKm()}
                    disabled={!renderedMarkdown || exportingKind !== null}
                  >
                    同步到数据库
                  </Button>
                </Space>
              }>
              {loading ? (<div style={{ textAlign: 'center', padding: 40 }}><Spin /></div>) : renderedMarkdown ? (
                <div style={{ display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0, height: '100%' }}>
                  <JygzSchemePanel
                    markdown={formattedMarkdown}
                    previewDisplayMd={typewriterShown}
                    view={schemePreviewMode}
                    onViewChange={(v) => {
                      setSchemePreviewMode(v);
                      if (v === 'edit') setPreviewRevealActive(false);
                    }}
                    onMarkdownChange={(md) => {
                      setFormattedMarkdown(md);
                      setRenderedMarkdown(md);
                      setPreviewStreamTarget(md);
                    }}
                    taskId={taskId || null}
                  />
                </div>
              ) : (<Empty description="点击'基础配置'选择参数，然后生成方案" />)}
            </Card>
          </Col>
        </Row>
      </div>
      <Modal
        title="保存方案到历史"
        open={saveSchemeModalOpen}
        onCancel={() => setSaveSchemeModalOpen(false)}
        onOk={handleConfirmSaveScheme}
        confirmLoading={savingScheme}
        okText="保存"
        destroyOnHidden
      >
        <div style={{ marginBottom: 12 }}>
          <Text type="secondary">将当前全部配置、环境设置与生成结果一并保存，便于在首页「最近方案」中再次打开。</Text>
        </div>
        <div style={{ marginBottom: 8 }}>
          <Text strong>方案名称</Text>
          <Input
            value={saveSchemeName}
            onChange={(e) => setSaveSchemeName(e.target.value)}
            placeholder="请输入方案名称"
            maxLength={200}
            showCount
            style={{ marginTop: 4 }}
          />
        </div>
        <div>
          <Text strong>方案描述（可选）</Text>
          <TextArea
            value={saveSchemeDescription}
            onChange={(e) => setSaveSchemeDescription(e.target.value)}
            placeholder="简要说明"
            rows={2}
            maxLength={500}
            showCount
            style={{ marginTop: 4 }}
          />
        </div>
      </Modal>
      {/* 环境配置步骤已下线：原「环境配置」大弹窗保留备恢复
      <Modal title="环境配置" open={envConfigModalVisible} onCancel={() => setEnvConfigModalVisible(false)} width="85%" style={{ top: 20 }}
        footer={[<Button key="cancel" onClick={() => setEnvConfigModalVisible(false)}>取消</Button>, <Button key="save" type="primary" onClick={() => { updateConfig({ env_settings_map: editingEnvSettings }); setEnvConfigModalVisible(false); message.success('环境配置已保存'); }}>保存环境配置</Button>]}
      >{renderEnvConfigModal()}</Modal>
      */}
      <ConfigModal
        open={configModalOpen}
        onClose={() => setConfigModalOpen(false)}
        onSave={handleConfigModalSave}
        fetchMethods={fetchJygzMethods}
        contentHeight="calc(88vh - 112px)"
      />
      <style jsx global>{`
        .html-table-wrapper table { border-collapse: collapse; width: 100%; }
        .html-table-wrapper th, .html-table-wrapper td { border: 1px solid #d9d9d9; padding: 6px 8px; }
        .html-table-wrapper th { background: #fafafa; font-weight: bold; }
        .markdown-preview h1 { font-size: 22px; font-weight: bold; margin: 14px 0 8px; }
        .markdown-preview h2 { font-size: 18px; font-weight: bold; margin: 12px 0 6px; }
        .markdown-preview .jygz-md-h3 { font-size: 16px; font-weight: 700; margin: 10px 0 6px; line-height: 1.35; color: rgba(0,0,0,0.88); }
        .markdown-preview .jygz-md-h4 { font-size: 15px; font-weight: 600; margin: 8px 0 5px; line-height: 1.35; color: rgba(0,0,0,0.88); }
        .markdown-preview .jygz-md-h5 { font-size: 14px; font-weight: 600; margin: 8px 0 4px; line-height: 1.35; color: rgba(0,0,0,0.82); }
        .markdown-preview .jygz-md-h6 { font-size: 13px; font-weight: 500; margin: 6px 0 4px; line-height: 1.35; color: rgba(0,0,0,0.72); }
        .markdown-preview table { border-collapse: collapse; width: 100%; margin: 8px 0; }
        .markdown-preview th, .markdown-preview td { border: 1px solid #d9d9d9; padding: 6px 8px; }
        .markdown-preview th { background: #fafafa; font-weight: bold; }
      `}</style>
      <SchemeKmSyncModal
        open={kmSyncModalOpen}
        initialMeta={kmSyncMeta || buildJygzKmMeta({ fileType: 'docx' })}
        file={kmSyncFile}
        onClose={() => {
          setKmSyncModalOpen(false);
          setKmSyncFile(null);
          setKmSyncMeta(null);
        }}
      />
    </div>
  );
}

export default function JYGZPage() {
  return (
    <Suspense
      fallback={
        <div
          style={{
            height: '100%',
            minHeight: 0,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            overflow: 'hidden',
          }}
        >
          <Spin size="large" />
        </div>
      }
    >
      <JYGZPageContent />
    </Suspense>
  );
}
