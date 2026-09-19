'use client';

import React, { useState, useEffect, useRef, useCallback } from 'react';
import { useRouter } from 'next/navigation';
import { Bubble } from '@ant-design/x';
import {
  Layout, Collapse, Tag, Typography, Button, List, Popconfirm, Empty, Spin,
  Avatar, message as antMessage, Modal, Drawer, Tabs, Descriptions, Space, Select, Alert, Table
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import {
  DeleteOutlined, ClearOutlined, RobotOutlined, UserOutlined, SearchOutlined,
  FileSearchOutlined, MergeCellsOutlined, ThunderboltOutlined, LoadingOutlined,
  CheckCircleOutlined, EyeOutlined, PlusOutlined, MenuFoldOutlined, MenuUnfoldOutlined,
  ReloadOutlined, SettingOutlined, CopyOutlined
} from '@ant-design/icons';
import MarkdownRenderer from '@/components/MarkdownRenderer';
import EnvironmentAnswerBody, { isEnvironmentPropertiesAnswer } from '@/components/EnvironmentAnswerBody';
import EnvironmentHtmlField, { ENVIRONMENT_HTML_TABLE_STYLES } from '@/components/EnvironmentHtmlField';
import { rewriteEnvironmentMediaUrls } from '@/components/ThreeColumnLayout/environmentMedia';
import ZnwdAnswerFeedback, {
  type AnswerFeedbackSubmitPayload,
  type CorrectionHints,
} from '@/components/ZnwdAnswerFeedback';
import { useSSE } from '@/app/hooks/useSSE';
import KnowledgeGraph, { type GraphData, type GraphNode } from '@/components/KnowledgeGraph/KnowledgeGraph';
import LocalDocxPreview from '@/components/LocalDocxPreview';
import EnvironmentTable, { type EnvironmentTableRow, type SelectedProjectEntry, type SelectedIndicatorEntry } from '@/components/EnvironmentTable';
import MethodList, { type MethodNode } from '@/components/MethodList/index';
import ZnwdProgressTimeline from '@/components/ZnwdProgressTimeline';
import {
  humanizeThinkingKind,
  humanizeStepLabel,
  humanizeStepMessage,
  formatSourceButtonLabel,
  formatSourcePreviewLine,
} from '@/app/GLCX/ZNWD/znwdUserCopy';
import {
  formatNodeTitle,
  formatNodePropertiesForDisplay,
  nodeTypeZh,
} from '@/app/GLCX/ZNWD/formatSourceNode';
import { ENVIRONMENT_ORDER } from '@/constants/environments';

import ZnwdUnifiedComposer from '@/app/GLCX/ZNWD/components/ZnwdUnifiedComposer';
import ZnwdAppearanceSettings from '@/app/GLCX/ZNWD/components/ZnwdAppearanceSettings';
import ZnwdEquipmentHierarchyTable, {
  type ZnwdEquipmentHierarchyRow,
} from '@/app/GLCX/ZNWD/components/ZnwdEquipmentHierarchyTable';
import {
  buildSourceTableCacheKey,
  getGlobalSourceTableCache,
  setGlobalSourceTableCache,
  type ZnwdCachedSourceTable,
  type ZnwdEnvironmentSourceRow,
} from '@/app/GLCX/ZNWD/utils/sourceTableCache';

export type { ZnwdEnvironmentSourceRow };
import { appearanceToCssVars } from '@/app/GLCX/ZNWD/hooks/useZnwdAppearance';
import { useZnwdAppearanceState } from '@/app/GLCX/ZNWD/hooks/useZnwdAppearanceState';

const { Sider, Content } = Layout;
const { Text, Paragraph } = Typography;

const ZNWD_CONTENT_MAX_WIDTH = 960;
const ZNWD_LLM_STORAGE_KEY = 'znwd_llm_model_key';

function znwdStripSourcePick(s: string): string {
  let t = s || '';
  t = t.replace(/<<<\s*SOURCE_PICK\s*>>>[\s\S]*?<<<\s*END_SOURCE_PICK\s*>>>/gi, '');
  t = t.replace(/<<<\s*SOURCE_PICK\s*>>>[\s\S]*$/gi, '');
  return t;
}

/** 正文：去掉思考块与协议标签，保留 CONTENT 内正文。 */
function znwdSanitizeAnswerBodyForDisplay(raw: string): string {
  let s = raw || '';
  s = s.replace(/<\s*THINK\s*>[\s\S]*?<\s*\/\s*THINK\s*>/gi, '');
  s = s.replace(/<\s*thinking\s*>[\s\S]*?<\s*\/\s*thinking\s*>/gi, '');
  s = s.replace(/\[\s*THINK\s*\][\s\S]*?\[\s*\/\s*THINK\s*\]/gi, '');
  s = s.replace(/<\s*CONTENT\s*>([\s\S]*?)<\s*\/\s*CONTENT\s*>/gi, '$1');
  s = s.replace(/<\s*content\s*>([\s\S]*?)<\s*\/\s*content\s*>/gi, '$1');
  s = s.replace(/\[\s*CONTENT\s*\]([\s\S]*?)\[\s*\/\s*CONTENT\s*\]/gi, '$1');
  s = s.replace(/<\s*\/?\s*(?:THINK|thinking|CONTENT|content)\s*>/gi, '');
  s = znwdStripSourcePick(s);
  return rewriteEnvironmentMediaUrls(s.trim());
}

/** 去掉嵌套 {...} 中疑似 RoutePlan / meta steps 的 JSON 块 */
function znwdStripBalancedJsonBlobs(raw: string, isRoutePlanBlob: (t: string) => boolean): string {
  let out = '';
  let i = 0;
  while (i < raw.length) {
    if (raw[i] !== '{') {
      out += raw[i];
      i += 1;
      continue;
    }
    let depth = 0;
    let j = i;
    while (j < raw.length) {
      const ch = raw[j];
      if (ch === '{') depth += 1;
      else if (ch === '}') {
        depth -= 1;
        if (depth === 0) {
          const blob = raw.slice(i, j + 1);
          if (!isRoutePlanBlob(blob)) out += blob;
          i = j + 1;
          break;
        }
      }
      j += 1;
    }
    if (j >= raw.length) break;
  }
  return out;
}

function znwdIsRoutePlanJsonBlob(t: string): boolean {
  return (
    /"answer_strategy"|"intended_business_id"|"probe_snapshot"|"params_draft"|"guessed_node_types"/i.test(t)
    || /"purpose_node"|"biz_type"|"known_type"|"hop_direction"|"biz_key"/i.test(t)
    || /"steps"\s*:\s*\[/i.test(t)
    || /"op_code"/i.test(t)
    || /BIZ_[A-Z0-9_]+/.test(t)
  );
}

/** 去掉流式误插入的行内【路由】【归纳】（历史脏数据） */
function znwdStripStrayThinkingTags(s: string): string {
  return s.replace(/(\S)【路由】/g, '$1').replace(/(\S)【归纳】/g, '$1');
}

/** 仅在 kind 切换时加段落标签（每个 SSE 小块不要重复前缀） */
function znwdThinkingSectionPrefix(kind: string | undefined, lastKind: string | undefined): string {
  const k = (kind || 'system').toLowerCase();
  const prev = (lastKind || 'system').toLowerCase();
  if (k === prev) return '';
  const label = humanizeThinkingKind(kind);
  if (!label) return '';
  return `【${label}】`;
}

function znwdAppendThinkingChunk(
  prev: string,
  chunk: string,
  kind?: string,
  lastKind?: string
): { content: string; lastKind: string } {
  const k = (kind || 'system').toLowerCase();
  const section = znwdThinkingSectionPrefix(kind, lastKind);
  let text = prev;
  if (section) {
    const needNl = text.length > 0 && !text.endsWith('\n');
    text += (needNl ? '\n' : '') + section;
  }
  return { content: text + chunk, lastKind: k };
}

/** 思考区：去掉 RoutePlan JSON、检索规划与协议标签。 */
function znwdStripThinkingRoutePlanJson(raw: string): string {
  let s = raw || '';
  s = znwdStripBalancedJsonBlobs(s, znwdIsRoutePlanJsonBlob);
  const brace = s.lastIndexOf('{');
  if (brace >= 0 && !s.slice(brace).includes('}')) {
    s = s.slice(0, brace);
  }
  const noise =
    /probe_snapshot|params_draft|answer_strategy|intended_business_id|BIZ_[A-Z0-9_]+|单跳关系|业务类型为|retrieval_question\s*补全|Equipment\s*→/i;
  const lines = s.split('\n').filter((line) => {
    const t = line.trim();
    if (!t) return true;
    if (/^[\{\[]/.test(t) || noise.test(t)) return false;
    return true;
  });
  return lines.join('\n');
}

function znwdSanitizeThinkingBodyForDisplay(raw: string, _isComplete: boolean): string {
  let s = raw || '';
  s = s.replace(/<\s*CONTENT\s*>[\s\S]*?<\s*\/\s*CONTENT\s*>/gi, '');
  s = s.replace(/<\s*content\s*>[\s\S]*?<\s*\/\s*content\s*>/gi, '');
  s = s.replace(/\[\s*CONTENT\s*\][\s\S]*?\[\s*\/\s*CONTENT\s*\]/gi, '');
  s = s.replace(/<\s*THINK\s*>([\s\S]*?)<\s*\/\s*THINK\s*>/gi, '$1\n');
  s = s.replace(/<\s*thinking\s*>([\s\S]*?)<\s*\/\s*thinking\s*>/gi, '$1\n');
  s = s.replace(/\[\s*THINK\s*\]([\s\S]*?)\[\s*\/\s*THINK\s*\]/gi, '$1\n');
  s = s.replace(/<\s*\/?\s*(?:THINK|thinking|CONTENT|content)\s*>/gi, '');
  s = znwdStripSourcePick(s);
  s = znwdStripThinkingRoutePlanJson(s);
  s = znwdStripStrayThinkingTags(s);
  s = s.replace(/\n{3,}/g, '\n\n');
  return s.trim();
}

/** Preflight/HTTP 路由进度文案，不进入思考面板 */
const ZNWD_BOOTSTRAP_TEXT_MARKERS = [
  '正在准备',
  '正在连接',
  '正在理解',
  '正在结合对话',
  '正在进行意图',
  '正在用模型分析',
  '进入 V6 Agent',
  '正在根据问法',
  '已在图谱中定位',
  '问法较复杂',
  '已收到您的问题',
  '正在图谱中验证',
  '正在查询清单库',
  '正在根据检索结果',
];

function isZnwdBootstrapThinkingText(chunk: string): boolean {
  const t = (chunk || '').trim();
  if (!t) return true;
  if (t.includes('【检索规划】')) return false;
  return ZNWD_BOOTSTRAP_TEXT_MARKERS.some((p) => t.includes(p));
}

interface ThinkingStep {
  type: string;
  message: string;
  status?: 'loading' | 'done';
  phase?: string;
  templateId?: string;
  candidateCount?: number;
  bindingCount?: number;
  zvecUsed?: boolean;
  subSteps?: { id?: string; purpose?: string; status?: string }[];
  hits?: number;
  sample?: string[];
  chunks?: number;
  sources?: string[];
  totalHits?: number;
  neo4jHits?: number;
  chromaHits?: number;
  results?: any[];
  matchedChunks?: any[];
  fullStandards?: any[];
}

interface ThinkingProcess {
  content: string;
  isComplete: boolean;
}

interface HistoryItem {
  id: number;
  sessionId: string;
  title: string;
  messageCount: number;
  createdAt: string;
  updatedAt: string;
  messages?: any[];
}

interface ChatMessage {
  id: string;
  role: 'user' | 'ai';
  content: string;
  thinkingSteps?: ThinkingStep[];
  thinkingProcess?: ThinkingProcess;
  answerContent?: string;
  /** 流式阶段：steps=仅在加载动画处覆盖展示步骤；thinking/answer=展示气泡内思考区与正文 */
  streamPhase?: 'steps' | 'thinking' | 'answer';
  /** 当前检索/处理步骤一句话（下一条 step 覆盖上一条） */
  stepStatusLine?: string;
  /** Preflight 阶段收集的进度句，用于灰字轮换 */
  bootstrapLines?: string[];
  /** 服务端分段耗时（开发调试 / done 事件） */
  serverTimingMs?: Record<string, number>;
  /** 本轮回答开始时间戳 */
  answerStartedAt?: number;
  /** 本轮回答耗时（毫秒） */
  answerDurationMs?: number;
  sourceData?: MessageSourceData;
  sourceLoading?: boolean;
  sourceQuestion?: string;
  /** V5：模型判定的最相关向量库来源（用于来源抽屉高亮） */
  primaryChromaSource?: PrimaryChromaSource | null;
  /** 流式命中摘要（一行自然语言） */
  sourcePreviewLine?: string;
  /** 服务端下发的 SSYQ 表格检索词，仅用于 /source/table，不在界面展示 */
  tableSearchKeyword?: string;
  /** 回答完成后预取的来源表格（查看来源时直接使用，不再请求） */
  cachedSourceTable?: ZnwdCachedSourceTable;
  /** 来源表格是否已预取完成 */
  sourceTableReady?: boolean;
  /** 来源表格预取中 */
  sourceTableLoading?: boolean;
  /** ZNWD1：上一段思考 SSE 的 kind，用于段落前缀去重 */
  thinkingLastKind?: string;
  /** ZNWD4：低置信路由反问，等待用户选业务 */
  routeClarify?: {
    clarifyEventId: string;
    originalQuestion: string;
    prompt: string;
    options: { id: string; routeId: string; label: string }[];
    resolved?: boolean;
    selectedLabel?: string;
  };
  /** ZNWD4：同名实体歧义反问 */
  entityClarify?: {
    clarifyEventId: string;
    originalQuestion: string;
    prompt: string;
    childName?: string;
    options: { id: string; nodeId: string; label: string; parentType?: string; name?: string }[];
    resolved?: boolean;
    selectedLabel?: string;
  };
  /** ZNWD4：答后纠错 */
  turnId?: string;
  routingEventId?: string;
  answerCorrection?: CorrectionHints & { resolved?: boolean };
  answerFeedback?: { rating?: 'positive' | 'negative'; submitted?: boolean };
  /** ZNWD4：路由阶段草稿（意图/关键词） */
  routeDraft?: {
    purpose?: string;
    purpose_zh?: string;
    route_id?: string;
    keywords?: string[];
  };
  /** ZNWD4：图库无方法时 LLM 生成后，可跳转分类管理新增 */
  methodCreateAction?: {
    enabled: boolean;
    label?: string;
    href?: string;
    draft?: Record<string, unknown>;
  };
}

interface Neo4jNodeResult {
  labels?: string[] | string;
  properties?: Record<string, any>;
}

interface ChromaChunkResult {
  doc_id?: string;
  title?: string;
  type?: string;
  source?: string;
  source_file?: string;
  minio_path?: string;
  file_format?: string;
  metadata?: Record<string, unknown>;
  content?: string;
}

interface PrimaryChromaSource {
  doc_id?: string;
  title?: string;
  minio_path?: string;
  file_format?: string;
  reason?: string;
}

interface MessageSourceData {
  neo4jResults: Neo4jNodeResult[];
  chromaResults: ChromaChunkResult[];
}

interface LlmModelOption {
  model_key: string;
  model_name: string;
  description?: string;
}

interface InterestOption {
  id: string;
  name: string;
}

function getStepIcon(type: string, status?: string) {
  if (status === 'loading') return <LoadingOutlined style={{ color: '#1677ff' }} />;
  if (status === 'done') return <CheckCircleOutlined style={{ color: '#52c41a' }} />;
  switch (type) {
    case 'neo4j_search': return <SearchOutlined />;
    case 'chroma_search': return <FileSearchOutlined />;
    case 'fusion': return <MergeCellsOutlined />;
    case 'generating': return <ThunderboltOutlined />;
    default: return <SearchOutlined />;
  }
}

function getStepLabel(type: string) {
  const mapped = humanizeStepLabel(type);
  if (mapped !== '处理中') return mapped;
  switch (type) {
    case 'intent_judge': return '理解问题';
    case 'neo4j_search': return '清单检索';
    case 'chroma_search': return '文档检索';
    case 'retrieve': return '检索';
    case 'synthesis': return '整理回答';
    case 'fusion': return '合并结果';
    case 'generating': return '生成回答';
    default: return '处理中';
  }
}

let znwdStepLoadingStyleInstalled = false;
function installZnwdStepLoadingStyles() {
  if (znwdStepLoadingStyleInstalled || typeof document === 'undefined') return;
  znwdStepLoadingStyleInstalled = true;
  const el = document.createElement('style');
  el.setAttribute('data-znwd', 'step-loading');
  el.textContent = `
@keyframes znwdDotFade {
  0%, 80%, 100% { opacity: 0.28; transform: scale(0.85); }
  40% { opacity: 1; transform: scale(1); }
}
.znwd-step-dots { display: inline-flex; gap: 5px; align-items: center; padding: 2px 0; }
.znwd-step-dots i {
  width: 6px; height: 6px; border-radius: 50%; background: #8c8c8c;
  animation: znwdDotFade 1.15s ease-in-out infinite both;
}
.znwd-step-dots i:nth-child(1) { animation-delay: 0s; }
.znwd-step-dots i:nth-child(2) { animation-delay: 0.18s; }
.znwd-step-dots i:nth-child(3) { animation-delay: 0.36s; }
`;
  document.head.appendChild(el);
}

function StepLoadingWithStatus({ text, lines }: { text?: string; lines?: string[] }) {
  const pool = (lines && lines.length > 0 ? lines : text?.trim() ? [text.trim()] : []) as string[];
  const [idx, setIdx] = useState(0);

  useEffect(() => {
    installZnwdStepLoadingStyles();
  }, []);

  useEffect(() => {
    setIdx(0);
  }, [pool.join('|')]);

  useEffect(() => {
    if (pool.length <= 1) return undefined;
    const timer = window.setInterval(() => {
      setIdx((i) => (i + 1) % pool.length);
    }, 1400);
    return () => window.clearInterval(timer);
  }, [pool.length, pool.join('|')]);

  const display = pool.length > 0 ? pool[pool.length - 1] : '处理中…';

  return (
    <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12, maxWidth: 420 }}>
      <span className="znwd-step-dots" aria-hidden>
        <i />
        <i />
        <i />
      </span>
      <Text type="secondary" style={{ fontSize: 12, lineHeight: 1.5, flex: 1 }}>
        {display}
      </Text>
    </div>
  );
}

function Neo4jResultModal({ open, onClose, results, selectedSource }: { open: boolean; onClose: () => void; results: any[]; selectedSource?: any }) {
  return (
    <Modal title="清单检索结果" open={open} onCancel={onClose} footer={null} width={700}>
      <List
        dataSource={results}
        renderItem={(item: { labels: any[]; properties: any; }, index: number) => {
          const isSelected = selectedSource && 
            selectedSource.labels?.[0] === item.labels?.[0] &&
            JSON.stringify(selectedSource.properties) === JSON.stringify(item.properties);
          const title = formatNodeTitle(item.labels, item.properties);
          const fields = formatNodePropertiesForDisplay(item.labels, item.properties);
          
          return (
            <List.Item style={{ 
              display: 'block', 
              padding: '8px 0',
              background: isSelected ? '#e6f7ff' : 'transparent',
              borderRadius: 4,
              marginBottom: 8,
              border: isSelected ? '1px solid #1890ff' : '1px solid transparent'
            }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <Tag color="blue">{nodeTypeZh(item.labels?.[0])}</Tag>
                <Text type="secondary" style={{ fontSize: 12 }}>{title || `记录 ${index + 1}`}</Text>
              </div>
              <div style={{ marginTop: 4, fontSize: 13 }}>
                {fields.map(({ key, label, value }) => (
                  <div key={key} style={{ marginBottom: 2 }}>
                    <Text strong>{label}:</Text> <Text>{value}</Text>
                  </div>
                ))}
              </div>
            </List.Item>
          );
        }}
      />
    </Modal>
  );
}

function cleanContent(text: string): string {
  return text.replace(/=====\s*Page\s*\d+\s*=====/g, '').trim();
}

function isGarbledText(text: string): boolean {
  if (!text) return true;
  const replacementCount = (text.match(/�/g) || []).length;
  if (replacementCount >= 3) return true;
  const weirdBlockCount = (text.match(/[犐-犿]/g) || []).length;
  if (weirdBlockCount >= 8) return true;
  return false;
}

function filterValidChromaChunks(chunks: ChromaChunkResult[]): ChromaChunkResult[] {
  return (chunks || []).filter((chunk) => !isGarbledText(chunk.content || ''));
}

function serializeChatMessagesForSave(msgs: ChatMessage[]) {
  return msgs.map((m) => ({
    role: m.role === 'ai' ? 'assistant' : m.role,
    content: m.role === 'ai' ? (m.answerContent || m.content) : m.content,
    thinkingSteps: m.thinkingSteps,
    thinkingProcess: m.thinkingProcess
      ? {
          content: m.thinkingProcess.content,
          isComplete: m.thinkingProcess.isComplete,
        }
      : undefined,
    sourceData: m.role === 'ai' ? m.sourceData : undefined,
    primaryChromaSource: m.role === 'ai' ? m.primaryChromaSource : undefined,
    tableSearchKeyword: m.role === 'ai' ? m.tableSearchKeyword : undefined,
    routingEventId: m.role === 'ai' ? m.routingEventId : undefined,
    turnId: m.role === 'ai' ? m.turnId : undefined,
    methodCreateAction: m.role === 'ai' ? m.methodCreateAction : undefined,
    answerFeedback: m.role === 'ai' ? m.answerFeedback : undefined,
  }));
}

/** 优先用 source_data 写入的 sourceData，否则从 step 里的 results / matchedChunks 兜底（与流式事件顺序无关） */
function getEffectiveSourceData(msg: ChatMessage): MessageSourceData {
  const from = msg.sourceData || { neo4jResults: [], chromaResults: [] };
  const neoStep = (msg.thinkingSteps || []).find((s) => s.type === 'neo4j_search') as ThinkingStep & {
    results?: Neo4jNodeResult[];
  };
  const chromaStep = (msg.thinkingSteps || []).find((s) => s.type === 'chroma_search') as ThinkingStep & {
    matchedChunks?: ChromaChunkResult[];
  };
  const neo =
    from.neo4jResults && from.neo4jResults.length > 0
      ? from.neo4jResults
      : Array.isArray(neoStep?.results)
        ? neoStep.results
        : [];
  const rawCh =
    from.chromaResults && from.chromaResults.length > 0
      ? from.chromaResults
      : Array.isArray(chromaStep?.matchedChunks)
        ? chromaStep.matchedChunks
        : [];
  return {
    neo4jResults: Array.isArray(neo) ? neo : [],
    chromaResults: filterValidChromaChunks(Array.isArray(rawCh) ? rawCh : []),
  };
}

/** 抽屉内文件 Tab：合并 step 与 sourceData 中的 Chroma 片段（去重），不过滤乱码以便兜底预览 */
function getChromaRawForDrawer(msg: ChatMessage): ChromaChunkResult[] {
  const fromData = msg.sourceData?.chromaResults || [];
  const chromaStep = (msg.thinkingSteps || []).find((s) => s.type === 'chroma_search') as ThinkingStep & {
    matchedChunks?: ChromaChunkResult[];
  };
  const fromStep = chromaStep?.matchedChunks || [];
  const keys = new Set<string>();
  const out: ChromaChunkResult[] = [];
  for (const c of [...fromStep, ...fromData]) {
    if (!c || typeof c !== 'object') continue;
    const k = `${c.doc_id || ''}|${c.title || ''}|${(c as ChromaChunkResult).source_file || ''}`;
    if (keys.has(k)) continue;
    keys.add(k);
    out.push(c);
  }
  return out;
}

const ZNWD_ENVIRONMENT_SOURCE_FIELD_KEYS: (keyof ZnwdEnvironmentSourceRow)[] = [
  '名称',
  '标签',
  '描述',
  '分类',
  '分级',
  '典型组合',
  '约束',
  '要求',
];

const ZNWD_ENVIRONMENT_HTML_FIELDS = new Set<keyof ZnwdEnvironmentSourceRow>([
  '描述',
  '分类',
  '分级',
  '典型组合',
  '约束',
  '要求',
]);

const ENV_NEO_PROP_TO_ZH: Record<string, keyof ZnwdEnvironmentSourceRow> = {
  Name: '名称',
  Label: '标签',
  Description: '描述',
  Type: '分类',
  Class: '分级',
  Combination: '典型组合',
  Limit: '约束',
  Request: '要求',
};

function znwdNeoPropGet(props: Record<string, unknown>, field: string): string {
  for (const k of [field, field.toLowerCase(), field.toUpperCase()]) {
    const v = props[k];
    if (v != null && String(v).trim()) return String(v).trim();
  }
  return '';
}

function znwdFormatDurationMs(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) return '';
  if (ms < 1000) return `${Math.round(ms)}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

function znwdComputeAnswerDuration(startedAt?: number): number | undefined {
  if (!startedAt) return undefined;
  return Math.max(0, Date.now() - startedAt);
}

function znwdQuestionAsksEquipmentHierarchy(question: string): boolean {
  const text = (question || '').trim();
  if (!text) return false;
  if (/(试验项目|测试项目|考核指标).{0,8}(设备|装备)/.test(text)) return false;
  if (/(设备|装备).{0,8}(试验项目|测试项目|考核指标)/.test(text)) return false;
  if (/(装备类型|设备类型|一级.*装备|二级.*类型|具体装备|装备类别)/.test(text)) return true;
  if (/(哪些|有哪些|列出).{0,12}(装备类型|设备类型|二级类型|具体装备)/.test(text)) return true;
  return false;
}

/** API 未切 equipment_hierarchy 视图时，从 Neo 命中 EquipmentType/Equipment 兜底 */
function buildEquipmentHierarchyFromNeoHits(hits: Neo4jNodeResult[]): ZnwdEquipmentHierarchyRow[] {
  const flat = flattenZnwdNeoHitsForGraph(hits as unknown[]);
  const rows: ZnwdEquipmentHierarchyRow[] = [];
  const seen = new Set<string>();
  for (const node of flat) {
    const labels = node.labels || [];
    const props = (node.properties || {}) as Record<string, unknown>;
    if (labels.includes('EquipmentType')) {
      const l2Id = znwdNeoPropGet(props, 'Id') || znwdNeoPropGet(props, 'id') || '';
      const key = `${l2Id}::`;
      if (!l2Id || seen.has(key)) continue;
      seen.add(key);
      rows.push({
        level1Type: znwdNeoPropGet(props, 'Type') || '未分类',
        level2TypeId: l2Id,
        level2TypeName: znwdNeoPropGet(props, 'Name') || l2Id,
        equipmentId: '',
        equipmentName: '',
      });
    }
    if (labels.includes('Equipment')) {
      const eqId = znwdNeoPropGet(props, 'Id') || znwdNeoPropGet(props, 'id') || '';
      const key = `::${eqId}`;
      if (!eqId || seen.has(key)) continue;
      seen.add(key);
      rows.push({
        level1Type: '未分类',
        level2TypeId: '',
        level2TypeName: '—',
        equipmentId: eqId,
        equipmentName: znwdNeoPropGet(props, 'Name') || eqId,
      });
    }
  }
  return rows;
}

function znwdQuestionAsksEnvironmentAttributes(question: string): boolean {
  const text = (question || '').trim();
  if (!text) return false;
  if (/(环境|试验环境).*(分类|分级|类型|组合|约束|要求|体系)/.test(text)) return true;
  if (/(分类|分级|典型组合|约束|要求).{0,16}(环境|试验环境)/.test(text)) return true;
  return false;
}

/** API 未切 environment 视图时，从 Neo 命中 Environment 节点兜底构造来源行 */
function buildEnvironmentSourcesFromNeoHits(hits: Neo4jNodeResult[]): ZnwdEnvironmentSourceRow[] {
  const flat = flattenZnwdNeoHitsForGraph(hits as unknown[]);
  const seen = new Set<string>();
  const rows: ZnwdEnvironmentSourceRow[] = [];
  for (const node of flat) {
    if (!node.labels?.includes('Environment')) continue;
    const props = (node.properties || {}) as Record<string, unknown>;
    const id =
      znwdNeoPropGet(props, 'Id') ||
      znwdNeoPropGet(props, 'id') ||
      znwdNeoPropGet(props, 'Name');
    if (!id || seen.has(id)) continue;
    seen.add(id);
    const row: ZnwdEnvironmentSourceRow = {
      id,
      名称: '—',
      标签: '—',
      描述: '—',
      分类: '—',
      分级: '—',
      典型组合: '—',
      约束: '—',
      要求: '—',
    };
    for (const [en, zh] of Object.entries(ENV_NEO_PROP_TO_ZH)) {
      const v = znwdNeoPropGet(props, en);
      if (v) row[zh] = v;
    }
    rows.push(row);
  }
  return rows;
}

const ZNWD_ROUTE_PROGRESS_STAGE_LABELS: Record<string, string> = {
  route_lock: '识别意图与关键词',
  step1: '分析问题目的',
  step2: '匹配业务方案',
  assemble: '组装检索方案',
  context_enrich: '结合对话上下文',
};

function renderZnwdEnvironmentSourceCell(
  field: keyof ZnwdEnvironmentSourceRow,
  val: unknown,
  compact = true,
) {
  const text = String(val ?? '').trim();
  if (!text || text === '—') return '—';
  if (ZNWD_ENVIRONMENT_HTML_FIELDS.has(field)) {
    return <EnvironmentHtmlField value={text} compact={compact} />;
  }
  return (
    <span style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>{text}</span>
  );
}

const ZNWD_ENVIRONMENT_SOURCE_COLUMNS: ColumnsType<ZnwdEnvironmentSourceRow> =
  ZNWD_ENVIRONMENT_SOURCE_FIELD_KEYS.map((field) => ({
    title: field,
    dataIndex: field,
    key: field,
    width:
      field === '描述'
        ? 160
        : field === '约束' || field === '要求' || field === '典型组合'
          ? 180
          : field === '名称'
            ? 120
            : 100,
    ellipsis: !ZNWD_ENVIRONMENT_HTML_FIELDS.has(field),
    onCell: () =>
      ZNWD_ENVIRONMENT_HTML_FIELDS.has(field)
        ? { style: { verticalAlign: 'top' as const, maxWidth: 280 } }
        : {},
    render: (val: unknown) => renderZnwdEnvironmentSourceCell(field, val, true),
  }));

/** 与 SSYQ 页一致：将 /search 返回的 tableRows 转为 EnvironmentTable 数据源 */
function ssyqTableRowsToEnvironmentRows(tableRows: any[]): EnvironmentTableRow[] {
  const envRows: EnvironmentTableRow[] = [];
  const seenEqIds = new Set<string>();
  for (const row of tableRows || []) {
    if (row.type === 'equipment' && row.equipmentId && !seenEqIds.has(row.equipmentId)) {
      seenEqIds.add(row.equipmentId);
      envRows.push({
        equipmentTypeId: row.equipmentId,
        equipmentTypeName: row.equipmentName || row.equipmentId,
        projectsByEnv: row.projectsByEnv || {},
        indicators: row.indicators || [],
      });
    } else if ((row.type === 'project' || row.type === 'indicator') && row.projectsByEnv && row.equipments) {
      for (const eq of row.equipments) {
        if (!eq?.id || seenEqIds.has(eq.id)) continue;
        seenEqIds.add(eq.id);
        envRows.push({
          equipmentTypeId: eq.id,
          equipmentTypeName: eq.name || eq.id,
          projectsByEnv: row.projectsByEnv || {},
          indicators: row.indicators || [],
        });
      }
    }
  }
  return envRows;
}

/** 将 V4 rowKind、V5 的 n/m 等行展开为带 labels/properties 的节点，供图谱与命中摘要使用 */
function flattenZnwdNeoHitsForGraph(hits: any[]): Neo4jNodeResult[] {
  const out: Neo4jNodeResult[] = [];
  const push = (labels: any, properties: any) => {
    if (labels == null && !properties) return;
    const labs = Array.isArray(labels)
      ? labels
      : typeof labels === 'string'
        ? labels.split(',').map((s: string) => s.trim()).filter(Boolean)
        : [];
    if (!labs.length) return;
    out.push({
      labels: labs,
      properties: properties && typeof properties === 'object' ? properties : {},
    });
  };

  for (const hit of hits || []) {
    if (!hit || typeof hit !== 'object' || (hit as any).error) continue;
    const rk = hit.rowKind as string | undefined;
    if (rk === 'EquipmentToTestProject') {
      if (hit.equipment) push(hit.equipment.labels, hit.equipment.properties);
      if (hit.testProject) push(hit.testProject.labels, hit.testProject.properties);
      continue;
    }
    if (rk === 'EquipmentToIndicator') {
      if (hit.equipment) push(hit.equipment.labels, hit.equipment.properties);
      if (hit.indicator) push(hit.indicator.labels, hit.indicator.properties);
      continue;
    }
    if (rk === 'EquipmentProjectTestMethod') {
      if (hit.equipment) push(hit.equipment.labels, hit.equipment.properties);
      if (hit.testProject) push(hit.testProject.labels, hit.testProject.properties);
      if (hit.testMethod) push(hit.testMethod.labels, hit.testMethod.properties);
      continue;
    }
    if (rk === 'EnvironmentToTestProject') {
      if (hit.environment) push(hit.environment.labels, hit.environment.properties);
      if (hit.testProject) push(hit.testProject.labels, hit.testProject.properties);
      continue;
    }
    if (hit.n && typeof hit.n === 'object' && hit.n.labels) push(hit.n.labels, hit.n.properties);
    if (hit.m && typeof hit.m === 'object' && hit.m.labels) push(hit.m.labels, hit.m.properties);
    if (hit.labels != null || typeof hit.labels === 'string') push(hit.labels, hit.properties);
  }
  return out;
}

function applyZnwdSelectionHints(
  envRows: EnvironmentTableRow[],
  hints: {
    selectProjectIds?: string[];
    selectIndicatorIds?: string[];
    selectMethodIds?: string[];
  },
): { projects: SelectedProjectEntry[]; indicators: SelectedIndicatorEntry[]; methodIds: string[] } {
  const pset = new Set((hints.selectProjectIds || []).filter(Boolean));
  const iset = new Set((hints.selectIndicatorIds || []).filter(Boolean));
  const perEqProject = new Map<string, SelectedProjectEntry>();

  for (const row of envRows) {
    const eqId = row.equipmentTypeId;
    for (const envName of ENVIRONMENT_ORDER) {
      const plist = row.projectsByEnv[envName] || [];
      for (const node of plist) {
        if (pset.has(node.id) && !perEqProject.has(eqId)) {
          perEqProject.set(eqId, {
            projectId: node.id,
            equipmentTypeId: eqId,
            categoryId: node.type === 'category' ? node.id : undefined,
            nodeData: node,
          });
        }
        for (const ch of node.children || []) {
          if (pset.has(ch.id) && !perEqProject.has(eqId)) {
            perEqProject.set(eqId, {
              projectId: ch.id,
              equipmentTypeId: eqId,
              categoryId: node.id,
              nodeData: ch,
            });
          }
        }
      }
    }
  }

  const perEqInd = new Map<string, SelectedIndicatorEntry>();
  for (const row of envRows) {
    const eqId = row.equipmentTypeId;
    for (const ind of row.indicators || []) {
      if (iset.has(ind.id) && !perEqInd.has(eqId)) {
        perEqInd.set(eqId, {
          indicatorId: ind.id,
          equipmentTypeId: eqId,
          nodeData: ind,
        });
      }
    }
  }

  return {
    projects: Array.from(perEqProject.values()),
    indicators: Array.from(perEqInd.values()),
    methodIds: (hints.selectMethodIds || []).filter(Boolean),
  };
}

/** 用户问法是否明确在问「测试/试验方法」等（用于抽屉表格默认全选 vs 按命中勾选） */
function znwdUserQuestionInvolvesMethod(question: string): boolean {
  const t = (question || '').trim();
  if (!t) return false;
  if (
    /(测试方法|试验方法|测定方法|验证方法|检测方法|怎么做|如何做|操作步骤|试验步骤|用什[么么]方法|哪些方法|有什么方法|查什么方法|方法\s*有|方法\s*是)/.test(
      t,
    )
  ) {
    return true;
  }
  if (/(项目|试验|测试)[^。；;]{0,24}的\s*方法/.test(t)) return true;
  if (/的\s*方法\s*([？?。]|$)/.test(t)) return true;
  if (/方法\s*(有|是|为|包括|列举|哪些|什么)/.test(t)) return true;
  return false;
}

/** 服务端未下发 ssyq 词时，从用户问句取短词（与后端 normalize_znwd_table_keyword 规则对齐） */
function znwdFallbackTableKeywordFromQuestion(q: string): string {
  let t = (q || '').trim();
  if (!t) return '';
  t = t.replace(/^【承前主体】[^\n。；;]+[。；;\n]\s*/, '').trim();
  t = t.replace(/^[请问一下：:，,。.「」【】]+/, '').trim();
  t = t.replace(/^(对于|关于|针对|对|就|在|请|麻烦|帮我|想|想要|需要|想了解一下|想咨询)+/, '').trim();
  t = t.replace(/^(?:查询|请问|想了解一下|想了解|想知道)\s*/, '').trim();
  const seps = [
    '有哪些', '有什么', '哪些', '哪一些', '哪类', '请列举', '列举一下', '列举',
    '的测试项目', '的试验项目', '测试项目', '试验项目', '的项目', '的指标',
    '考核指标', '的测试', '的试验', '的方法', '的标准', '是什么', '怎样', '如何', '吗', '呢', '？', '?',
  ];
  for (const sep of seps) {
    if (t.includes(sep)) {
      t = t.split(sep)[0].trim();
      break;
    }
  }
  t = t.replace(/^[请问一下：:，,。.「」【】]+/, '').trim();
  t = t.replace(/(查询|了解|知道|告诉|说说|介绍一下|帮我|麻烦)$/, '').trim();
  const m = t.match(/^[\u4e00-\u9fff]{1,10}/);
  if (m) return m[0].trim();
  return t.slice(0, 32).trim();
}

/** 非「方法」类问题时：每个设备行下勾选全部项目（含分类下子项）与全部指标，便于方法区展示全集 */
function selectAllZnwdTableSelection(envRows: EnvironmentTableRow[]): {
  projects: SelectedProjectEntry[];
  indicators: SelectedIndicatorEntry[];
} {
  const projects: SelectedProjectEntry[] = [];
  const indicators: SelectedIndicatorEntry[] = [];

  for (const row of envRows) {
    const eqId = row.equipmentTypeId;
    for (const envName of ENVIRONMENT_ORDER) {
      const plist = row.projectsByEnv[envName] || [];
      for (const node of plist) {
        if (node.type === 'category') {
          projects.push({
            projectId: node.id,
            equipmentTypeId: eqId,
            categoryId: node.id,
            nodeData: node,
          });
          for (const ch of node.children || []) {
            projects.push({
              projectId: ch.id,
              equipmentTypeId: eqId,
              categoryId: node.id,
              nodeData: ch,
            });
          }
        } else {
          projects.push({
            projectId: node.id,
            equipmentTypeId: eqId,
            nodeData: node,
          });
        }
      }
    }
    for (const ind of row.indicators || []) {
      indicators.push({
        indicatorId: ind.id,
        equipmentTypeId: eqId,
        nodeData: ind,
      });
    }
  }
  return { projects, indicators };
}

function findPrevUserQuestionForAiMessage(messages: ChatMessage[], aiMsgId: string): string {
  const idx = messages.findIndex((m) => m.id === aiMsgId);
  for (let i = (idx >= 0 ? idx : messages.length) - 1; i >= 0; i--) {
    if (messages[i]?.role === 'user') return String(messages[i].content || '').trim();
  }
  return '';
}

function normalizeNodeLabels(labels: Neo4jNodeResult['labels']): string[] {
  if (Array.isArray(labels)) return labels;
  if (typeof labels === 'string') return labels.split(',').map((x) => x.trim()).filter(Boolean);
  return [];
}

function pickNodeId(node: Neo4jNodeResult, fallbackIndex: number): string {
  const props = node.properties || {};
  return String(
    props.Id ??
    props.id ??
    props.elementId ??
    props.uid ??
    props.uuid ??
    props.code ??
    props.name ??
    props.title ??
    `node_${fallbackIndex}`
  );
}

function mapNeo4jToGraphData(rawNodes: Neo4jNodeResult[]): GraphData {
  const nodes = flattenZnwdNeoHitsForGraph(rawNodes as any[]);
  const graphNodes: GraphNode[] = nodes
    .filter((node) => node && typeof node === 'object' && !('error' in (node as any)))
    .map((node, index) => {
    const labels = normalizeNodeLabels(node.labels);
    const props = node.properties || {};
    const id = pickNodeId(node, index);
    const label = String(props.Name ?? props.name ?? props.Label ?? props.title ?? props.code ?? props.Id ?? id);
    const group = labels[0] || 'Node';
    return {
      id,
      label,
      group,
      title: labels.join(', '),
      properties: { ...props, __labels: labels },
      value: 18,
    };
  });
  return { nodes: graphNodes, edges: [] };
}

function ChromaResultModal({ open, onClose, standards, selectedSource }: { open: boolean; onClose: () => void; standards: any[]; selectedSource?: any }) {
  return (
    <Modal title="文件检索结果" open={open} onCancel={onClose} footer={null} width={800}>
      {standards.map((std) => {
        const isSelected = selectedSource && 
          (selectedSource.doc_id === std.doc_id || selectedSource.title === std.title);
        
        return (
          <div key={std.doc_id} style={{ 
            marginBottom: 16, 
            padding: 12, 
            background: isSelected ? '#e6f7ff' : '#fafafa', 
            borderRadius: 8,
            border: isSelected ? '1px solid #1890ff' : '1px solid transparent'
          }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <div>
                <Tag color="green">{std.doc_id}</Tag>
                <Text strong style={{ marginLeft: 8 }}>{std.title}</Text>
                <Text type="secondary" style={{ marginLeft: 8 }}>({std.chunk_count} 个片段)</Text>
              </div>
              {isSelected && <Tag color="blue">当前选中</Tag>}
            </div>
            <div style={{ marginTop: 8, fontSize: 13, maxHeight: 400, overflow: 'auto' }}>
              <pre style={{ margin: 0, whiteSpace: 'pre-wrap', wordBreak: 'break-word', fontFamily: 'inherit', fontSize: 13 }}>
                {cleanContent(std.content || '')}
              </pre>
            </div>
          </div>
        );
      })}
    </Modal>
  );
}

const ZNWD_THINKING_TEXT_COLOR = '#999';
const ZNWD_THINKING_FONT_SIZE = 12;

function ThinkingProcessPanel({ process }: { process: ThinkingProcess }) {
  const [collapsed, setCollapsed] = useState(false);
  const contentRef = useRef<HTMLDivElement>(null);
  
  useEffect(() => {
    if (contentRef.current && !collapsed) {
      contentRef.current.scrollTop = contentRef.current.scrollHeight;
    }
  }, [process.content, collapsed]);
  
  if (!process || !process.content) return null;
  
  return (
    <div style={{ marginBottom: 16 }}>
      <div 
        style={{ 
          display: 'flex', 
          alignItems: 'center', 
          justifyContent: 'space-between',
          cursor: 'pointer',
          padding: '8px 12px',
          background: '#f6f8fa',
          borderRadius: 8,
          border: '1px solid #e1e4e8'
        }}
        onClick={() => setCollapsed(!collapsed)}
      >
        <Text strong style={{ fontSize: 13 }}>
          {collapsed ? '▶' : '▼'} 思考过程
        </Text>
        <Text type="secondary" style={{ fontSize: 12 }}>
          {process.isComplete ? '已完成' : '思考中...'}
        </Text>
      </div>
      
      {!collapsed && (
        <div 
          ref={contentRef}
          style={{ 
            padding: 12, 
            background: '#fafafa', 
            borderRadius: '0 0 8px 8px',
            border: '1px solid #e1e4e8',
            borderTop: 'none',
            fontSize: ZNWD_THINKING_FONT_SIZE,
            lineHeight: 1.55,
            overflowY: 'auto',
            color: ZNWD_THINKING_TEXT_COLOR,
          }}
        >
          <Paragraph
            style={{
              marginBottom: 0,
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-word',
              fontSize: ZNWD_THINKING_FONT_SIZE,
              color: ZNWD_THINKING_TEXT_COLOR,
            }}
          >
            {znwdSanitizeThinkingBodyForDisplay(process.content, process.isComplete)}
          </Paragraph>
        </div>
      )}
    </div>
  );
}

function formatSourceFileLabel(ff?: string): string {
  const f = (ff || 'other').toLowerCase();
  if (f === 'pdf') return 'PDF';
  if (f === 'docx') return 'Word（DOCX）';
  if (f === 'doc') return 'Word（DOC）';
  return '其他';
}

function resolveChromaPreviewUrl(item: ChromaChunkResult): string {
  const raw = String(item.source_file || item.minio_path || '').trim();
  if (!raw) return '';
  if (/^https?:\/\//i.test(raw)) return raw;
  const low = raw.toLowerCase();
  if (low.startsWith('minio://')) {
    return `/api/znwd/file/preview?url=${encodeURIComponent(raw)}`;
  }
  if (/\.(pdf|docx|doc)$/i.test(raw)) {
    return `/api/znwd/file/preview?path=${encodeURIComponent(raw)}`;
  }
  return '';
}

function FilePreviewPane({ item }: { item: ChromaChunkResult }) {
  const rawPath = String(item.minio_path || item.source_file || '').trim();
  const previewUrl = resolveChromaPreviewUrl(item);
  const fmt = (item.file_format || '').toLowerCase();
  const isPdf =
    fmt === 'pdf' || /\.pdf($|\?)/i.test(rawPath) || /\.pdf($|\?)/i.test(previewUrl);
  const isDocx =
    fmt === 'docx' ||
    /\.docx($|\?)/i.test(rawPath) ||
    /\.docx($|\?)/i.test(previewUrl);
  const isDoc =
    fmt === 'doc' || /\.doc($|\?)/i.test(rawPath) || /\.doc($|\?)/i.test(previewUrl);
  const canEmbed =
    !!previewUrl && (/^https?:\/\//i.test(previewUrl) || previewUrl.startsWith('/api/'));

  const snippet = (item.content || '').trim();

  return (
    <div style={{ maxHeight: 640, overflowY: 'auto', padding: 8, background: '#fafafa', borderRadius: 6 }}>
      {item.source === 'znwd6_deferred_doc' ? (
        <Tag color="blue" style={{ marginBottom: 8 }}>
          ZNWD6 延迟文档检索
        </Tag>
      ) : null}
      {isPdf && canEmbed && previewUrl ? (
        <div style={{ marginBottom: 16 }}>
          <Text strong style={{ display: 'block', marginBottom: 8 }}>PDF 预览</Text>
          <iframe
            src={previewUrl}
            style={{
              width: '100%',
              height: 520,
              border: '1px solid #e8e8e8',
              borderRadius: 8,
              background: '#fff',
            }}
            title="PDF 预览"
          />
        </div>
      ) : isPdf && !previewUrl ? (
        <Text type="warning" style={{ display: 'block', marginBottom: 12 }}>
          未解析到可预览的 PDF 地址（需元数据中带 minio:// 或对象路径）。
        </Text>
      ) : null}
      <Descriptions column={1} size="small" bordered title="原始文件（向量库元数据）">
        <Descriptions.Item label="标题">{item.title || '—'}</Descriptions.Item>
        <Descriptions.Item label="文档 ID">{item.doc_id || '—'}</Descriptions.Item>
        <Descriptions.Item label="文件格式">{formatSourceFileLabel(item.file_format)}</Descriptions.Item>
        <Descriptions.Item label="MinIO 路径">
          <Text copyable={!!rawPath} style={{ wordBreak: 'break-all' }}>
            {rawPath || '—'}
          </Text>
        </Descriptions.Item>
      </Descriptions>
      {isDocx && canEmbed && previewUrl ? (
        <div style={{ marginTop: 12 }} key={previewUrl}>
          <Text strong style={{ display: 'block', marginBottom: 8 }}>Word 预览（本地 docx-preview / mammoth）</Text>
          <LocalDocxPreview fileUrl={previewUrl} reloadKey={0} />
        </div>
      ) : null}
      {!isPdf && !isDocx && isDoc ? (
        <Text type="secondary" style={{ display: 'block', marginTop: 12 }}>
          当前为 Word（.doc）或未提供可嵌入预览地址，请从 MinIO 路径下载后查看。
        </Text>
      ) : null}
      {!isPdf && !isDocx && !isDoc && rawPath ? (
        <Text type="secondary" style={{ display: 'block', marginTop: 12 }}>
          未识别为 PDF/Word 嵌入预览；路径见上。
        </Text>
      ) : null}
      {snippet ? (
        <Collapse
          style={{ marginTop: 12 }}
          items={[
            {
              key: 'snippet',
              label: '片段原文（纯文本，非 Markdown）',
              children: (
                <pre
                  style={{
                    margin: 0,
                    whiteSpace: 'pre-wrap',
                    wordBreak: 'break-word',
                    fontSize: 12,
                    maxHeight: 220,
                    overflow: 'auto',
                  }}
                >
                  {cleanContent(snippet)}
                </pre>
              ),
            },
          ]}
        />
      ) : null}
    </div>
  );
}


function ThinkingPanel({ steps }: { steps: ThinkingStep[] }) {
  const [neo4jModalOpen, setNeo4jModalOpen] = useState(false);
  const [chromaModalOpen, setChromaModalOpen] = useState(false);
  const [selectedSource, setSelectedSource] = useState<any>(null);

  if (!steps || steps.length === 0) return null;

  const neo4jStep = steps.find(s => s.type === 'neo4j_search');
  const chromaStep = steps.find(s => s.type === 'chroma_search');

  const handleSourceClick = (source: any, type: 'neo4j' | 'chroma') => {
    setSelectedSource({ ...source, type });
    if (type === 'neo4j') {
      setNeo4jModalOpen(true);
    } else {
      setChromaModalOpen(true);
    }
  };

  const collapseItems = steps.map((step, idx) => ({
    key: String(idx),
    label: (
      <span style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        {getStepIcon(step.type, step.status)}
        <Text strong>{getStepLabel(step.type)}</Text>
        <Text type="secondary" style={{ fontSize: 12 }}>{step.message}</Text>
      </span>
    ),
    children: (
      <div style={{ padding: '4px 0', fontSize: 13 }}>
        {step.type === 'neo4j_search' && step.hits !== undefined && (
          <div>
            <Tag color="blue">命中 {step.hits} 个节点</Tag>
            {step.sample && step.sample.length > 0 && (
              <div style={{ marginTop: 8 }}>
                <Text type="secondary">示例节点：</Text>
                {step.sample.map((s, i) => (
                  <div key={i} style={{ marginLeft: 12, color: '#666' }}>{s}</div>
                ))}
              </div>
            )}
            {step.results && step.results.length > 0 && (
              <div style={{ marginTop: 8 }}>
                <Text type="secondary">信息来源：</Text>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginTop: 4 }}>
                  {step.results.slice(0, 5).map((result, i) => (
                    <Tag 
                      key={i} 
                      color="blue" 
                      style={{ cursor: 'pointer' }}
                      onClick={() => handleSourceClick(result, 'neo4j')}
                    >
                      {result.labels?.[0] || '节点'} {i + 1}
                    </Tag>
                  ))}
                  {step.results.length > 5 && (
                    <Tag color="blue">+{step.results.length - 5} 更多</Tag>
                  )}
                </div>
                {step.results.length > 3 && (
                  <Button
                    type="link" size="small" icon={<EyeOutlined />}
                    style={{ padding: 0, marginTop: 4 }}
                    onClick={() => setNeo4jModalOpen(true)}
                  >
                    查看全部 {step.results.length} 个节点
                  </Button>
                )}
              </div>
            )}
          </div>
        )}
        {step.type === 'chroma_search' && step.chunks !== undefined && (
          <div>
            <Tag color="green">找到 {step.chunks} 个匹配片段</Tag>
            {step.sources && step.sources.length > 0 && (
              <div style={{ marginTop: 8 }}>
                <Text type="secondary">信息来源：</Text>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginTop: 4 }}>
                  {step.sources.slice(0, 5).map((s, i) => (
                    <Tag 
                      key={i} 
                      color="green" 
                      style={{ cursor: 'pointer' }}
                      onClick={() => handleSourceClick({ title: s }, 'chroma')}
                    >
                      {s}
                    </Tag>
                  ))}
                  {step.sources.length > 5 && (
                    <Tag color="green">+{step.sources.length - 5} 更多</Tag>
                  )}
                </div>
              </div>
            )}
            {step.matchedChunks && step.matchedChunks.length > 0 && (
              <div style={{ marginTop: 8 }}>
                <Text type="secondary">匹配片段预览：</Text>
                {step.matchedChunks.slice(0, 3).map((c, i) => (
                  <div key={i} style={{
                    marginLeft: 12, marginTop: 4, padding: 6,
                    background: '#fff', borderRadius: 4, border: '1px solid #f0f0f0',
                    cursor: 'pointer'
                  }}
                  onClick={() => handleSourceClick(c, 'chroma')}
                  >
                    <Tag color="geekblue">{c.doc_id}</Tag>
                    <Text type="secondary" style={{ fontSize: 11 }}>
                      距离: {c.distance?.toFixed(4)}
                    </Text>
                    <div style={{ fontSize: 12, color: '#555', marginTop: 2 }}>
                      {cleanContent(c.preview)}
                    </div>
                  </div>
                ))}
              </div>
            )}
            {step.fullStandards && step.fullStandards.length > 0 && (
              <Button
                type="link" size="small" icon={<EyeOutlined />}
                style={{ padding: 0, marginTop: 4 }}
                onClick={() => setChromaModalOpen(true)}
              >
                查看 {step.fullStandards.length} 个标准完整内容
              </Button>
            )}
          </div>
        )}
        {step.type === 'fusion' && (
          <div>
            <Tag color="purple">清单: {step.neo4jHits ?? 0} 条</Tag>
            <Tag color="cyan">文档: {step.chromaHits ?? 0} 份</Tag>
            <Tag color="orange">总计: {step.totalHits ?? 0} 条</Tag>
          </div>
        )}
        {step.type === 'generating' && (
          <Text type="secondary">
            {step.status === 'loading' ? '正在调用大语言模型生成回答...' : '答案生成完毕'}
          </Text>
        )}
      </div>
    ),
  }));

  return (
    <>
      <Collapse
        size="small" ghost
        items={collapseItems}
        style={{ marginTop: 8, background: '#fafafa', borderRadius: 8 }}
      />
      <Neo4jResultModal
        open={neo4jModalOpen}
        onClose={() => {
          setNeo4jModalOpen(false);
          setSelectedSource(null);
        }}
        results={neo4jStep?.results || []}
        selectedSource={selectedSource?.type === 'neo4j' ? selectedSource : null}
      />
      <ChromaResultModal
        open={chromaModalOpen}
        onClose={() => {
          setChromaModalOpen(false);
          setSelectedSource(null);
        }}
        standards={chromaStep?.fullStandards || []}
        selectedSource={selectedSource?.type === 'chroma' ? selectedSource : null}
      />
    </>
  );
}

async function copyTextToClipboard(text: string): Promise<boolean> {
  const t = (text || '').trim();
  if (!t) return false;
  try {
    await navigator.clipboard.writeText(t);
    return true;
  } catch {
    try {
      const ta = document.createElement('textarea');
      ta.value = t;
      ta.style.position = 'fixed';
      ta.style.left = '-9999px';
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      document.body.removeChild(ta);
      return true;
    } catch {
      return false;
    }
  }
}

function ZnwdMessageCopyButton({ text }: { text: string }) {
  const handleCopy = async () => {
    const ok = await copyTextToClipboard(text);
    if (ok) antMessage.success('已复制');
    else antMessage.error('复制失败');
  };
  return (
    <Button
      type="link"
      size="small"
      icon={<CopyOutlined />}
      style={{ padding: 0, fontSize: 13, height: 'auto' }}
      onClick={() => void handleCopy()}
    >
      复制
    </Button>
  );
}

export default function ZNWDPage() {
  const router = useRouter();
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [history, setHistory] = useState<HistoryItem[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [siderCollapsed, setSiderCollapsed] = useState(false);
  const [inputValue, setInputValue] = useState('');
  const [sessionId, setSessionId] = useState<string>('');
  const [, setAnswerTimerTick] = useState(0);
  const [sourceDrawerOpen, setSourceDrawerOpen] = useState(false);
  const [sourceDrawerFileTabKey, setSourceDrawerFileTabKey] = useState<string>('');
  const [sourceDrawerPrimary, setSourceDrawerPrimary] = useState<PrimaryChromaSource | null>(null);
  const [activeSourceData, setActiveSourceData] = useState<MessageSourceData | null>(null);
  /** 打开抽屉时的原始 Chroma 列表（过滤后可能为空，仍应用来展示「有检索」与兜底预览） */
  const [sourceDrawerChromaRaw, setSourceDrawerChromaRaw] = useState<ChromaChunkResult[]>([]);
  const [sourceTableRows, setSourceTableRows] = useState<EnvironmentTableRow[]>([]);
  const [sourceViewMode, setSourceViewMode] = useState<'equipment_table' | 'environment' | 'equipment_hierarchy'>('equipment_table');
  const [equipmentHierarchyRows, setEquipmentHierarchyRows] = useState<ZnwdEquipmentHierarchyRow[]>([]);
  const [sourceHighlightEquipmentIds, setSourceHighlightEquipmentIds] = useState<string[]>([]);
  const [sourceHighlightLevel2TypeIds, setSourceHighlightLevel2TypeIds] = useState<string[]>([]);
  const [sourceHighlightProjectIds, setSourceHighlightProjectIds] = useState<string[]>([]);
  const [sourceHighlightIndicatorIds, setSourceHighlightIndicatorIds] = useState<string[]>([]);
  const [environmentSources, setEnvironmentSources] = useState<ZnwdEnvironmentSourceRow[]>([]);
  const [expandedEnvSourceKey, setExpandedEnvSourceKey] = useState<string | null>(null);
  const [sourceTableLoading, setSourceTableLoading] = useState(false);
  /** 与本轮用户问法一致：涉及「方法」时用单选+按服务端 hints；否则多选全选项目/指标 */
  const [sourceTableInvolvesMethod, setSourceTableInvolvesMethod] = useState(false);
  const [selectedProjectsInSource, setSelectedProjectsInSource] = useState<SelectedProjectEntry[]>([]);
  const [selectedIndicatorsInSource, setSelectedIndicatorsInSource] = useState<SelectedIndicatorEntry[]>([]);
  const [sourceMethods, setSourceMethods] = useState<MethodNode[]>([]);
  const [selectedMethodIdsInSource, setSelectedMethodIdsInSource] = useState<string[]>([]);
  const [graphData, setGraphData] = useState<GraphData>({ nodes: [], edges: [] });
  const [graphRefreshKey, setGraphRefreshKey] = useState(0);
  const [nodeInfoModalOpen, setNodeInfoModalOpen] = useState(false);
  const [activeNodeInfo, setActiveNodeInfo] = useState<GraphNode | null>(null);
  const [exploringNeighbors, setExploringNeighbors] = useState(false);
  const [llmModels, setLlmModels] = useState<LlmModelOption[]>([]);
  const [selectedLlmModelKey, setSelectedLlmModelKey] = useState<string>('');
  const [interestDrawerOpen, setInterestDrawerOpen] = useState(false);
  const [interestEquipOptions, setInterestEquipOptions] = useState<InterestOption[]>([]);
  const [interestEnvOptions, setInterestEnvOptions] = useState<InterestOption[]>([]);
  const [interestEquipSelected, setInterestEquipSelected] = useState<string[]>([]);
  const [interestEnvSelected, setInterestEnvSelected] = useState<string[]>([]);
  const [interestLoadOptions, setInterestLoadOptions] = useState(false);
  const [interestSaving, setInterestSaving] = useState(false);
  const [appearanceDrawerOpen, setAppearanceDrawerOpen] = useState(false);
  const { appearance, updateAppearance } = useZnwdAppearanceState();
  const chatEndRef = useRef<HTMLDivElement>(null);
  const chatContainerRef = useRef<HTMLDivElement>(null);
  const aiMsgIdRef = useRef<string>('');
  const znwdStreamHandlerRef = useRef<((event: { type: string; data: unknown }) => void) | null>(null);
  const streamTimingRef = useRef({ t0: 0, firstLogged: false });
  const sourceHydratePayloadRef = useRef<{
    keyword: string;
    neo4jHits: Neo4jNodeResult[];
    userQuestion?: string;
  } | null>(null);
  const prefetchInflightByMsgRef = useRef<Map<string, Promise<ZnwdCachedSourceTable | null>>>(new Map());
  const messagesRef = useRef<ChatMessage[]>([]);
  /** 流式对话：默认 ZNWD3；回退旧版在 .env 设 NEXT_PUBLIC_ZNWD_CHAT_STREAM=/api/znwd1/chat/stream */
  const znwdStreamEndpoint =
    process.env.NEXT_PUBLIC_ZNWD_CHAT_STREAM || '/api/znwd6/chat/stream';
  const isZnwd1Stream = /znwd1/i.test(znwdStreamEndpoint);
  const isZnwd3Stream = /znwd3/i.test(znwdStreamEndpoint);
  const isZnwd4Stream = /znwd4/i.test(znwdStreamEndpoint);
  const isZnwd5Stream = /znwd5/i.test(znwdStreamEndpoint);
  const isZnwd6Stream = /znwd6/i.test(znwdStreamEndpoint);
  /** ZNWD4/5/6 共用：分步思考、来源表格、评价反馈、新增试验方法 */
  const isZnwd45Stream = isZnwd4Stream || isZnwd5Stream || isZnwd6Stream;
  const znwdSourceTableApi = isZnwd1Stream
    ? '/api/znwd1/source/table'
    : isZnwd4Stream
      ? '/api/znwd4/source/table'
      : isZnwd5Stream
        ? '/api/znwd5/source/table'
        : isZnwd6Stream
          ? '/api/znwd6/source/table'
          : isZnwd3Stream
            ? '/api/znwd3/source/table'
            : '/api/znwd/source/table';
  const znwdFeedbackApi = isZnwd6Stream
    ? '/api/znwd6/feedback'
    : isZnwd5Stream
      ? '/api/znwd5/feedback'
      : isZnwd4Stream
        ? '/api/znwd4/feedback'
        : '/api/znwd4/feedback';
  const { sendMessage, stopGeneration, isGenerating } = useSSE(znwdStreamEndpoint);
  const { sendMessage: sendGuidedExecute } = useSSE('/api/znwd6/guided/execute');

  useEffect(() => { loadHistory(); }, []);

  useEffect(() => {
    messagesRef.current = messages;
  }, [messages]);

  useEffect(() => {
    if (!isGenerating) return;
    const id = window.setInterval(() => setAnswerTimerTick((t) => t + 1), 500);
    return () => window.clearInterval(id);
  }, [isGenerating]);

  useEffect(() => {
    if (typeof window === 'undefined') return;
    let u = localStorage.getItem('userId');
    if (!u) {
      u =
        typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
          ? `u-${crypto.randomUUID()}`
          : `u-${Date.now()}-${Math.random().toString(36).slice(2, 11)}`;
      localStorage.setItem('userId', u);
    }
  }, []);
  useEffect(() => {
    const fetchLlmModels = async () => {
      try {
        const res = await fetch('/api/xtgl/mxgl/llm-models?active_only=1');
        if (!res.ok) return;
        const data = await res.json();
        const list: LlmModelOption[] = Array.isArray(data?.data) ? data.data : [];
        setLlmModels(list);
        if (!list.length) return;
        const stored =
          typeof window !== 'undefined' ? localStorage.getItem(ZNWD_LLM_STORAGE_KEY) : null;
        const pick =
          stored && list.some((m) => m.model_key === stored)
            ? stored
            : list[0].model_key;
        setSelectedLlmModelKey(pick);
        localStorage.setItem(ZNWD_LLM_STORAGE_KEY, pick);
      } catch (error) {
        console.error('加载LLM模型列表失败:', error);
      }
    };
    fetchLlmModels();
  }, []);

  useEffect(() => {
    const stored = localStorage.getItem('znwd_session_id');
    if (stored) {
      setSessionId(stored);
    } else {
      const newId = `session-${Date.now()}`;
      setSessionId(newId);
      localStorage.setItem('znwd_session_id', newId);
    }
  }, []);
  
  const scrollToBottom = useCallback(() => {
    if (chatContainerRef.current) {
      chatContainerRef.current.scrollTop = chatContainerRef.current.scrollHeight;
    }
  }, []);
  
  const loadInterestUi = useCallback(async () => {
    if (typeof window === 'undefined') return;
    const userId = localStorage.getItem('userId') || 'anonymous';
    setInterestLoadOptions(true);
    try {
      const [oRes, cRes] = await Promise.all([
        fetch('/api/znwd/user/interest/options'),
        fetch(`/api/znwd/user/interest?userId=${encodeURIComponent(userId)}`),
      ]);
      const oj = oRes.ok
        ? await oRes.json()
        : { equipmentTypes: [], equipments: [], environments: [] };
      const cj = cRes.ok
        ? await cRes.json()
        : { equipmentTypeIds: [], equipmentIds: [], environmentIds: [] };
      const typeOpts = Array.isArray(oj.equipmentTypes)
        ? oj.equipmentTypes
        : Array.isArray(oj.equipments)
          ? oj.equipments
          : [];
      setInterestEquipOptions(typeOpts);
      setInterestEnvOptions(Array.isArray(oj.environments) ? oj.environments : []);
      const savedTypes = Array.isArray(cj.equipmentTypeIds)
        ? cj.equipmentTypeIds
        : Array.isArray(cj.equipmentIds)
          ? cj.equipmentIds
          : [];
      setInterestEquipSelected(savedTypes.map(String));
      setInterestEnvSelected(Array.isArray(cj.environmentIds) ? cj.environmentIds.map(String) : []);
    } catch (e) {
      console.error(e);
      antMessage.error('加载兴趣配置失败');
    } finally {
      setInterestLoadOptions(false);
    }
  }, []);

  useEffect(() => {
    scrollToBottom();
  }, [messages, scrollToBottom]);

  useEffect(() => {
    if (!sourceDrawerOpen) return;
    const hideKnowledgeGraphDrawers = () => {
      document.querySelectorAll('.ant-drawer').forEach((drawer) => {
        const title = drawer.querySelector('.ant-drawer-title')?.textContent?.trim();
        if (title === '节点详情' || title === '探索配置') {
          (drawer as HTMLElement).style.display = 'none';
        }
      });
    };
    hideKnowledgeGraphDrawers();
    const observer = new MutationObserver(hideKnowledgeGraphDrawers);
    observer.observe(document.body, { childList: true, subtree: true });
    return () => observer.disconnect();
  }, [sourceDrawerOpen]);

  const fetchSourceMethods = useCallback(async (projectIds: string[], indicatorIds: string[]) => {
    try {
      const url = new URL('/api/GLCX/SSYQ', window.location.origin);
      url.searchParams.set('path', '/methods');
      const res = await fetch(url.toString(), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ projectIds, indicatorIds }),
      });
      const data = await res.json();
      const methods: MethodNode[] = Array.isArray(data?.data) ? data.data : [];
      setSourceMethods(methods);
      return methods;
    } catch (e) {
      console.error('加载方法失败', e);
      setSourceMethods([]);
      return [];
    }
  }, []);

  const buildSourceTableCacheFromResponse = useCallback(
    (
      json: { success?: boolean; data?: Record<string, unknown> },
      payload: { keyword: string; userQuestion?: string; neo4jHits?: Neo4jNodeResult[] },
    ): ZnwdCachedSourceTable | null => {
      if (!json?.success || !json?.data) return null;
      const rawView = json.data.sourceView;
      const view =
        rawView === 'environment'
          ? 'environment'
          : rawView === 'equipment_hierarchy'
            ? 'equipment_hierarchy'
            : 'equipment_table';
      const hints = (json.data.selectionHints || {}) as {
        highlightEquipmentIds?: string[];
        highlightLevel2TypeIds?: string[];
        highlightProjectIds?: string[];
        highlightIndicatorIds?: string[];
        selectProjectIds?: string[];
        selectIndicatorIds?: string[];
        selectMethodIds?: string[];
      };
      const emptyHighlights = {
        highlightEquipmentIds: [] as string[],
        highlightLevel2TypeIds: [] as string[],
        highlightProjectIds: [] as string[],
        highlightIndicatorIds: [] as string[],
      };
      const neoFallback = () => {
        if (!znwdQuestionAsksEnvironmentAttributes(payload.userQuestion || '')) return null;
        const fromNeo = buildEnvironmentSourcesFromNeoHits(payload.neo4jHits || []);
        if (!fromNeo.length) return null;
        return {
          envRows: [] as EnvironmentTableRow[],
          environmentSources: fromNeo,
          equipmentHierarchyRows: [] as ZnwdEquipmentHierarchyRow[],
          sourceViewMode: 'environment' as const,
          involvesMethod: false,
          selectedProjects: [] as SelectedProjectEntry[],
          selectedIndicators: [] as SelectedIndicatorEntry[],
          selectedMethodIds: [] as string[],
          keyword: payload.keyword,
          ...emptyHighlights,
        };
      };
      const hierarchyNeoFallback = () => {
        if (!znwdQuestionAsksEquipmentHierarchy(payload.userQuestion || '')) return null;
        const fromNeo = buildEquipmentHierarchyFromNeoHits(payload.neo4jHits || []);
        if (!fromNeo.length) return null;
        return {
          envRows: [] as EnvironmentTableRow[],
          environmentSources: [] as ZnwdEnvironmentSourceRow[],
          equipmentHierarchyRows: fromNeo,
          sourceViewMode: 'equipment_hierarchy' as const,
          involvesMethod: false,
          selectedProjects: [] as SelectedProjectEntry[],
          selectedIndicators: [] as SelectedIndicatorEntry[],
          selectedMethodIds: [] as string[],
          keyword: payload.keyword,
          highlightEquipmentIds: hints.highlightEquipmentIds || [],
          highlightLevel2TypeIds: hints.highlightLevel2TypeIds || [],
          highlightProjectIds: [],
          highlightIndicatorIds: [],
        };
      };
      if (view === 'environment') {
        const apiRows = (json.data.environmentSources || []) as ZnwdEnvironmentSourceRow[];
        if (apiRows.length > 0) {
          return {
            envRows: [],
            environmentSources: apiRows,
            equipmentHierarchyRows: [],
            sourceViewMode: 'environment',
            involvesMethod: false,
            selectedProjects: [],
            selectedIndicators: [],
            selectedMethodIds: [],
            keyword: payload.keyword,
            ...emptyHighlights,
          };
        }
        const fb = neoFallback();
        if (fb) return fb;
        return {
          envRows: [],
          environmentSources: [],
          equipmentHierarchyRows: [],
          sourceViewMode: 'environment',
          involvesMethod: false,
          selectedProjects: [],
          selectedIndicators: [],
          selectedMethodIds: [],
          keyword: payload.keyword,
          ...emptyHighlights,
        };
      }
      if (view === 'equipment_hierarchy') {
        const apiRows = (json.data.equipmentHierarchyRows || []) as ZnwdEquipmentHierarchyRow[];
        if (apiRows.length > 0) {
          return {
            envRows: [],
            environmentSources: [],
            equipmentHierarchyRows: apiRows,
            sourceViewMode: 'equipment_hierarchy',
            involvesMethod: false,
            selectedProjects: [],
            selectedIndicators: [],
            selectedMethodIds: [],
            keyword: payload.keyword,
            highlightEquipmentIds: hints.highlightEquipmentIds || [],
            highlightLevel2TypeIds: hints.highlightLevel2TypeIds || [],
            highlightProjectIds: [],
            highlightIndicatorIds: [],
          };
        }
        const fb = hierarchyNeoFallback();
        if (fb) return fb;
        return {
          envRows: [],
          environmentSources: [],
          equipmentHierarchyRows: [],
          sourceViewMode: 'equipment_hierarchy',
          involvesMethod: false,
          selectedProjects: [],
          selectedIndicators: [],
          selectedMethodIds: [],
          keyword: payload.keyword,
          ...emptyHighlights,
        };
      }
      const envRows = ssyqTableRowsToEnvironmentRows((json.data.tableRows || []) as unknown[]);
      const qInvolves = znwdUserQuestionInvolvesMethod(payload.userQuestion || '');
      const hintsHaveSelection =
        (Array.isArray(hints.selectProjectIds) && hints.selectProjectIds.length > 0) ||
        (Array.isArray(hints.selectIndicatorIds) && hints.selectIndicatorIds.length > 0) ||
        (Array.isArray(hints.selectMethodIds) && hints.selectMethodIds.length > 0);
      const involvesMethod = qInvolves || hintsHaveSelection;
      const useHints = qInvolves || hintsHaveSelection;
      const sel = useHints
        ? applyZnwdSelectionHints(envRows, {
            selectProjectIds: hints.selectProjectIds,
            selectIndicatorIds: hints.selectIndicatorIds,
            selectMethodIds: hints.selectMethodIds,
          })
        : (() => {
            const all = selectAllZnwdTableSelection(envRows);
            return { ...all, methodIds: [] as string[] };
          })();
      const fb = neoFallback();
      if (fb) return fb;
      const hfb = hierarchyNeoFallback();
      if (hfb) return hfb;
      return {
        envRows,
        environmentSources: [],
        equipmentHierarchyRows: [],
        sourceViewMode: 'equipment_table',
        involvesMethod,
        selectedProjects: sel.projects,
        selectedIndicators: sel.indicators,
        selectedMethodIds: sel.methodIds,
        keyword: payload.keyword,
        highlightEquipmentIds: hints.highlightEquipmentIds || [],
        highlightLevel2TypeIds: hints.highlightLevel2TypeIds || [],
        highlightProjectIds: hints.highlightProjectIds || [],
        highlightIndicatorIds: hints.highlightIndicatorIds || [],
      };
    },
    [],
  );

  const applyCachedSourceTableToDrawer = useCallback(
    async (cache: ZnwdCachedSourceTable) => {
      sourceHydratePayloadRef.current = {
        keyword: cache.keyword,
        neo4jHits: [],
        userQuestion: '',
      };
      setSourceViewMode(cache.sourceViewMode);
      setSourceTableInvolvesMethod(cache.involvesMethod);
      setSourceHighlightEquipmentIds(cache.highlightEquipmentIds || []);
      setSourceHighlightLevel2TypeIds(cache.highlightLevel2TypeIds || []);
      setSourceHighlightProjectIds(cache.highlightProjectIds || []);
      setSourceHighlightIndicatorIds(cache.highlightIndicatorIds || []);
      if (cache.sourceViewMode === 'environment') {
        setEnvironmentSources(cache.environmentSources);
        setEquipmentHierarchyRows([]);
        setSourceTableRows([]);
        setSelectedProjectsInSource([]);
        setSelectedIndicatorsInSource([]);
        setSelectedMethodIdsInSource([]);
        setSourceMethods([]);
        return;
      }
      if (cache.sourceViewMode === 'equipment_hierarchy') {
        setEnvironmentSources([]);
        setEquipmentHierarchyRows(cache.equipmentHierarchyRows);
        setSourceTableRows([]);
        setSelectedProjectsInSource([]);
        setSelectedIndicatorsInSource([]);
        setSelectedMethodIdsInSource([]);
        setSourceMethods([]);
        return;
      }
      setEnvironmentSources([]);
      setEquipmentHierarchyRows([]);
      setSourceTableRows(cache.envRows);
      setSelectedProjectsInSource(cache.selectedProjects);
      setSelectedIndicatorsInSource(cache.selectedIndicators);
      setSelectedMethodIdsInSource(cache.selectedMethodIds);
      const pids = Array.from(new Set(cache.selectedProjects.map((x) => x.projectId)));
      const iids = Array.from(new Set(cache.selectedIndicators.map((x) => x.indicatorId)));
      await fetchSourceMethods(pids, iids);
    },
    [fetchSourceMethods],
  );

  const fetchSourceTableCache = useCallback(
    async (payload: { keyword: string; neo4jHits: Neo4jNodeResult[]; userQuestion?: string }) => {
      const res = await fetch(znwdSourceTableApi, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          keyword: payload.keyword,
          ssyqTableKeyword: payload.keyword,
          neo4jHits: payload.neo4jHits,
          userQuestion: payload.userQuestion || '',
        }),
      });
      const json = await res.json();
      if (process.env.NODE_ENV === 'development') {
        const hydrateMs = json?.data?.hydrateMs;
        if (typeof hydrateMs === 'number') {
          console.info(`[ZNWD] source/table hydrateMs=${hydrateMs}`);
        }
      }
      return buildSourceTableCacheFromResponse(json, payload);
    },
    [buildSourceTableCacheFromResponse, znwdSourceTableApi],
  );

  const hydrateZnwdSourceTable = useCallback(
    async (payload: { keyword: string; neo4jHits: Neo4jNodeResult[]; userQuestion?: string }) => {
      sourceHydratePayloadRef.current = payload;
      setSourceTableLoading(true);
      setSourceTableRows([]);
      setEnvironmentSources([]);
      setEquipmentHierarchyRows([]);
      setSourceViewMode('equipment_table');
      try {
        const cache = await fetchSourceTableCache(payload);
        if (!cache) {
          setSourceTableRows([]);
          setEnvironmentSources([]);
          setEquipmentHierarchyRows([]);
          return null;
        }
        await applyCachedSourceTableToDrawer(cache);
        return cache;
      } catch (e) {
        console.error('ZNWD source/table request failed', e);
        setSourceTableRows([]);
        setEquipmentHierarchyRows([]);
        return null;
      } finally {
        setSourceTableLoading(false);
      }
    },
    [applyCachedSourceTableToDrawer, fetchSourceTableCache],
  );

  const prefetchSourceTableForMessage = useCallback(
    async (
      aiMsgId: string,
      override?: { keyword: string; neo4jHits: Neo4jNodeResult[]; userQuestion: string },
    ) => {
      let payload = override;
      if (!payload) {
        const msg = messagesRef.current.find((m) => m.id === aiMsgId);
        if (!msg) return;
        const eff = getEffectiveSourceData(msg);
        const userQ =
          (msg.sourceQuestion || '').trim() || findPrevUserQuestionForAiMessage(messagesRef.current, aiMsgId);
        const kw =
          (msg.tableSearchKeyword || '').trim() || znwdFallbackTableKeywordFromQuestion(userQ);
        payload = {
          keyword: kw,
          neo4jHits: eff.neo4jResults || [],
          userQuestion: userQ,
        };
      }
      if (!payload.keyword.trim() && !(payload.neo4jHits || []).length) {
        return;
      }

      const cacheKey = buildSourceTableCacheKey(payload.keyword, payload.neo4jHits);
      const cached = getGlobalSourceTableCache(cacheKey);
      if (cached) {
        setMessages((prev) =>
          prev.map((m) =>
            m.id === aiMsgId
              ? {
                  ...m,
                  cachedSourceTable: cached,
                  sourceTableLoading: false,
                  sourceTableReady: true,
                }
              : m,
          ),
        );
        return;
      }

      const existingInflight = prefetchInflightByMsgRef.current.get(aiMsgId);
      if (existingInflight) {
        await existingInflight;
        return;
      }

      setMessages((prev) =>
        prev.map((m) =>
          m.id === aiMsgId ? { ...m, sourceTableLoading: true, sourceTableReady: false } : m,
        ),
      );

      const task = (async () => {
        try {
          const cache = await fetchSourceTableCache(payload!);
          if (cache) {
            setGlobalSourceTableCache(cacheKey, cache);
          }
          setMessages((prev) =>
            prev.map((m) =>
              m.id === aiMsgId
                ? {
                    ...m,
                    cachedSourceTable: cache || undefined,
                    sourceTableLoading: false,
                    sourceTableReady: true,
                  }
                : m,
            ),
          );
          return cache;
        } catch (e) {
          console.error('ZNWD prefetch source/table failed', e);
          setMessages((prev) =>
            prev.map((m) =>
              m.id === aiMsgId ? { ...m, sourceTableLoading: false, sourceTableReady: true } : m,
            ),
          );
          return null;
        } finally {
          prefetchInflightByMsgRef.current.delete(aiMsgId);
        }
      })();

      prefetchInflightByMsgRef.current.set(aiMsgId, task);
      await task;
    },
    [fetchSourceTableCache],
  );

  const openSourceDrawerFromMessage = useCallback(
    async (msg: ChatMessage) => {
      let liveMsg = messagesRef.current.find((m) => m.id === msg.id) || msg;
      if (!liveMsg.cachedSourceTable && liveMsg.sourceTableLoading) {
        const inflight = prefetchInflightByMsgRef.current.get(liveMsg.id);
        if (inflight) {
          await inflight;
          liveMsg = messagesRef.current.find((m) => m.id === msg.id) || liveMsg;
        }
      }

      const eff = getEffectiveSourceData(liveMsg);
      const neoRaw = eff.neo4jResults || [];
      const chromaRaw = getChromaRawForDrawer(liveMsg);
      setActiveSourceData({
        neo4jResults: neoRaw,
        chromaResults: filterValidChromaChunks(chromaRaw),
      });
      setGraphData(mapNeo4jToGraphData(neoRaw));
      setSourceDrawerChromaRaw(chromaRaw);
      setSourceDrawerPrimary(liveMsg.primaryChromaSource || null);
      const prefDoc = (liveMsg.primaryChromaSource?.doc_id || '').trim();
      const prefTitle = (liveMsg.primaryChromaSource?.title || '').trim();
      let tabKey = '';
      const hitIdx = chromaRaw.findIndex(
        (c) =>
          (prefDoc && String(c.doc_id || '') === prefDoc) ||
          (prefTitle && String(c.title || '') === prefTitle) ||
          (prefTitle && String(c.title || '').includes(prefTitle)),
      );
      if (hitIdx >= 0) {
        const c = chromaRaw[hitIdx];
        tabKey = `${String(c.doc_id || 'noid')}::__${hitIdx}`;
      }
      setSourceDrawerFileTabKey(tabKey);
      setSelectedProjectsInSource([]);
      setSelectedIndicatorsInSource([]);
      setSourceMethods([]);
      setSelectedMethodIdsInSource([]);
      setGraphRefreshKey((k) => k + 1);
      setSourceDrawerOpen(true);
      const userQ =
        (liveMsg.sourceQuestion || '').trim() || findPrevUserQuestionForAiMessage(messagesRef.current, liveMsg.id);
      if (liveMsg.cachedSourceTable) {
        sourceHydratePayloadRef.current = {
          keyword: liveMsg.cachedSourceTable.keyword,
          neo4jHits: neoRaw,
          userQuestion: userQ,
        };
        await applyCachedSourceTableToDrawer(liveMsg.cachedSourceTable);
        return;
      }
      const kw = (liveMsg.tableSearchKeyword || '').trim();
      const effectiveKeyword = kw || znwdFallbackTableKeywordFromQuestion(userQ);
      const cache = await hydrateZnwdSourceTable({
        keyword: effectiveKeyword,
        neo4jHits: neoRaw,
        userQuestion: userQ,
      });
      if (cache) {
        const cacheKey = buildSourceTableCacheKey(effectiveKeyword, neoRaw);
        setGlobalSourceTableCache(cacheKey, cache);
        setMessages((prev) =>
          prev.map((m) =>
            m.id === liveMsg.id ? { ...m, cachedSourceTable: cache, sourceTableReady: true } : m,
          ),
        );
      }
    },
    [applyCachedSourceTableToDrawer, hydrateZnwdSourceTable],
  );

  const handleGraphNodeClick = (node: GraphNode) => {
    setActiveNodeInfo(node);
    setNodeInfoModalOpen(true);
  };

  const handleExploreNeighbors = async () => {
    if (!activeNodeInfo) return;
    setExploringNeighbors(true);
    try {
      const res = await fetch('/api/znwd/graph/neighbors', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          nodeId: activeNodeInfo.id,
          elementId: activeNodeInfo.properties?.__elementId || '',
          labels: activeNodeInfo.properties?.__labels || [],
          properties: activeNodeInfo.properties || {},
          limit: 20,
        }),
      });
      if (!res.ok) return;
      const data = await res.json();
      if (data?.nodes?.length) {
        setGraphData(data as GraphData);
        antMessage.success(`已加载 ${data.nodes.length} 个相邻节点`);
        setGraphRefreshKey((k) => k + 1);
        setNodeInfoModalOpen(false);
      } else {
        antMessage.info('未找到相邻节点');
      }
    } catch (error) {
      console.error('Explore neighbors failed:', error);
      antMessage.error('查询邻居节点失败');
    } finally {
      setExploringNeighbors(false);
    }
  };

  const handleRefreshGraph = () => {
    if (!activeSourceData) return;
    setGraphData(mapNeo4jToGraphData(activeSourceData.neo4jResults || []));
    const p = sourceHydratePayloadRef.current;
    if (p) void hydrateZnwdSourceTable(p);
    else {
      void hydrateZnwdSourceTable({
        keyword: '',
        neo4jHits: activeSourceData.neo4jResults || [],
        userQuestion: '',
      });
    }
    setGraphRefreshKey((k) => k + 1);
  };

  const handleClearSourceSelection = useCallback(() => {
    setSelectedProjectsInSource([]);
    setSelectedIndicatorsInSource([]);
    setSelectedMethodIdsInSource([]);
    void fetchSourceMethods([], []);
  }, [fetchSourceMethods]);

  const loadHistory = async () => {
    setHistoryLoading(true);
    try {
      const res = await fetch('/api/znwd/history');
      const data = await res.json();
      setHistory(data.items || []);
    } catch (err) {
      console.error('Load history failed:', err);
    } finally {
      setHistoryLoading(false);
    }
  };

  const persistConversation = useCallback(
    async (msgs: ChatMessage[]) => {
      const validMessages = msgs.filter((m) => {
        if (m.role === 'user') return m.content.trim().length > 0;
        if (m.role === 'ai') return (m.answerContent || m.content || '').trim().length > 0;
        return false;
      });
      if (validMessages.length === 0) return;
      try {
        const userId =
          typeof window !== 'undefined' ? localStorage.getItem('userId') || 'anonymous' : 'anonymous';
        const userName =
          typeof window !== 'undefined' ? localStorage.getItem('userName') || '匿名用户' : '匿名用户';
        const sid =
          sessionId ||
          (typeof window !== 'undefined' ? localStorage.getItem('znwd_session_id') || '' : '');
        await fetch('/api/znwd/chat/save', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            sessionId: sid,
            userId,
            userName,
            messages: serializeChatMessagesForSave(validMessages),
          }),
        });
        void loadHistory();
      } catch (err) {
        console.error('Persist conversation failed:', err);
      }
    },
    [sessionId, loadHistory]
  );

  const handleHistorySelect = async (id: number) => {
    try {
      const res = await fetch(`/api/znwd/history/${id}`);
      const data = await res.json();
      if (data.messages && data.messages.length > 0) {
        const loadedMessages: ChatMessage[] = data.messages.map((msg: any, index: number) => {
          if (msg.role === 'user') {
            return {
              id: `user-${id}-${index}`,
              role: 'user',
              content: msg.content
            };
          }

          let prevUserQuestion = '';
          for (let j = index - 1; j >= 0; j--) {
            const p = data.messages[j];
            if (p?.role === 'user' && p?.content) {
              prevUserQuestion = String(p.content);
              break;
            }
          }

          let thinkingProcess: ThinkingProcess | undefined;
          if (msg.thinkingProcess && msg.thinkingProcess.content) {
            thinkingProcess = {
              content: msg.thinkingProcess.content,
              isComplete: msg.thinkingProcess.isComplete || true
            };
          } else if (msg.thinkingSteps && msg.thinkingSteps.length > 0) {
            const thinkingContent = msg.thinkingSteps
              .filter((step: any) => step.type === 'thinking' || step.type === 'generating' || step.type === 'thinking_complete')
              .map((step: any) => step.message)
              .join('\n');
            thinkingProcess = {
              content: thinkingContent || '',
              isComplete: true
            };
          }

          const neo4jStep = (msg.thinkingSteps || []).find((s: any) => s.type === 'neo4j_search');
          const chromaStep = (msg.thinkingSteps || []).find((s: any) => s.type === 'chroma_search');
          const sourceFromMsg =
            msg.sourceData && typeof msg.sourceData === 'object'
              ? (msg.sourceData as MessageSourceData)
              : null;
          const sourceData: MessageSourceData =
            sourceFromMsg && (Array.isArray(sourceFromMsg.neo4jResults) || Array.isArray(sourceFromMsg.chromaResults))
              ? {
                  neo4jResults: sourceFromMsg.neo4jResults || [],
                  chromaResults: filterValidChromaChunks(sourceFromMsg.chromaResults || []),
                }
              : {
                  neo4jResults: neo4jStep?.results || [],
                  chromaResults: filterValidChromaChunks(chromaStep?.matchedChunks || []),
                };

          return {
            id: `ai-${id}-${index}`,
            role: 'ai',
            content: msg.content || '',
            thinkingSteps: msg.thinkingSteps || [],
            thinkingProcess,
            answerContent: msg.content || '',
            sourceData,
            sourceQuestion: prevUserQuestion,
            sourceLoading: false,
            primaryChromaSource: msg.primaryChromaSource || undefined,
            tableSearchKeyword: msg.tableSearchKeyword,
            routingEventId: msg.routingEventId,
            turnId: msg.turnId,
            methodCreateAction: msg.methodCreateAction,
            answerFeedback: msg.answerFeedback,
          };
        });

        setMessages(loadedMessages);
        const sid = data.sessionId || '';
        setSessionId(sid);
        if (sid && typeof window !== 'undefined') {
          localStorage.setItem('znwd_session_id', sid);
        }
      }
    } catch (err) {
      console.error('Load history detail failed:', err);
    }
  };

  const handleDeleteHistory = async (id: number) => {
    try {
      await fetch(`/api/znwd/history/${id}`, { method: 'DELETE' });
      antMessage.success('已删除');
      loadHistory();
    } catch {
      antMessage.error('删除失败');
    }
  };

  const handleClearHistory = async () => {
    try {
      await fetch('/api/znwd/history', { method: 'DELETE' });
      setHistory([]);
      setMessages([]);
      antMessage.success('已清空');
    } catch {
      antMessage.error('清空失败');
    }
  };

  const handleSaveInterest = async () => {
    if (typeof window === 'undefined') return;
    const userId = localStorage.getItem('userId') || 'anonymous';
    setInterestSaving(true);
    try {
      const res = await fetch('/api/znwd/user/interest', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          userId,
          equipmentTypeIds: interestEquipSelected,
          environmentIds: interestEnvSelected,
        }),
      });
      if (!res.ok) throw new Error('bad status');
      antMessage.success('已保存兴趣配置');
      setInterestDrawerOpen(false);
    } catch {
      antMessage.error('保存失败');
    } finally {
      setInterestSaving(false);
    }
  };

  const processZnwdStreamEvent = useCallback((event: { type: string; data: unknown }) => {
      const currentAiMsgId = aiMsgIdRef.current;

      if (
        !streamTimingRef.current.firstLogged &&
        (event.type === 'thinking_start' || event.type === 'thinking') &&
        isZnwd1Stream
      ) {
        const t1 = typeof performance !== 'undefined' ? performance.now() : Date.now();
        const ms = Math.round(t1 - streamTimingRef.current.t0);
        streamTimingRef.current.firstLogged = true;
        if (ms > 1500) {
          console.warn(`[ZNWD1] 首字思考延迟 ${ms}ms（>1500ms）`);
        } else {
          console.info(`[ZNWD1] 首字思考延迟 ${ms}ms`);
        }
      }

      console.log('SSE Event:', event.type, event.data);
      if (event.type === 'thinking_start') {
        setMessages(prev =>
          prev.map(m => {
            if (m.id !== currentAiMsgId) return m;
            const prevContent = m.thinkingProcess?.content || '';
            const seed =
              prevContent.trim() || '正在分析您的问题…\n';
            if (isZnwd45Stream) {
              return {
                ...m,
                stepStatusLine: '正在理解您的问题…',
                bootstrapLines: [...(m.bootstrapLines || []), '正在理解您的问题…'].filter(
                  (v, i, a) => a.indexOf(v) === i
                ),
                thinkingProcess: {
                  content: seed.endsWith('\n') ? seed : `${seed}\n`,
                  isComplete: false,
                },
              };
            }
            return {
              ...m,
              streamPhase: 'thinking',
              stepStatusLine: undefined,
              thinkingProcess: {
                content: seed.endsWith('\n') ? seed : `${seed}\n`,
                isComplete: false,
              },
            };
          })
        );
      } else if (event.type === 'context_resolved') {
        const cr = event.data as {
          resolvedQuestion?: string;
          rawQuestion?: string;
        };
        const line = (cr.resolvedQuestion || '').trim();
        if (!line) return;
        setMessages((prev) =>
          prev.map((m) => {
            if (m.id !== currentAiMsgId) return m;
            const seed = m.thinkingProcess?.content || '';
            const addition = `理解为：${line}\n`;
            const next = seed.includes(addition.trim()) ? seed : `${seed}${addition}`;
            return {
              ...m,
              thinkingProcess: {
                content: next,
                isComplete: m.thinkingProcess?.isComplete ?? false,
              },
            };
          }),
        );
      } else if (event.type === 'search_keywords') {
        const sk = event.data as { ssyqTableKeyword?: string };
        const kw = (sk.ssyqTableKeyword || '').trim();
        setMessages((prev) =>
          prev.map((m) => (m.id === currentAiMsgId ? { ...m, tableSearchKeyword: kw } : m))
        );
        if (isZnwd45Stream && kw) {
          queueMicrotask(() => void prefetchSourceTableForMessage(currentAiMsgId));
        }
      } else if (event.type === 'thinking') {
        const td = event.data as { content?: string; kind?: string };
        const chunk = td.content || '';
        const thinkKind = td.kind;
        if (!chunk) return;
        setMessages(prev =>
          prev.map(m => {
            if (m.id !== currentAiMsgId) return m;
            // ZNWD1 里程碑文案（已收到/正在理解/图谱验证等）须进入思考区；
            // 若仍当 bootstrap，会一直停在「正在准备回答…」三点加载。
            if (m.streamPhase === 'steps') {
              if (!isZnwd1Stream && isZnwdBootstrapThinkingText(chunk)) {
                const line = chunk.trim().split('\n')[0] || chunk.trim();
                const lines = [...(m.bootstrapLines || [])];
                if (line && !lines.includes(line)) lines.push(line);
                return {
                  ...m,
                  stepStatusLine: line || m.stepStatusLine,
                  bootstrapLines: lines,
                };
              }
              const promoteOnRoute =
                isZnwd45Stream && (thinkKind === 'route' || thinkKind === 'system');
              const cur = m.thinkingProcess?.content || '';
              const seed = cur.trim() || '正在分析您的问题…\n';
              const merged = znwdAppendThinkingChunk(
                seed.endsWith('\n') ? seed : `${seed}\n`,
                chunk,
                thinkKind,
                m.thinkingLastKind
              );
              return {
                ...m,
                streamPhase: promoteOnRoute ? ('thinking' as const) : ('thinking' as const),
                stepStatusLine: undefined,
                thinkingLastKind: merged.lastKind,
                thinkingProcess: {
                  content: merged.content,
                  isComplete: false,
                },
              };
            }
            const cur = m.thinkingProcess?.content || '';
            const merged = znwdAppendThinkingChunk(cur, chunk, thinkKind, m.thinkingLastKind);
            return {
              ...m,
              streamPhase: 'thinking' as const,
              stepStatusLine: undefined,
              thinkingLastKind: merged.lastKind,
              thinkingProcess: {
                content: merged.content,
                isComplete: false,
              },
            };
          })
        );
      } else if (event.type === 'route_progress') {
        const rp = event.data as {
          stage?: string;
          status?: string;
          message?: string;
          purpose?: string;
          purpose_zh?: string;
          route_id?: string;
          keywords?: string[];
        };
        const stage = (rp.stage || '').trim();
        const stageLabel = ZNWD_ROUTE_PROGRESS_STAGE_LABELS[stage] || stage || '路由';
        const line = (rp.message || stageLabel).trim();
        setMessages((prev) =>
          prev.map((m) => {
            if (m.id !== currentAiMsgId) return m;
            const subStep = {
              id: stage || 'route',
              purpose: line || stageLabel,
              status: rp.status === 'done' ? 'done' : 'loading',
            };
            const existing = m.thinkingSteps || [];
            const routingIdx = existing.findIndex((s) => s.type === 'routing');
            let nextSteps: ThinkingStep[];
            if (routingIdx < 0) {
              nextSteps = [
                ...existing,
                {
                  type: 'routing',
                  message: line || '正在理解问题…',
                  status: rp.status === 'done' ? 'done' : 'loading',
                  subSteps: [subStep],
                },
              ];
            } else {
              nextSteps = [...existing];
              const routing = { ...nextSteps[routingIdx] };
              const subs = [...(routing.subSteps || [])];
              const si = subs.findIndex((s) => s.id === subStep.id);
              if (si >= 0) subs[si] = subStep;
              else subs.push(subStep);
              routing.subSteps = subs;
              if (rp.status === 'loading') routing.status = 'loading';
              if (line) routing.message = line;
              nextSteps[routingIdx] = routing;
            }
            const shouldPromote =
              isZnwd45Stream && (m.streamPhase === 'steps' || !m.streamPhase);
            const seed = (m.thinkingProcess?.content || '').trim() || '正在分析您的问题…\n';
            const nextThinking =
              shouldPromote && line
                ? {
                    content: seed.includes(line) ? seed : `${seed}${line}\n`,
                    isComplete: false,
                  }
                : m.thinkingProcess;
            return {
              ...m,
              streamPhase: shouldPromote ? ('thinking' as const) : m.streamPhase,
              stepStatusLine: line || m.stepStatusLine,
              thinkingSteps: nextSteps,
              thinkingProcess: nextThinking,
              routeDraft: {
                ...(m.routeDraft || {}),
                ...(rp.purpose ? { purpose: rp.purpose } : {}),
                ...(rp.purpose_zh ? { purpose_zh: rp.purpose_zh } : {}),
                ...(rp.route_id ? { route_id: rp.route_id } : {}),
                ...(Array.isArray(rp.keywords) && rp.keywords.length
                  ? { keywords: rp.keywords }
                  : {}),
              },
            };
          })
        );
      } else if (event.type === 'tool_result') {
        const toolData = event.data as { name: string; result: any };
        setMessages(prev =>
          prev.map(m => {
            if (m.id !== currentAiMsgId) return m;
            const currentSource = m.sourceData || { neo4jResults: [], chromaResults: [] };
            const nextSource: MessageSourceData = {
              neo4jResults: currentSource.neo4jResults,
              chromaResults: currentSource.chromaResults,
            };
            if (toolData.name === 'query_neo4j' && Array.isArray(toolData.result)) {
              nextSource.neo4jResults = toolData.result;
            }
            if (toolData.name === 'search_knowledge_base' && Array.isArray(toolData.result)) {
              nextSource.chromaResults = toolData.result;
            }
            if (
              (toolData.name === 'znwd_execute_meta_steps' || toolData.name === 'znwd_meta_ops_retrieve') &&
              toolData.result &&
              typeof toolData.result === 'object'
            ) {
              const r = toolData.result as {
                neo4jResults?: unknown[];
                chromaResults?: unknown[];
              };
              if (Array.isArray(r.neo4jResults)) {
                nextSource.neo4jResults = r.neo4jResults as Neo4jNodeResult[];
              }
              if (Array.isArray(r.chromaResults)) {
                nextSource.chromaResults = filterValidChromaChunks(r.chromaResults as ChromaChunkResult[]);
              }
            }
            return {
              ...m,
              sourceData: nextSource,
            };
          })
        );
      } else if (event.type === 'source_data') {
        const src = event.data as MessageSourceData & { partial?: boolean };
        const nextSource: MessageSourceData = {
          neo4jResults: Array.isArray(src?.neo4jResults) ? src.neo4jResults : [],
          chromaResults: filterValidChromaChunks(Array.isArray(src?.chromaResults) ? src.chromaResults : []),
        };
        const flat = flattenZnwdNeoHitsForGraph(nextSource.neo4jResults);
        const equipmentNodes = flat.filter((n) => n.labels?.includes('Equipment'));
        const preview = formatSourcePreviewLine(
          (equipmentNodes.length ? equipmentNodes : flat) as { labels?: string[]; properties?: Record<string, unknown> }[],
        );
        setMessages(prev =>
          prev.map((m) => {
            if (m.id !== currentAiMsgId) return m;
            return {
              ...m,
              sourceData: nextSource,
              sourceLoading: !src?.partial,
              sourcePreviewLine: preview || m.sourcePreviewLine,
            };
          })
        );
        if (isZnwd45Stream && !src?.partial) {
          queueMicrotask(() => void prefetchSourceTableForMessage(currentAiMsgId));
        }
      } else if (event.type === 'step') {
        const stepData = event.data as ThinkingStep & {
          subSteps?: { id?: string; purpose?: string; status?: string }[];
          templateId?: string;
          candidateCount?: number;
          bindingCount?: number;
          zvecUsed?: boolean;
        };
        const statusLine = humanizeStepMessage(
          (stepData.message || getStepLabel(stepData.type)).trim(),
          stepData.type
        );
        setMessages(prev =>
          prev.map(m => {
            if (m.id !== currentAiMsgId) return m;
            const existing = m.thinkingSteps || [];
            const idx = existing.findIndex((s) => s.type === stepData.type);
            let nextSteps: ThinkingStep[];
            if (idx >= 0) {
              nextSteps = [...existing];
              nextSteps[idx] = { ...nextSteps[idx], ...stepData };
            } else {
              nextSteps = [...existing, stepData];
            }
            const baseSource = m.sourceData || { neo4jResults: [], chromaResults: [] };
            const neoFromStep = (stepData as ThinkingStep & { results?: Neo4jNodeResult[] }).results;
            const chromaFromStep = (stepData as ThinkingStep & { matchedChunks?: ChromaChunkResult[] }).matchedChunks;
            let sourceData = baseSource;
            if (stepData.type === 'neo4j_search' && Array.isArray(neoFromStep) && neoFromStep.length > 0) {
              sourceData = { ...sourceData, neo4jResults: neoFromStep };
            }
            if (stepData.type === 'chroma_search' && Array.isArray(chromaFromStep) && chromaFromStep.length > 0) {
              sourceData = {
                ...sourceData,
                chromaResults: filterValidChromaChunks(chromaFromStep),
              };
            }
            const hasRows =
              (sourceData.neo4jResults?.length || 0) > 0 || (sourceData.chromaResults?.length || 0) > 0;

            const inBootstrapSteps = m.streamPhase === 'steps' || stepData.phase === 'bootstrap';
            if (inBootstrapSteps) {
              const lines = [...(m.bootstrapLines || [])];
              if (statusLine && !lines.includes(statusLine)) {
                lines.push(statusLine);
              }
              const promoteToThinking =
                Boolean(statusLine && stepData.type === 'routing' && stepData.status === 'done');
              const promoteZnwd4 =
                isZnwd45Stream &&
                stepData.type === 'routing' &&
                stepData.status === 'done';
              return {
                ...m,
                streamPhase: promoteToThinking || promoteZnwd4 ? ('thinking' as const) : m.streamPhase,
                stepStatusLine: promoteToThinking || promoteZnwd4 ? statusLine : statusLine || m.stepStatusLine,
                bootstrapLines: lines,
                thinkingProcess: promoteToThinking || promoteZnwd4
                  ? {
                      content: `${(m.thinkingProcess?.content || '').trim() || '正在分析您的问题…'}\n${statusLine}\n`,
                      isComplete: false,
                    }
                  : m.thinkingProcess,
                thinkingSteps: nextSteps,
                sourceData,
                sourceLoading: hasRows ? false : m.sourceLoading,
              };
            }

            const nextPhase =
              isZnwd45Stream &&
              stepData.type === 'retrieval' &&
              stepData.status === 'loading' &&
              inBootstrapSteps
                ? ('thinking' as const)
                : m.streamPhase;

            return {
              ...m,
              streamPhase: nextPhase,
              thinkingSteps: nextSteps,
              stepStatusLine: statusLine || m.stepStatusLine,
              sourceData,
              sourceLoading: hasRows ? false : m.sourceLoading,
            };
          })
        );
      } else if (event.type === 'answer') {
        const answerData = event.data as { content: string };
        setMessages(prev =>
          prev.map(m => {
            if (m.id !== currentAiMsgId) return m;
            const nextPhase =
              m.streamPhase === 'steps' || m.streamPhase === 'thinking' ? 'answer' : (m.streamPhase || 'answer');
            return {
              ...m,
              streamPhase: nextPhase,
              stepStatusLine: undefined,
              answerContent: (m.answerContent || '') + answerData.content
            };
          })
        );
      } else if (event.type === 'route_clarify') {
        const cd = event.data as {
          clarifyEventId?: string;
          originalQuestion?: string;
          prompt?: string;
          options?: { id: string; routeId: string; label: string }[];
        };
        setMessages(prev =>
          prev.map(m => {
            if (m.id !== currentAiMsgId) return m;
            const think = (m.thinkingProcess?.content || '').trim();
            return {
              ...m,
              streamPhase: 'answer' as const,
              stepStatusLine: undefined,
              sourceLoading: false,
              thinkingProcess: {
                content: think ? `${think}\n` : '正在分析您的问题…\n',
                isComplete: true,
              },
              routeClarify: {
                clarifyEventId: cd.clarifyEventId || '',
                originalQuestion: cd.originalQuestion || m.sourceQuestion || '',
                prompt: cd.prompt || '请确认您想了解哪一类信息：',
                options: Array.isArray(cd.options) ? cd.options : [],
              },
            };
          })
        );
      } else if (event.type === 'entity_clarify') {
        const cd = event.data as {
          clarifyEventId?: string;
          originalQuestion?: string;
          question?: string;
          prompt?: string;
          childName?: string;
          options?: { id: string; nodeId: string; label: string; parentType?: string; name?: string }[];
        };
        setMessages(prev =>
          prev.map(m => {
            if (m.id !== currentAiMsgId) return m;
            const think = (m.thinkingProcess?.content || '').trim();
            return {
              ...m,
              streamPhase: 'answer' as const,
              stepStatusLine: undefined,
              sourceLoading: false,
              thinkingProcess: {
                content: think ? `${think}\n` : '正在分析您的问题…\n',
                isComplete: true,
              },
              entityClarify: {
                clarifyEventId: cd.clarifyEventId || '',
                originalQuestion: cd.originalQuestion || cd.question || m.sourceQuestion || '',
                prompt: cd.prompt || '请选择具体装备类型：',
                childName: cd.childName,
                options: Array.isArray(cd.options) ? cd.options : [],
              },
            };
          })
        );
      } else if (event.type === 'timing') {
        const td = event.data as { phase?: string; ms?: number };
        if (process.env.NODE_ENV === 'development' && td.phase && typeof td.ms === 'number') {
          console.info(`[ZNWD] timing ${td.phase}=${td.ms}ms`);
        }
        setMessages((prev) =>
          prev.map((m) => {
            if (m.id !== currentAiMsgId) return m;
            return {
              ...m,
              serverTimingMs: {
                ...(m.serverTimingMs || {}),
                [String(td.phase)]: Number(td.ms),
              },
            };
          }),
        );
      } else if (event.type === 'error') {
        const errorData = event.data as { message: string };
        antMessage.error(errorData.message, 10);
        setMessages(prev =>
          prev.map(m => {
            if (m.id !== currentAiMsgId) return m;
            return {
              ...m,
              streamPhase: 'answer',
              stepStatusLine: undefined,
              answerContent: `⚠️ ${errorData.message}`,
              answerDurationMs: znwdComputeAnswerDuration(m.answerStartedAt),
            };
          })
        );
      } else if (event.type === 'token_limit') {
        const limitData = event.data as { message: string; estimatedTokens: number; maxTokens: number };
        antMessage.warning(limitData.message, 5);
        setMessages(prev =>
          prev.map(m => {
            if (m.id !== currentAiMsgId) return m;
            return {
              ...m,
              streamPhase: 'answer',
              stepStatusLine: undefined,
              answerContent: `⚠️ ${limitData.message}（已使用 ${limitData.estimatedTokens} / ${limitData.maxTokens} tokens）`,
              answerDurationMs: znwdComputeAnswerDuration(m.answerStartedAt),
              thinkingProcess: m.thinkingProcess ? {
                ...m.thinkingProcess,
                isComplete: true
              } : undefined,
            };
          })
        );
      } else if (event.type === 'done') {
        const doneData = event.data as {
          fullAnswer?: string;
          fullThinking?: string;
          primaryChromaSource?: PrimaryChromaSource | null;
          ssyqTableKeyword?: string;
          awaitingRouteClarify?: boolean;
          awaitingEntityClarify?: boolean;
          turnId?: string;
          routingEventId?: string;
          correctionHints?: CorrectionHints;
          methodCreateAction?: ChatMessage['methodCreateAction'];
          timingMs?: Record<string, number>;
        };
        let prefetchOverride:
          | { keyword: string; neo4jHits: Neo4jNodeResult[]; userQuestion: string }
          | null = null;
        setMessages((prev) => {
          const next = prev.map((m) => {
            if (m.id !== currentAiMsgId) return m;
            if (doneData.awaitingRouteClarify && m.routeClarify && !m.routeClarify.resolved) {
              const thinkCur = m.thinkingProcess?.content || '';
              let thinkingContent = thinkCur;
              if (typeof doneData.fullThinking === 'string' && doneData.fullThinking.length > thinkingContent.length) {
                thinkingContent = doneData.fullThinking;
              }
              return {
                ...m,
                streamPhase: 'answer' as const,
                stepStatusLine: undefined,
                sourceLoading: false,
                answerDurationMs: znwdComputeAnswerDuration(m.answerStartedAt),
                thinkingProcess: {
                  content: thinkingContent.trim() || thinkCur.trim() || '已完成初步分析。',
                  isComplete: true,
                },
              };
            }
            if (doneData.awaitingEntityClarify && m.entityClarify && !m.entityClarify.resolved) {
              const thinkCur = m.thinkingProcess?.content || '';
              let thinkingContent = thinkCur;
              if (typeof doneData.fullThinking === 'string' && doneData.fullThinking.length > thinkingContent.length) {
                thinkingContent = doneData.fullThinking;
              }
              return {
                ...m,
                streamPhase: 'answer' as const,
                stepStatusLine: undefined,
                sourceLoading: false,
                answerDurationMs: znwdComputeAnswerDuration(m.answerStartedAt),
                thinkingProcess: {
                  content: thinkingContent.trim() || thinkCur.trim() || '已完成初步分析。',
                  isComplete: true,
                },
              };
            }
            let answerContent = m.answerContent || '';
            if (!answerContent.trim() && typeof doneData.fullAnswer === 'string' && doneData.fullAnswer.trim()) {
              answerContent = znwdSanitizeAnswerBodyForDisplay(doneData.fullAnswer.trim());
            } else if (
              typeof doneData.fullAnswer === 'string' &&
              doneData.fullAnswer.length > answerContent.length
            ) {
              answerContent = znwdSanitizeAnswerBodyForDisplay(doneData.fullAnswer);
            }
            const thinkCur = m.thinkingProcess?.content || '';
            let thinkingContent = thinkCur;
            if (
              !thinkCur.trim() &&
              typeof doneData.fullThinking === 'string' &&
              doneData.fullThinking.trim()
            ) {
              thinkingContent = doneData.fullThinking;
            } else if (
              typeof doneData.fullThinking === 'string' &&
              doneData.fullThinking.length > thinkingContent.length
            ) {
              thinkingContent = doneData.fullThinking;
            }
            const doneKw = (doneData.ssyqTableKeyword || '').trim();
            const correctionHints = doneData.correctionHints;
            const methodCreateAction = doneData.methodCreateAction;
            const hasCorrection =
              !!doneData.turnId &&
              !!correctionHints &&
              correctionHints.enabled &&
              !doneData.awaitingRouteClarify;
            const userQ =
              (m.sourceQuestion || '').trim() || findPrevUserQuestionForAiMessage(prev, currentAiMsgId);
            const updatedMsg = {
              ...m,
              streamPhase: 'answer' as const,
              stepStatusLine: undefined,
              sourceLoading: false,
              answerContent,
              content: answerContent,
              answerDurationMs: znwdComputeAnswerDuration(m.answerStartedAt),
              ...(doneData.timingMs ? { serverTimingMs: doneData.timingMs } : {}),
              ...(doneKw ? { tableSearchKeyword: doneKw } : {}),
              primaryChromaSource: doneData.primaryChromaSource ?? m.primaryChromaSource,
              ...(hasCorrection
                ? {
                    turnId: doneData.turnId,
                    routingEventId: doneData.routingEventId,
                    answerCorrection: {
                      ...correctionHints,
                      turnId: doneData.turnId,
                      routingEventId: doneData.routingEventId,
                    },
                  }
                : {}),
              ...(methodCreateAction?.enabled ? { methodCreateAction } : {}),
              thinkingProcess: {
                content: thinkingContent.trim() || thinkCur.trim() || '已完成分析。',
                isComplete: true,
              },
              thinkingSteps: (m.thinkingSteps || []).map((s) =>
                s.status === 'loading' ? { ...s, status: 'done' as const } : s
              ),
            };
            if (
              isZnwd45Stream &&
              !doneData.awaitingRouteClarify &&
              !doneData.awaitingEntityClarify
            ) {
              const eff = getEffectiveSourceData(updatedMsg);
              const kw =
                doneKw ||
                (updatedMsg.tableSearchKeyword || '').trim() ||
                znwdFallbackTableKeywordFromQuestion(userQ);
              prefetchOverride = {
                keyword: kw,
                neo4jHits: eff.neo4jResults || [],
                userQuestion: userQ,
              };
            }
            return updatedMsg;
          });
          return next;
        });
        if (isZnwd45Stream && prefetchOverride) {
          void prefetchSourceTableForMessage(currentAiMsgId, prefetchOverride);
        }
      }
  }, [isZnwd1Stream, isZnwd45Stream, prefetchSourceTableForMessage]);

  useEffect(() => {
    znwdStreamHandlerRef.current = processZnwdStreamEvent;
  }, [processZnwdStreamEvent]);

  const handleSend = useCallback(async (msg: string) => {
    if (!msg.trim()) return;

    setInputValue('');

    let currentSessionId = sessionId;
    if (!currentSessionId) {
      currentSessionId = localStorage.getItem('znwd_session_id') || `session-${Date.now()}`;
      setSessionId(currentSessionId);
      localStorage.setItem('znwd_session_id', currentSessionId);
    }

    const userMessage = { id: `user-${Date.now()}`, role: 'user' as const, content: msg };
    const newAiMsgId = `ai-${Date.now()}`;
    aiMsgIdRef.current = newAiMsgId;
    const answerStartedAt = Date.now();
    const aiMessage = { 
      id: newAiMsgId, 
      role: 'ai' as const, 
      content: '', 
      thinkingSteps: [] as any[],
      thinkingProcess: { content: '', isComplete: false },
      answerContent: '',
      answerStartedAt,
      streamPhase: 'steps' as const,
      stepStatusLine: '正在准备回答…' as string | undefined,
      bootstrapLines: ['正在准备回答…'] as string[],
      sourceData: { neo4jResults: [], chromaResults: [] },
      sourceLoading: true,
      sourceTableLoading: false,
      sourceTableReady: false,
      sourceQuestion: msg,
    };
    
    sourceHydratePayloadRef.current = null;
    setMessages(prev => [...prev, userMessage, aiMessage]);

    const userId = typeof window !== 'undefined' ? (localStorage.getItem('userId') || 'anonymous') : 'anonymous';
    const llmKey =
      selectedLlmModelKey ||
      (typeof window !== 'undefined' ? localStorage.getItem(ZNWD_LLM_STORAGE_KEY) : '') ||
      llmModels[0]?.model_key ||
      '';
    streamTimingRef.current = {
      t0: typeof performance !== 'undefined' ? performance.now() : Date.now(),
      firstLogged: false,
    };
    await sendMessage(
      {
        question: msg,
        sessionId: currentSessionId,
        userId,
        llmModelKey: llmKey,
      },
      processZnwdStreamEvent
    );
  }, [sendMessage, sessionId, selectedLlmModelKey, llmModels, isZnwd1Stream, isZnwd45Stream, prefetchSourceTableForMessage, processZnwdStreamEvent]);

  const handleGuidedExecute = useCallback(
    async (payload: {
      question: string;
      templateId: string;
      intentId: string;
      goalType?: string;
      bindings: { slot_key: string; node_type: string; name: string; entity_id?: string }[];
    }) => {
      if (!payload.question.trim() || isGenerating) return;
      if (!isZnwd6Stream) {
        void handleSend(payload.question);
        return;
      }

      setInputValue('');

      let currentSessionId = sessionId;
      if (!currentSessionId) {
        currentSessionId = localStorage.getItem('znwd_session_id') || `session-${Date.now()}`;
        setSessionId(currentSessionId);
        localStorage.setItem('znwd_session_id', currentSessionId);
      }

      const userMessage = {
        id: `user-${Date.now()}`,
        role: 'user' as const,
        content: payload.question,
      };
      const newAiMsgId = `ai-${Date.now()}`;
      aiMsgIdRef.current = newAiMsgId;
      const answerStartedAt = Date.now();
      const aiMessage = {
        id: newAiMsgId,
        role: 'ai' as const,
        content: '',
        thinkingSteps: [] as any[],
        thinkingProcess: { content: '', isComplete: false },
        answerContent: '',
        answerStartedAt,
        streamPhase: 'steps' as const,
        stepStatusLine: '正在按引导选择精准检索…',
        bootstrapLines: ['正在按引导选择精准检索…'],
        sourceData: { neo4jResults: [], chromaResults: [] },
        sourceLoading: true,
        sourceTableLoading: false,
        sourceTableReady: false,
        sourceQuestion: payload.question,
        guidedTemplateId: payload.templateId,
      };

      sourceHydratePayloadRef.current = null;
      setMessages((prev) => [...prev, userMessage, aiMessage]);

      const userId =
        typeof window !== 'undefined' ? localStorage.getItem('userId') || 'anonymous' : 'anonymous';
      const llmKey =
        selectedLlmModelKey ||
        (typeof window !== 'undefined' ? localStorage.getItem(ZNWD_LLM_STORAGE_KEY) : '') ||
        llmModels[0]?.model_key ||
        '';


      await sendGuidedExecute(
        {
          question: payload.question,
          sessionId: currentSessionId,
          userId,
          llmModelKey: llmKey,
          templateId: payload.templateId,
          intentId: payload.intentId,
          goalType: payload.goalType,
          bindings: payload.bindings,
        },
        processZnwdStreamEvent,
      );
    },
    [sendGuidedExecute, sessionId, selectedLlmModelKey, llmModels, isGenerating, isZnwd6Stream, handleSend, processZnwdStreamEvent],
  );

  const handleCreateMethodFromAnswer = useCallback(
    (msg: ChatMessage) => {
      const action = msg.methodCreateAction;
      if (!action?.enabled || !action.draft) {
        antMessage.warning('当前回答无法创建方法草稿');
        return;
      }
      try {
        sessionStorage.setItem('methodCreateFromZnwd', JSON.stringify(action.draft));
        router.push(action.href || '/FLGL?tab=method&autoOpen=1');
      } catch {
        antMessage.error('无法打开分类管理，请稍后重试');
      }
    },
    [router],
  );

  const handleRouteClarifyPick = useCallback(
    async (
      aiMsgId: string,
      clarify: NonNullable<ChatMessage['routeClarify']>,
      option: { routeId: string; label: string }
    ) => {
      if (!clarify || clarify.resolved || isGenerating) return;

      let currentSessionId = sessionId;
      if (!currentSessionId) {
        currentSessionId = localStorage.getItem('znwd_session_id') || `session-${Date.now()}`;
        setSessionId(currentSessionId);
      }

      aiMsgIdRef.current = aiMsgId;
      setMessages(prev =>
        prev.map(m => {
          if (m.id !== aiMsgId) return m;
          const think = (m.thinkingProcess?.content || '').trim();
          return {
            ...m,
            streamPhase: 'steps' as const,
            stepStatusLine: '正在按您的选择继续检索…',
            sourceLoading: true,
            answerContent: '',
            content: '',
            answerStartedAt: Date.now(),
            answerDurationMs: undefined,
            routeClarify: {
              ...clarify,
              resolved: true,
              selectedLabel: option.label,
            },
            thinkingProcess: {
              content: think ? `${think}\n您选择了：${option.label}\n` : `您选择了：${option.label}\n`,
              isComplete: false,
            },
          };
        })
      );

      const userId = typeof window !== 'undefined' ? (localStorage.getItem('userId') || 'anonymous') : 'anonymous';
      const llmKey =
        selectedLlmModelKey ||
        (typeof window !== 'undefined' ? localStorage.getItem(ZNWD_LLM_STORAGE_KEY) : '') ||
        llmModels[0]?.model_key ||
        '';

      await sendMessage(
        {
          question: clarify.originalQuestion,
          sessionId: currentSessionId,
          userId,
          llmModelKey: llmKey,
          routeClarify: {
            selectedRouteId: option.routeId,
            clarifyEventId: clarify.clarifyEventId,
            originalQuestion: clarify.originalQuestion,
            selectedLabel: option.label,
          },
        },
        processZnwdStreamEvent
      );
    },
    [sendMessage, sessionId, selectedLlmModelKey, llmModels, isGenerating, processZnwdStreamEvent]
  );

  const handleEntityClarifyPick = useCallback(
    async (
      aiMsgId: string,
      clarify: NonNullable<ChatMessage['entityClarify']>,
      option: { nodeId: string; label: string; parentType?: string; name?: string }
    ) => {
      if (!clarify || clarify.resolved || isGenerating) return;

      let currentSessionId = sessionId;
      if (!currentSessionId) {
        currentSessionId = localStorage.getItem('znwd_session_id') || `session-${Date.now()}`;
        setSessionId(currentSessionId);
      }

      aiMsgIdRef.current = aiMsgId;
      setMessages(prev =>
        prev.map(m => {
          if (m.id !== aiMsgId) return m;
          const think = (m.thinkingProcess?.content || '').trim();
          return {
            ...m,
            streamPhase: 'steps' as const,
            stepStatusLine: '正在按您选择的装备类型检索…',
            sourceLoading: true,
            answerContent: '',
            content: '',
            answerStartedAt: Date.now(),
            answerDurationMs: undefined,
            entityClarify: {
              ...clarify,
              resolved: true,
              selectedLabel: option.label,
            },
            thinkingProcess: {
              content: think ? `${think}\n您选择了：${option.label}\n` : `您选择了：${option.label}\n`,
              isComplete: false,
            },
          };
        })
      );

      const userId = typeof window !== 'undefined' ? (localStorage.getItem('userId') || 'anonymous') : 'anonymous';
      const llmKey =
        selectedLlmModelKey ||
        (typeof window !== 'undefined' ? localStorage.getItem(ZNWD_LLM_STORAGE_KEY) : '') ||
        llmModels[0]?.model_key ||
        '';

      await sendMessage(
        {
          question: clarify.originalQuestion,
          sessionId: currentSessionId,
          userId,
          llmModelKey: llmKey,
          entityClarify: {
            selectedNodeId: option.nodeId,
            clarifyEventId: clarify.clarifyEventId,
            originalQuestion: clarify.originalQuestion,
            parentType: option.parentType,
            childName: option.name || clarify.childName,
            name: option.name,
          },
        },
        processZnwdStreamEvent
      );
    },
    [sendMessage, sessionId, selectedLlmModelKey, llmModels, isGenerating, processZnwdStreamEvent]
  );

  const postAnswerFeedback = useCallback(
    async (payload: AnswerFeedbackSubmitPayload) => {
      const currentSessionId =
        sessionId || (typeof window !== 'undefined' ? localStorage.getItem('znwd_session_id') || '' : '');
      const userId =
        typeof window !== 'undefined' ? localStorage.getItem('userId') || 'anonymous' : 'anonymous';
      await fetch(znwdFeedbackApi, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ...payload,
          sessionId: currentSessionId,
          userId,
        }),
      });
    },
    [sessionId, znwdFeedbackApi]
  );

  const handleAnswerFeedbackOnly = useCallback(
    async (aiMsgId: string, payload: AnswerFeedbackSubmitPayload) => {
      try {
        await postAnswerFeedback(payload);
        setMessages((prev) =>
          prev.map((m) => {
            if (m.id !== aiMsgId) return m;
            return {
              ...m,
              answerFeedback: { rating: payload.rating, submitted: true },
              answerCorrection: m.answerCorrection
                ? { ...m.answerCorrection, resolved: true }
                : undefined,
            };
          })
        );
        antMessage.success('感谢您的反馈');
      } catch {
        antMessage.error('反馈提交失败，请稍后重试');
      }
    },
    [postAnswerFeedback]
  );

  const handleAnswerCorrectionRerun = useCallback(
    async (aiMsgId: string, payload: AnswerFeedbackSubmitPayload) => {
      if (!payload.feedbackKind || !payload.correctedRouteId || isGenerating) return;

      let currentSessionId = sessionId;
      if (!currentSessionId) {
        currentSessionId = localStorage.getItem('znwd_session_id') || `session-${Date.now()}`;
        setSessionId(currentSessionId);
      }

      aiMsgIdRef.current = aiMsgId;
      setMessages((prev) =>
        prev.map((m) => {
          if (m.id !== aiMsgId) return m;
          const think = (m.thinkingProcess?.content || '').trim();
          return {
            ...m,
            streamPhase: 'steps' as const,
            stepStatusLine: '正在按您的纠正重新检索…',
            sourceLoading: true,
            answerContent: '',
            content: '',
            answerStartedAt: Date.now(),
            answerDurationMs: undefined,
            answerFeedback: { rating: 'negative', submitted: true },
            answerCorrection: m.answerCorrection
              ? { ...m.answerCorrection, resolved: true }
              : undefined,
            thinkingProcess: {
              content: think ? `${think}\n正在按您的纠正重新检索…\n` : '正在按您的纠正重新检索…\n',
              isComplete: false,
            },
          };
        })
      );

      try {
        await postAnswerFeedback({ ...payload, rerun: true });
      } catch {
        antMessage.warning('反馈记录失败，仍将尝试重新回答');
      }

      const userId =
        typeof window !== 'undefined' ? localStorage.getItem('userId') || 'anonymous' : 'anonymous';
      const llmKey =
        selectedLlmModelKey ||
        (typeof window !== 'undefined' ? localStorage.getItem(ZNWD_LLM_STORAGE_KEY) : '') ||
        llmModels[0]?.model_key ||
        '';

      await sendMessage(
        {
          question: payload.originalQuestion,
          sessionId: currentSessionId,
          userId,
          llmModelKey: llmKey,
          answerCorrection: {
            turnId: payload.turnId,
            routingEventId: payload.routingEventId,
            originalQuestion: payload.originalQuestion,
            rating: 'negative',
            feedbackKind: payload.feedbackKind,
            correctedRouteId: payload.correctedRouteId,
            correctedSlots: payload.correctedSlots,
            predictedSlots: payload.predictedSlots,
            comment: payload.comment,
            rerun: true,
          },
        },
        processZnwdStreamEvent
      );
    },
    [sendMessage, sessionId, selectedLlmModelKey, llmModels, isGenerating, postAnswerFeedback, processZnwdStreamEvent]
  );

  const roles: Record<string, any> = {
    ai: {
      placement: 'start',
      avatar: <Avatar icon={<RobotOutlined />} style={{ background: '#1677ff' }} />,
      variant: 'outlined', shape: 'corner',
    },
    user: {
      placement: 'end',
      avatar: <Avatar icon={<UserOutlined />} style={{ background: '#87d068' }} />,
      variant: 'filled', shape: 'corner',
    },
  };

  return (
    <Layout
      style={{
        height: 'calc(100vh - 70px)',
        background: 'var(--znwd-page-bg, #fff)',
        overflow: 'hidden',
        ...appearanceToCssVars(appearance),
      }}
    >
      <Sider
        width={280}
        collapsed={siderCollapsed}
        collapsedWidth={40}
        onCollapse={setSiderCollapsed}
        theme="light"
        style={{
          borderRight: '1px solid #f0f0f0',
          display: 'flex',
          flexDirection: 'column',
          height: '100%',
          overflow: 'hidden',
        }}
        trigger={null}
      >
        <div
          style={{
            display: 'flex',
            flexDirection: 'column',
            height: '100%',
            overflow: 'hidden',
          }}
        >
          {siderCollapsed ? (
            <div 
              style={{ 
                display: 'flex', 
                alignItems: 'flex-start', 
                justifyContent: 'center',
                paddingTop: 16,
                cursor: 'pointer',
                height: '100%'
              }}
              onClick={() => setSiderCollapsed(false)}
            >
              <Button type="text" icon={<MenuUnfoldOutlined />} title="展开历史对话" />
            </div>
          ) : (
            <>
              <div style={{ padding: '16px', borderBottom: '1px solid #f0f0f0', display: 'flex', flexDirection: 'column', gap: 12 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <Text strong style={{ fontSize: 15 }}>历史对话</Text>
                  <div style={{ display: 'flex', gap: 8 }}>
                    <Popconfirm title="确定清空所有历史？" onConfirm={handleClearHistory} okText="确定" cancelText="取消">
                      <Button type="text" size="small" icon={<ClearOutlined />} danger title="清空全部" />
                    </Popconfirm>
                    <Button 
                      type="text" 
                      size="small" 
                      icon={<MenuFoldOutlined />} 
                      onClick={() => setSiderCollapsed(true)}
                      title="收起"
                    />
                  </div>
                </div>
                <Button 
                  type="primary" 
                  icon={<PlusOutlined />} 
                  onClick={async () => {
                    const validMessages = messages.filter(m => {
                      if (m.role === 'user') return m.content.trim().length > 0;
                      if (m.role === 'ai') return (m.answerContent || m.content || '').trim().length > 0;
                      return false;
                    });

                    if (validMessages.length > 0) {
                      try {
                        await persistConversation(validMessages);
                        antMessage.success('对话已保存');
                      } catch (err) {
                        console.error('保存对话失败:', err);
                        antMessage.error('保存失败');
                      }
                    }
                    setMessages([]);
                    const newId = `session-${Date.now()}`;
                    setSessionId(newId);
                    localStorage.setItem('znwd_session_id', newId);
                    loadHistory();
                  }}
                  block
                >
                  新对话
                </Button>
              </div>
              <div style={{ flex: 1, overflowY: 'auto', minHeight: 0 }}>
                {historyLoading ? (
                  <div style={{ textAlign: 'center', padding: 40 }}><Spin /></div>
                ) : history.length === 0 ? (
                  <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无对话记录" style={{ marginTop: 40 }} />
                ) : (
                  <div style={{ padding: '0 16px 16px' }}>
                    {history.map((item) => (
                      <div
                        key={item.id}
                        style={{
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'space-between',
                          padding: '12px 0',
                          cursor: 'pointer',
                          borderBottom: '1px solid #f5f5f5',
                        }}
                        onClick={() => handleHistorySelect(item.id)}
                      >
                        <div style={{ flex: 1, overflow: 'hidden' }}>
                          <Text ellipsis style={{ fontSize: 13 }}>{item.title}</Text>
                          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: 2 }}>
                            <Text type="secondary" style={{ fontSize: 11 }}>{item.messageCount}条对话</Text>
                            <Text type="secondary" style={{ fontSize: 10 }}>{new Date(item.updatedAt).toLocaleString()}</Text>
                          </div>
                        </div>
                        <Popconfirm
                          title="删除？"
                          onConfirm={(e) => { e?.stopPropagation(); handleDeleteHistory(item.id); }}
                          onCancel={(e) => e?.stopPropagation()}
                        >
                          <Button type="text" size="small" danger icon={<DeleteOutlined />} onClick={(e) => e.stopPropagation()} />
                        </Popconfirm>
                      </div>
                    ))}
                  </div>
                )}
              </div>
              <div
                style={{
                  flexShrink: 0,
                  marginTop: 'auto',
                  borderTop: '1px solid #f0f0f0',
                  padding: '12px 16px',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: 8,
                  background: 'var(--znwd-footer-bg, #fafafa)',
                }}
              >
                <Text type="secondary" style={{ fontSize: 11 }}>
                  {isZnwd6Stream ? 'ZNWD6' : isZnwd5Stream ? 'ZNWD5' : isZnwd4Stream ? 'ZNWD4' : 'ZNWD'}
                </Text>
                <Select
                  size="small"
                  style={{ width: '100%' }}
                  value={selectedLlmModelKey || undefined}
                  onChange={(key) => {
                    setSelectedLlmModelKey(key);
                    if (typeof window !== 'undefined') {
                      localStorage.setItem(ZNWD_LLM_STORAGE_KEY, key);
                    }
                  }}
                  placeholder="选择LLM模型"
                  options={llmModels.map((m) => ({
                    value: m.model_key,
                    label: m.model_name,
                  }))}
                />
                <Space wrap size={4}>
                  <Button
                    size="small"
                    icon={<SettingOutlined />}
                    onClick={() => {
                      setInterestDrawerOpen(true);
                      void loadInterestUi();
                    }}
                  >
                    {appearance.locale === 'en' ? 'Interest' : '兴趣配置'}
                  </Button>
                  <Button
                    size="small"
                    onClick={() => setAppearanceDrawerOpen(true)}
                  >
                    {appearance.locale === 'en' ? 'Appearance' : '页面样式'}
                  </Button>
                </Space>
              </div>
            </>
          )}
        </div>
      </Sider>

      <Content
        style={{
          display: 'flex',
          flexDirection: 'column',
          height: '100%',
          overflow: 'hidden',
          background: 'var(--znwd-page-bg, #fff)',
        }}
      >
        <div
          ref={chatContainerRef}
          style={{
            flex: 1,
            overflowY: 'auto',
            padding: '16px 24px',
            background: 'var(--znwd-page-bg, #fff)',
          }}
        >
          {messages.length === 0 ? (
            <div
              style={{
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'center',
                justifyContent: 'center',
                minHeight: '100%',
                padding: '24px 16px',
              }}
            >
              <RobotOutlined style={{ fontSize: 48, marginBottom: 16, color: '#1677ff' }} />
              <Paragraph
                style={{
                  fontSize: 18,
                  fontWeight: 500,
                  color: 'var(--znwd-text-color, #333)',
                  marginBottom: 8,
                  textAlign: 'center',
                }}
              >
                {appearance.locale === 'en' ? 'Intelligent Q&A' : '智能问答助手'}
              </Paragraph>
              <Paragraph type="secondary" style={{ marginBottom: 0, textAlign: 'center' }}>
                {appearance.locale === 'en'
                  ? 'Use guided steps or type freely in the box below'
                  : '可在下方选择「引导问答」逐步构建，或切换「自由提问」直接输入'}
              </Paragraph>
            </div>
          ) : (
            <div style={{ maxWidth: ZNWD_CONTENT_MAX_WIDTH, margin: '0 auto', width: '100%' }}>
              {messages.map(msg => {
                const isUser = msg.role === 'user';
                const roleConfig = isUser ? roles.user : roles.ai;
                const showStepLoading =
                  !isUser &&
                  msg.streamPhase === 'steps' &&
                  isGenerating &&
                  msg.id === aiMsgIdRef.current &&
                  !(msg.routeClarify && !msg.routeClarify.resolved) &&
                  !(msg.entityClarify && !msg.entityClarify.resolved);
                const answerText = znwdSanitizeAnswerBodyForDisplay(msg.answerContent || msg.content || '');
                const copyText = isUser ? msg.content : answerText;
                const showCopy = !!copyText.trim() && (isUser || (!showStepLoading && !!answerText));
                const showAnswerFeedback =
                  isZnwd45Stream &&
                  !isUser &&
                  !!answerText &&
                  msg.thinkingProcess?.isComplete &&
                  !(msg.routeClarify && !msg.routeClarify.resolved) &&
                  !(msg.entityClarify && !msg.entityClarify.resolved) &&
                  (!isGenerating || msg.id !== aiMsgIdRef.current) &&
                  !!msg.answerCorrection?.enabled;
                const isLiveAnswer =
                  !isUser && isGenerating && msg.id === aiMsgIdRef.current && !!msg.answerStartedAt;
                const answerDurationLabel =
                  typeof msg.answerDurationMs === 'number'
                    ? `用时 ${znwdFormatDurationMs(msg.answerDurationMs)}`
                    : isLiveAnswer && msg.answerStartedAt
                      ? `已等待 ${znwdFormatDurationMs(Date.now() - msg.answerStartedAt)}`
                      : null;
                return (
                  <Bubble
                    key={msg.id}
                    role={isUser ? 'user' : 'ai'}
                    placement={roleConfig.placement}
                    avatar={roleConfig.avatar}
                    variant={roleConfig.variant}
                    shape={roleConfig.shape}
                    loading={showStepLoading}
                    footerPlacement={isUser ? 'outer-end' : 'outer-start'}
                    footer={showCopy ? <ZnwdMessageCopyButton text={copyText} /> : undefined}
                    loadingRender={() => (
                      <StepLoadingWithStatus
                        text={msg.stepStatusLine}
                        lines={msg.bootstrapLines}
                      />
                    )}
                    content={
                      isUser ? (
                        msg.content
                      ) : showStepLoading ? null : (
                      <div style={{ width: '100%', color: 'var(--znwd-text-color, inherit)' }}>
                        {isZnwd45Stream ? (
                          <ZnwdProgressTimeline
                            steps={msg.thinkingSteps}
                            statusLine={msg.stepStatusLine}
                            phase={msg.streamPhase}
                            sourcePreviewLine={msg.sourcePreviewLine}
                          />
                        ) : null}
                        {msg.thinkingProcess && msg.thinkingProcess.content && (
                          <ThinkingProcessPanel process={msg.thinkingProcess} />
                        )}
                        {msg.routeClarify && !msg.routeClarify.resolved && (msg.routeClarify.options?.length || 0) > 0 && (
                          <div
                            style={{
                              marginBottom: 16,
                              padding: '12px 14px',
                              background: '#f6ffed',
                              border: '1px solid #b7eb8f',
                              borderRadius: 8,
                            }}
                          >
                            <Paragraph style={{ marginBottom: 12, color: '#434343' }}>
                              {msg.routeClarify.prompt}
                            </Paragraph>
                            <Space direction="vertical" size={8} style={{ width: '100%' }}>
                              {msg.routeClarify.options.map((opt) => (
                                <Button
                                  key={opt.id}
                                  block
                                  disabled={isGenerating}
                                  style={{
                                    height: 'auto',
                                    whiteSpace: 'normal',
                                    textAlign: 'left',
                                    padding: '10px 14px',
                                    lineHeight: 1.55,
                                  }}
                                  onClick={() => void handleRouteClarifyPick(msg.id, msg.routeClarify!, opt)}
                                >
                                  {opt.label}
                                </Button>
                              ))}
                            </Space>
                          </div>
                        )}
                        {msg.entityClarify && !msg.entityClarify.resolved && (msg.entityClarify.options?.length || 0) > 0 && (
                          <div
                            style={{
                              marginBottom: 16,
                              padding: '12px 14px',
                              background: '#e6f4ff',
                              border: '1px solid #91caff',
                              borderRadius: 8,
                            }}
                          >
                            <Paragraph style={{ marginBottom: 12, color: '#434343' }}>
                              {msg.entityClarify.prompt}
                            </Paragraph>
                            <Space direction="vertical" size={8} style={{ width: '100%' }}>
                              {msg.entityClarify.options.map((opt) => (
                                <Button
                                  key={opt.id}
                                  block
                                  disabled={isGenerating}
                                  style={{
                                    height: 'auto',
                                    whiteSpace: 'normal',
                                    textAlign: 'left',
                                    padding: '10px 14px',
                                    lineHeight: 1.55,
                                  }}
                                  onClick={() => void handleEntityClarifyPick(msg.id, msg.entityClarify!, opt)}
                                >
                                  {opt.label}
                                </Button>
                              ))}
                            </Space>
                          </div>
                        )}
                        <div style={{
                          borderTop: msg.thinkingProcess?.content ? '1px solid #f0f0f0' : 'none', 
                          paddingTop: msg.thinkingProcess?.content ? 16 : 0 
                        }}>
                          {answerDurationLabel ? (
                            <Text type="secondary" style={{ display: 'block', marginBottom: 8, fontSize: 12 }}>
                              {answerDurationLabel}
                            </Text>
                          ) : null}
                          {msg.answerContent || msg.content ? (
                            isEnvironmentPropertiesAnswer(answerText) ? (
                              <EnvironmentAnswerBody content={answerText} />
                            ) : (
                              <MarkdownRenderer
                                content={answerText}
                                enableImageDownload
                              />
                            )
                          ) : msg.routeClarify && !msg.routeClarify.resolved ? (
                            <Text type="secondary">请在上方选择一项，以便继续为您检索。</Text>
                          ) : msg.entityClarify && !msg.entityClarify.resolved ? (
                            <Text type="secondary">请在上方选择具体装备类型，以便继续为您检索。</Text>
                          ) : (
                            <div style={{ color: '#999', display: 'flex', alignItems: 'center', gap: 8 }}>
                              <LoadingOutlined />
                              {msg.streamPhase === 'thinking' && !(msg.thinkingProcess?.content)
                                ? '模型思考中…'
                                : msg.streamPhase === 'thinking' && (msg.thinkingProcess?.content)
                                  ? '正在撰写回答…'
                                  : '正在生成回答…'}
                            </div>
                          )}
                        </div>
                        {showAnswerFeedback && msg.answerCorrection ? (
                          <ZnwdAnswerFeedback
                            hints={msg.answerCorrection}
                            originalQuestion={msg.sourceQuestion || ''}
                            answerPreview={answerText}
                            disabled={isGenerating}
                            submitted={msg.answerFeedback?.submitted}
                            onSubmitFeedback={(payload) => handleAnswerFeedbackOnly(msg.id, payload)}
                            onRerun={(payload) => handleAnswerCorrectionRerun(msg.id, payload)}
                          />
                        ) : null}
                        {!!answerText && msg.methodCreateAction?.enabled ? (
                          <div style={{ marginTop: 12 }}>
                            <Button
                              type="primary"
                              ghost
                              icon={<PlusOutlined />}
                              onClick={() => handleCreateMethodFromAnswer(msg)}
                              disabled={isGenerating}
                            >
                              {msg.methodCreateAction.label || '新增为试验方法'}
                            </Button>
                          </div>
                        ) : null}
                        {!!answerText && (
                          <div style={{ marginTop: 12 }}>
                            <Button
                              type="link"
                              style={{ padding: 0, fontSize: 13 }}
                              onClick={() => void openSourceDrawerFromMessage(msg)}
                            >
                              {formatSourceButtonLabel({
                                neoCount: getEffectiveSourceData(msg).neo4jResults?.length || 0,
                                chromaCount: getEffectiveSourceData(msg).chromaResults?.length || 0,
                                tableLoading: msg.sourceTableLoading,
                                sourceLoading: msg.sourceLoading,
                                sourceTableReady: msg.sourceTableReady && !!msg.cachedSourceTable,
                              })}
                            </Button>
                          </div>
                        )}
                      </div>
                    )}
                    style={{ maxWidth: ZNWD_CONTENT_MAX_WIDTH, marginBottom: 14 }}
                  />
                );
              })}
            </div>
          )}
          <div ref={chatEndRef} />
        </div>
        <div
          style={{
            padding: '8px 24px 16px',
            background: 'var(--znwd-page-bg, #fff)',
            flexShrink: 0,
          }}
        >
          <div style={{ maxWidth: ZNWD_CONTENT_MAX_WIDTH, margin: '0 auto', width: '100%' }}>
            <ZnwdUnifiedComposer
              locale={appearance.locale}
              disabled={isGenerating}
              loading={isGenerating}
              guideEnabled={isZnwd6Stream}
              value={inputValue}
              onChange={setInputValue}
              onFreeSend={handleSend}
              onGuidedExecute={(p) => void handleGuidedExecute(p)}
              onCancel={stopGeneration}
              maxWidth={ZNWD_CONTENT_MAX_WIDTH}
            />
          </div>
        </div>
      </Content>
      <Drawer
        title="回答依据"
        placement="right"
        size={1020}
        open={sourceDrawerOpen}
        onClose={() => {
          setSourceDrawerOpen(false);
          setSourceDrawerChromaRaw([]);
          setSourceDrawerFileTabKey('');
          setSourceDrawerPrimary(null);
          setSourceViewMode('equipment_table');
          setEnvironmentSources([]);
          setEquipmentHierarchyRows([]);
        }}
        getContainer={typeof window !== 'undefined' ? document.body : undefined}
        zIndex={1000}
        rootStyle={{ position: 'fixed', inset: 0 }}
        rootClassName="znwd-source-drawer"
      >
        {(() => {
          const neoList = activeSourceData?.neo4jResults || [];
          const neoUsable = neoList.filter((n) => n && typeof n === 'object' && !('error' in (n as object)));
          const fileItems = activeSourceData?.chromaResults || [];
          // 有 Neo 节点就应展示清单区（表格可能仍在加载）；不能仅依赖异步填充后的 sourceTableRows
          const hasGraph =
            sourceViewMode === 'environment'
              ? environmentSources.length > 0 || sourceTableLoading
              : sourceViewMode === 'equipment_hierarchy'
                ? equipmentHierarchyRows.length > 0 || sourceTableLoading
                : neoUsable.length > 0 ||
                  (sourceTableRows?.length || 0) > 0 ||
                  sourceTableLoading;
          const hasFiles = fileItems.length > 0 || sourceDrawerChromaRaw.length > 0;
          const filePreviewItems =
            fileItems.length > 0 ? fileItems : sourceDrawerChromaRaw;

          const graphPanel = (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
              <Space style={{ justifyContent: 'space-between', flexWrap: 'wrap' }}>
                <Text strong>
                  {sourceViewMode === 'environment'
                    ? '试验环境'
                    : sourceViewMode === 'equipment_hierarchy'
                      ? '装备清单'
                      : '项目与指标'}
                </Text>
                <Space size={8}>
                  {sourceViewMode === 'equipment_table' ? (
                    <Button size="small" icon={<ClearOutlined />} onClick={handleClearSourceSelection}>
                      清除选中
                    </Button>
                  ) : null}
                  <Button size="small" icon={<ReloadOutlined />} onClick={handleRefreshGraph}>
                    刷新
                  </Button>
                </Space>
              </Space>
              {sourceViewMode === 'environment' ? (
                <Spin spinning={sourceTableLoading}>
                  {environmentSources.length > 0 ? (
                    environmentSources.length === 1 ? (
                      <div className="znwd-env-source-panel">
                        <style>{ENVIRONMENT_HTML_TABLE_STYLES}</style>
                        {ZNWD_ENVIRONMENT_SOURCE_FIELD_KEYS.map((field) => {
                          const val = environmentSources[0][field];
                          const text = String(val ?? '').trim();
                          if (!text || text === '—') return null;
                          return (
                            <div
                              key={field}
                              style={{
                                marginBottom: 14,
                                padding: '10px 12px',
                                background: '#fafafa',
                                borderRadius: 8,
                                border: '1px solid #f0f0f0',
                              }}
                            >
                              <Text strong style={{ display: 'block', marginBottom: 8 }}>
                                {field}
                              </Text>
                              {renderZnwdEnvironmentSourceCell(field, val, false)}
                            </div>
                          );
                        })}
                      </div>
                    ) : (
                      <Table<ZnwdEnvironmentSourceRow>
                        size="small"
                        rowKey={(r) => r.id || r.名称}
                        columns={ZNWD_ENVIRONMENT_SOURCE_COLUMNS}
                        dataSource={environmentSources}
                        pagination={{ pageSize: 10, showSizeChanger: environmentSources.length > 10 }}
                        scroll={{ x: 1000, y: 420 }}
                        expandable={{
                          expandedRowKeys: expandedEnvSourceKey ? [expandedEnvSourceKey] : [],
                          onExpand: (expanded, record) => {
                            const key = record.id || record.名称;
                            setExpandedEnvSourceKey(expanded ? key : null);
                          },
                          expandedRowRender: (record) => (
                            <div className="znwd-env-source-panel" style={{ padding: '8px 4px' }}>
                              <style>{ENVIRONMENT_HTML_TABLE_STYLES}</style>
                              {(['分类', '分级', '典型组合', '约束', '要求'] as const).map((field) => {
                                const val = record[field];
                                if (!val || val === '—') return null;
                                return (
                                  <div key={field} style={{ marginBottom: 12 }}>
                                    <Text strong style={{ display: 'block', marginBottom: 4 }}>
                                      {field}
                                    </Text>
                                    <EnvironmentHtmlField value={String(val)} />
                                  </div>
                                );
                              })}
                            </div>
                          ),
                          rowExpandable: (record) =>
                            (['分类', '分级', '典型组合', '约束', '要求'] as const).some(
                              (f) => record[f] && record[f] !== '—'
                            ),
                        }}
                      />
                    )
                  ) : (
                    <Empty description="未检索到试验环境节点，可点击刷新重试" />
                  )}
                </Spin>
              ) : sourceViewMode === 'equipment_hierarchy' ? (
                <ZnwdEquipmentHierarchyTable
                  rows={equipmentHierarchyRows}
                  loading={sourceTableLoading}
                  highlightEquipmentIds={sourceHighlightEquipmentIds}
                  highlightLevel2TypeIds={sourceHighlightLevel2TypeIds}
                />
              ) : (
              <>
              <EnvironmentTable
                mode="ssyq"
                dataSource={sourceTableRows}
                selectedProjects={selectedProjectsInSource}
                selectedIndicators={selectedIndicatorsInSource}
                highlightEquipmentRowIds={sourceHighlightEquipmentIds}
                highlightProjectIds={sourceHighlightProjectIds}
                highlightIndicatorIds={sourceHighlightIndicatorIds}
                onProjectSelect={(entry, checked) => {
                  setSelectedProjectsInSource((prev) => {
                    const exists = prev.some((p) => p.projectId === entry.projectId && p.equipmentTypeId === entry.equipmentTypeId);
                    if (checked && !exists) {
                      const next = [...prev, entry];
                      fetchSourceMethods(
                        Array.from(new Set(next.map((x) => x.projectId))),
                        Array.from(new Set(selectedIndicatorsInSource.map((x) => x.indicatorId))),
                      );
                      return next;
                    }
                    if (!checked) {
                      const next = prev.filter((p) => !(p.projectId === entry.projectId && p.equipmentTypeId === entry.equipmentTypeId));
                      fetchSourceMethods(
                        Array.from(new Set(next.map((x) => x.projectId))),
                        Array.from(new Set(selectedIndicatorsInSource.map((x) => x.indicatorId))),
                      );
                      return next;
                    }
                    return prev;
                  });
                }}
                onIndicatorSelect={(entry, checked) => {
                  setSelectedIndicatorsInSource((prev) => {
                    const exists = prev.some((i) => i.indicatorId === entry.indicatorId && i.equipmentTypeId === entry.equipmentTypeId);
                    if (checked && !exists) {
                      const next = [...prev, entry];
                      fetchSourceMethods(
                        Array.from(new Set(selectedProjectsInSource.map((x) => x.projectId))),
                        Array.from(new Set(next.map((x) => x.indicatorId))),
                      );
                      return next;
                    }
                    if (!checked) {
                      const next = prev.filter((i) => !(i.indicatorId === entry.indicatorId && i.equipmentTypeId === entry.equipmentTypeId));
                      fetchSourceMethods(
                        Array.from(new Set(selectedProjectsInSource.map((x) => x.projectId))),
                        Array.from(new Set(next.map((x) => x.indicatorId))),
                      );
                      return next;
                    }
                    return prev;
                  });
                }}
                singleSelect={sourceTableInvolvesMethod}
                loading={sourceTableLoading}
                emptyText="暂未匹配到清单记录，可点击刷新或切换到「文件」页"
                scrollY={400}
              />
              <div>
                <Text strong style={{ marginBottom: 8, display: 'block' }}>
                  方法列表
                </Text>
                <MethodList
                  methods={sourceMethods}
                  selectedMethodIds={selectedMethodIdsInSource}
                  onMethodSelect={(methodId, checked) => {
                    if (checked) setSelectedMethodIdsInSource((prev) => [...prev, methodId]);
                    else setSelectedMethodIdsInSource((prev) => prev.filter((id) => id !== methodId));
                  }}
                  singleSelect={true}
                  loading={false}
                  emptyText={
                    sourceMethods.length === 0 &&
                    (selectedProjectsInSource.length > 0 || selectedIndicatorsInSource.length > 0)
                      ? '当前勾选下未返回方法数据，可尝试减少勾选或点击刷新'
                      : '在上方勾选项目或指标后可查询方法'
                  }
                />
              </div>
              </>
              )}
            </div>
          );

          const filePanel = (
            <div>
              {/* <Text strong style={{ marginBottom: 8, display: 'block' }}>文件检索结果</Text> */}
              {/* {sourceDrawerPrimary && (sourceDrawerPrimary.title || sourceDrawerPrimary.doc_id) ? (
                <Alert
                  type="info"
                  showIcon
                  style={{ marginBottom: 12 }}
                  message="模型判定：对回答最有用的来源"
                  description={
                    <div>
                      <div>
                        <Text strong>{sourceDrawerPrimary.title || sourceDrawerPrimary.doc_id}</Text>
                        {sourceDrawerPrimary.file_format ? (
                          <Tag color="blue" style={{ marginLeft: 8 }}>
                            {formatSourceFileLabel(sourceDrawerPrimary.file_format)}
                          </Tag>
                        ) : null}
                      </div>
                      {sourceDrawerPrimary.reason ? (
                        <Paragraph style={{ marginBottom: 4, marginTop: 8 }}>{sourceDrawerPrimary.reason}</Paragraph>
                      ) : null}
                      {sourceDrawerPrimary.minio_path ? (
                        <Text type="secondary" copyable style={{ wordBreak: 'break-all' }}>
                          {sourceDrawerPrimary.minio_path}
                        </Text>
                      ) : null}
                    </div>
                  }
                />
              ) : null} */}
              {fileItems.length === 0 && sourceDrawerChromaRaw.length > 0 ? (
                <Text type="warning" style={{ display: 'block', marginBottom: 8, fontSize: 12 }}>
                  已命中 {sourceDrawerChromaRaw.length} 条片段；部分内容因编码检测未通过过滤，以下为原始预览。
                </Text>
              ) : null}
              {filePreviewItems.length > 1 ? (
                (() => {
                  const tabItems = filePreviewItems.map((item, index) => ({
                    key: `${String(item.doc_id || 'noid')}::__${index}`,
                    label: (item.title || item.doc_id || `片段${index + 1}`).slice(0, 32),
                    children: <FilePreviewPane item={item} />,
                  }));
                  const firstKey = tabItems[0]?.key || '';
                  const activeKey =
                    sourceDrawerFileTabKey && tabItems.some((t) => t.key === sourceDrawerFileTabKey)
                      ? sourceDrawerFileTabKey
                      : firstKey;
                  return (
                    <Tabs
                      activeKey={activeKey}
                      onChange={(k) => setSourceDrawerFileTabKey(k)}
                      items={tabItems}
                    />
                  );
                })()
              ) : filePreviewItems.length === 1 ? (
                <FilePreviewPane item={filePreviewItems[0]} />
              ) : (
                <Text type="secondary">未检索到文件结果</Text>
              )}
            </div>
          );

          if (hasGraph && hasFiles) {
            return (
              <Tabs
                items={[
                  { key: 'graph', label: '清单文件', children: graphPanel },
                  { key: 'file', label: '文件', children: filePanel },
                ]}
              />
            );
          }
          if (hasGraph) return graphPanel;
          if (hasFiles) return filePanel;
          return <Empty description="暂无可展示来源" image={Empty.PRESENTED_IMAGE_SIMPLE} />;
        })()}
      </Drawer>
      <Modal
        title="节点信息"
        open={nodeInfoModalOpen}
        onCancel={() => setNodeInfoModalOpen(false)}
        getContainer={typeof window !== 'undefined' ? document.body : undefined}
        zIndex={1100}
        footer={[
          <Button key="close" onClick={() => setNodeInfoModalOpen(false)}>关闭</Button>,
          <Button key="neighbors" type="primary" loading={exploringNeighbors} onClick={handleExploreNeighbors}>
            查看邻居节点
          </Button>,
        ]}
        width={640}
      >
        {activeNodeInfo ? (
          <>
            <Descriptions column={1} size="small" bordered>
              <Descriptions.Item label="节点ID">{String(activeNodeInfo.id)}</Descriptions.Item>
              <Descriptions.Item label="名称">{activeNodeInfo.label}</Descriptions.Item>
              <Descriptions.Item label="类型">{activeNodeInfo.group}</Descriptions.Item>
            </Descriptions>
            <div style={{ marginTop: 12, maxHeight: 320, overflowY: 'auto' }}>
              <Text strong>属性</Text>
              <pre style={{ marginTop: 8, background: '#fafafa', border: '1px solid #f0f0f0', borderRadius: 6, padding: 10, fontSize: 12 }}>
                {JSON.stringify(activeNodeInfo.properties || {}, null, 2)}
              </pre>
            </div>
          </>
        ) : null}
      </Modal>
      <Drawer
        title="兴趣配置"
        size={420}
        open={interestDrawerOpen}
        onClose={() => setInterestDrawerOpen(false)}
        destroyOnHidden
      >
        <Spin spinning={interestLoadOptions}>
          <Space direction="vertical" size="middle" style={{ width: '100%' }}>
            <div style={{ width: '100%' }}>
              <Text strong>关注一级设备类型</Text>
              <Select
                mode="multiple"
                allowClear
                style={{ width: '100%', marginTop: 8 }}
                placeholder="选择关心的一级设备类型（与 EquipmentType.Type 一致，用于结果软排序）"
                value={interestEquipSelected}
                onChange={(v) => setInterestEquipSelected(v)}
                options={interestEquipOptions.map((o) => ({
                  value: o.id,
                  label: o.name ? String(o.name) : o.id,
                }))}
                showSearch
                optionFilterProp="label"
                maxTagCount="responsive"
              />
            </div>
            {/* <div style={{ width: '100%' }}>
              <Text strong>关注环境</Text>
              <Select
                mode="multiple"
                allowClear
                style={{ width: '100%', marginTop: 8 }}
                placeholder="选择关心的试验环境"
                value={interestEnvSelected}
                onChange={(v) => setInterestEnvSelected(v)}
                options={interestEnvOptions.map((o) => ({
                  value: o.id,
                  label: o.name ? String(o.name) : o.id,
                }))}
                showSearch
                optionFilterProp="label"
                maxTagCount="responsive"
              />
            </div> */}
            <Button type="primary" loading={interestSaving} onClick={() => void handleSaveInterest()}>
              保存
            </Button>
          </Space>
        </Spin>
      </Drawer>
      <ZnwdAppearanceSettings
        open={appearanceDrawerOpen}
        onClose={() => setAppearanceDrawerOpen(false)}
        appearance={appearance}
        onChange={updateAppearance}
        locale={appearance.locale}
      />
    </Layout>
  );
}
