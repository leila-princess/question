'use client';

import { useState, useRef, useEffect, useCallback, Suspense } from 'react';
import { message, Typography, Spin, Empty, Button, Space, Tag, Input, Card, Divider, Progress, Modal, Select, Segmented } from 'antd';
import { HistoryOutlined, SaveOutlined, StopOutlined, CheckCircleOutlined, MenuUnfoldOutlined, MenuFoldOutlined } from '@ant-design/icons';
import { useSearchParams } from 'next/navigation';
import HistoryList from './components/HistoryList';
import ChatMessage from './components/ChatMessage';
import ChatInput from './components/ChatInput';
import ContentDrawer from './components/ContentDrawer';
import RegionRewriteDrawer from './components/RegionRewriteDrawer';
import TemplateSidebar from './components/TemplateSidebar';
import ConfigModal from './components/ConfigModal';
import PlanOutlinePanel from './components/PlanOutlinePanel';
import MarkdownRenderer from '@/components/MarkdownRenderer';
import SchemeKmSyncModal from '../components/SchemeKmSyncModal';
import { buildJyllmKmMeta, type SchemeExportFormat } from '../lib/schemeKmMeta';
import { downloadBlob, exportJyllmBlob, fileFromExport } from '../lib/schemeKmSync';
import { buildDefaultSchemeSaveName, resolveUniqueSchemeName } from './utils/schemeNaming';
import type { UploadFileMeta } from '@/app/ZSGL/WJGL/utils/wjglUploadPipeline';
import {
  fetchEquipmentTree, fetchProjects, fetchIndicators, fetchMethods,
  fetchSchemes, fetchScheme, saveScheme, updateScheme, submitFeedback,
  // 环境配置步骤已下线：暂不请求环境列表 / 资料员接口，保留导入备恢复
  // fetchEnvironments,
  // callLibrarian,
} from './api';
import type {
  ChatMessage as ChatMessageType,
  FlowStep,
  ChapterModule,
  DeviceSelection,
  EnvironmentSelection,
  PlannerPrompt,
  OutlineTreeNode,
  OutlineSelection,
  RegionRewriteSpec,
  RegionRevisionRecord,
} from './types/flow';
import { buildFullSchemeExcerpt, buildRegionRewriteBody } from './lib/regionRewrite';
import { JyllmTagStreamBuffer } from './lib/jyllmTagStreamBuffer';
import { useTypewriterReveal } from './hooks/useTypewriterReveal';
import { buildJyllmFullSchemeMarkdown } from './lib/chapterStreamDisplay';
import { buildJyllmIndicatorsPayload, buildJyllmProjectsPayload, splitProjectForestByEnvironment } from '../lib/projectTree';

const { Title, Text } = Typography;

const SCHEME_PANEL_WIDTH_LS = 'jyllm.schemePanelWidth';
const JYLLM_LLM_STORAGE_KEY = 'jyllm_llm_model_key';
const JYLLM_MODE_STORAGE_KEY = 'jyllm_generation_mode';
type GenerationMode = 'config' | 'pure_llm';

/** true：确定性骨架 + 规则/按需 LLM 填充（主路径）；false：旧 Planner/Reviewer */
const JYLLM_ENGINE_V2 = true;

function generateId() {
  return `msg_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
}

function buildPlannerMarkdownFromPrompts(prompts: PlannerPrompt[]): string {
  if (!prompts.length) return '';
  return prompts
    .map(
      (p) =>
        `## ${p.chapter_title}\n### 写作要点\n${p.writer_prompt || ''}\n### 评审要点\n${p.reviewer_prompt || ''}`,
    )
    .join('\n\n---\n\n');
}

function parseSSEStream(buffer: string): { events: Array<{ type: string; data: any }>; remaining: string } {
  const events: Array<{ type: string; data: any }> = [];
  const blocks = buffer.split(/\r?\n\r?\n/);
  const remaining = blocks.pop() ?? '';

  for (const block of blocks) {
    if (!block.trim()) continue;
    const lines = block.split(/\r?\n/);
    let eventType = '';
    const dataLines: string[] = [];
    for (const rawLine of lines) {
      const line = rawLine.trimEnd();
      if (line.startsWith('event:')) {
        eventType = line.slice(6).trim();
      } else if (line.startsWith('data:')) {
        dataLines.push(line.slice(5).trimStart());
      }
    }
    if (!eventType || dataLines.length === 0) continue;
    const dataStr = dataLines.join('\n');
    try {
      events.push({ type: eventType, data: JSON.parse(dataStr) });
    } catch {
      // 常见原因：TCP 分包导致半包 JSON；保留在 remaining 中等待后续字节（由调用方与 remaining 拼接）
    }
  }

  return { events, remaining };
}

/** 纯 LLM 流式方案：按 ## 1. / ## 2. 拆章 */
function splitPureLlmSchemeMarkdown(md: string): { ch1: string; ch2: string } {
  const m = md.match(/^([\s\S]*?)(##\s*2\.[\s\S]*)$/);
  if (m) return { ch1: m[1].trim(), ch2: m[2].trim() };
  return { ch1: md, ch2: '' };
}

function pureLlmModulesFromSchemeBuf(buf: string, status: 'generating' | 'complete' = 'generating'): ChapterModule[] {
  const { ch1, ch2 } = splitPureLlmSchemeMarkdown(buf);
  const base = {
    thinking: '',
    keywords: '',
    searchContext: '',
    score: 0,
    status,
  } as const;
  const mods: ChapterModule[] = [];
  if (ch1 || !ch2) {
    mods.push({ index: 1, title: '1. 试验依据', content: ch1, ...base });
  }
  if (ch2) {
    mods.push({ index: 2, title: '2. 试验内容及方法', content: ch2, ...base });
  }
  return mods;
}

const METHOD_SENTINEL_PREFIX = '<!--JYLLM_METHOD:';
const METHOD_SENTINEL_SUFFIX = '-->';

function methodSentinel(mid: string) {
  return `${METHOD_SENTINEL_PREFIX}${mid}${METHOD_SENTINEL_SUFFIX}`;
}

/** 将骨架中各方法占位符替换为已完成正文或当前流式缓冲，便于边生成边展示 */
function composeChapter2Markdown(
  skeleton: string,
  methodOrder: string[],
  completed: Record<string, string>,
  streamMid: string | null,
  streamBuf: string,
): string {
  let s = skeleton;
  for (const mid of methodOrder) {
    const sen = methodSentinel(mid);
    if (!s.includes(sen)) continue;
    if (completed[mid] !== undefined) {
      s = s.replace(sen, completed[mid]);
    } else if (streamMid === mid && streamBuf) {
      s = s.replace(sen, streamBuf);
    }
  }
  return s;
}




function JYLLMConfigPageContent() {
  const searchParams = useSearchParams();
  const [messages, setMessages] = useState<ChatMessageType[]>([]);
  const [currentStep, setCurrentStep] = useState<FlowStep>('device');
  const [isStreaming, setIsStreaming] = useState(false);
  const [showAllMessages, setShowAllMessages] = useState(true);
  const [historyCollapsed, setHistoryCollapsed] = useState(false);

  const [flatDevices, setFlatDevices] = useState<{ key: string; name: string; category: string; type?: string }[]>([]);
  const [deviceSelection, setDeviceSelection] = useState<DeviceSelection>({ searchKey: '', selectedDevices: [], modelInput: '' });
  const [envSelection, setEnvSelection] = useState<EnvironmentSelection>({ selectedEnvs: [], envConfig: '', librarianSuggestion: '' });
  const [selectedEquipmentKeys, setSelectedEquipmentKeys] = useState<string[]>([]);
  const [selectedEquipmentNodes, setSelectedEquipmentNodes] = useState<any[]>([]);
  const [selectedProjects, setSelectedProjects] = useState<any[]>([]);
  const [selectedIndicators, setSelectedIndicators] = useState<any[]>([]);
  const [selectedMethods, setSelectedMethods] = useState<any[]>([]);
  const [uploadedFiles, setUploadedFiles] = useState<any[]>([]);
  const [selectedUploadedFiles, setSelectedUploadedFiles] = useState<string[]>([]);

  const [projectMatrix, setProjectMatrix] = useState<Record<string, any[]>>({});
  const [indicatorMatrix, setIndicatorMatrix] = useState<Record<string, any[]>>({});
  const [matrixLoading, setMatrixLoading] = useState(false);

  const [allProjects, setAllProjects] = useState<any[]>([]);
  const [allIndicators, setAllIndicators] = useState<any[]>([]);
  const [allMethods, setAllMethods] = useState<any[]>([]);

  const [modules, setModules] = useState<ChapterModule[]>([]);
  const [taskId, setTaskId] = useState('');
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [schemePanelWidth, setSchemePanelWidth] = useState(440);
  const [drawerMode, setDrawerMode] = useState<'generating' | 'reviewing' | 'complete'>('generating');
  const [plannerPrompts, setPlannerPrompts] = useState<PlannerPrompt[]>([]);
  /** Planner [CONTENT] 原文或回显用 Markdown（旧 Planner 路径） */
  const [plannerMarkdown, setPlannerMarkdown] = useState('');
  /** V2：仅方案骨架（无占位符），确认大纲时与思考一并保存 */
  const [plannerSkeletonTarget, setPlannerSkeletonTarget] = useState('');
  /** V2 规划流：整包 Markdown 目标 + 打字机展示（先缓冲再展示） */
  const [planThinkStreamTarget, setPlanThinkStreamTarget] = useState('');
  const [planThinkStreamAnim, setPlanThinkStreamAnim] = useState(false);
  const planThinkStreamShown = useTypewriterReveal(planThinkStreamTarget, planThinkStreamAnim, {
    maxStep: 28,
    intervalMs: 18,
  });
  const [planOutlineStreamTarget, setPlanOutlineStreamTarget] = useState('');
  const [planOutlineStreamAnim, setPlanOutlineStreamAnim] = useState(false);
  const planOutlineStreamShown = useTypewriterReveal(planOutlineStreamTarget, planOutlineStreamAnim, {
    maxStep: 40,
    intervalMs: 18,
  });
  const [isReviewingPrompts, setIsReviewingPrompts] = useState(false);
  const [currentGeneratingChapter, setCurrentGeneratingChapter] = useState<number | null>(null);
  const [generationProgress, setGenerationProgress] = useState(0);
  const [appliedTemplateName, setAppliedTemplateName] = useState('');
  const [appliedTemplateContent, setAppliedTemplateContent] = useState('');
  const [schemeNameInput, setSchemeNameInput] = useState('');
  const [showSchemeNameModal, setShowSchemeNameModal] = useState(false);
  const [configModalOpen, setConfigModalOpen] = useState(false);
  const [configModalTableRows, setConfigModalTableRows] = useState<any[]>([]);
  const [configModalEnvNames, setConfigModalEnvNames] = useState<string[]>([]);
  const [deviceEnvMap, setDeviceEnvMap] = useState<Record<string, string[]>>({});
  // 环境配置步骤下线：资料员 loading 状态暂不使用（原 UI 已注释）
  // const [envLibrarianLoading, setEnvLibrarianLoading] = useState(false);
  const [envLibrarianResults, setEnvLibrarianResults] = useState<Record<string, { suggestion: string; reasoning: string }>>({});
  const [envConfigEdit, setEnvConfigEdit] = useState('');

  const [llmModelKey, setLlmModelKey] = useState('');
  const [llmOptions, setLlmOptions] = useState<{ label: string; value: string }[]>([]);
  // 模式选择已注释：固定为配置模式
  // const [generationMode, setGenerationMode] = useState<GenerationMode>(() => {
  //   if (typeof window === 'undefined') return 'config';
  //   const m = localStorage.getItem(JYLLM_MODE_STORAGE_KEY);
  //   return m === 'pure_llm' ? 'pure_llm' : 'config';
  // });
  const generationMode: GenerationMode = 'config';
  const [pureLlmFigures, setPureLlmFigures] = useState<Record<string, string>>({});

  const [deviceDisplayName, setDeviceDisplayName] = useState('');
  const [environmentConditionsText, setEnvironmentConditionsText] = useState('');
  const [outlineTree, setOutlineTree] = useState<OutlineTreeNode[]>([]);
  const [outlineSelection, setOutlineSelection] = useState<OutlineSelection | null>(null);
  const [feedbackRewriteLoading, setFeedbackRewriteLoading] = useState(false);
  const [overallScore, setOverallScore] = useState(90);
  const [overallComment, setOverallComment] = useState('');
  const [historyViewTaskId, setHistoryViewTaskId] = useState<string | null>(null);
  const [historyRefreshKey, setHistoryRefreshKey] = useState(0);
  const [historyViewFeedback, setHistoryViewFeedback] = useState<{ score?: number | null; comment?: string | null }>({});
  const [exportingKind, setExportingKind] = useState<SchemeExportFormat | null>(null);
  const [syncingToKm, setSyncingToKm] = useState(false);
  const [kmSyncModalOpen, setKmSyncModalOpen] = useState(false);
  const [kmSyncFile, setKmSyncFile] = useState<File | null>(null);
  const [kmSyncMeta, setKmSyncMeta] = useState<UploadFileMeta | null>(null);
  const [regionRewriteOpen, setRegionRewriteOpen] = useState(false);
  const [regionRewriteSpec, setRegionRewriteSpec] = useState<RegionRewriteSpec | null>(null);
  const [regionRewritePreview, setRegionRewritePreview] = useState('');
  const [regionRewriteComment, setRegionRewriteComment] = useState('');
  const [regionRewriteStreaming, setRegionRewriteStreaming] = useState('');
  const [regionRewriteLoading, setRegionRewriteLoading] = useState(false);
  const [regionRewriteMerged, setRegionRewriteMerged] = useState<string | null>(null);
  const [regionRewriteOriginal, setRegionRewriteOriginal] = useState('');
  const [regionRewriteDiffConfirmed, setRegionRewriteDiffConfirmed] = useState(false);
  const [regionRewriteWarnings, setRegionRewriteWarnings] = useState<string[]>([]);
  const [regionRewriteDiffStats, setRegionRewriteDiffStats] = useState<{
    similarity?: number;
    lines_added?: number;
    lines_removed?: number;
  } | null>(null);
  const [revisionHistory, setRevisionHistory] = useState<RegionRevisionRecord[]>([]);

  const chatBoxRef = useRef<HTMLDivElement>(null);
  const schemePanelWidthRef = useRef(440);
  const schemeResizeRef = useRef<{ startX: number; startW: number } | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const filteredMethodsRef = useRef<any[]>([]);
  const autoSaveTimerRef = useRef<NodeJS.Timeout | null>(null);
  /** 供「重新生成」读取最新章节正文（含右侧编辑回写） */
  const modulesRef = useRef<ChapterModule[]>([]);
  /** V2：plan_stream / build_skeleton 成功后为 true，startGeneration 走 generate-content */
  const useDeterministicEngineRef = useRef(false);
  /** V2：规划流思考 Markdown 累积，确认大纲时与骨架一并展示 */
  const planStreamThinkingRef = useRef('');
  /** V2：规划流对应助手消息 id，用于把打字机内容同步到「思考过程」 */
  const planStreamMsgIdRef = useRef<string | null>(null);

  const autoSaveHistory = useCallback(async (status: string = 'generating', schemeName?: string) => {
    if (!taskId) return;
    
    try {
      const name = schemeName || appliedTemplateName || `方案 ${taskId.slice(0, 8)}`;
      
      await fetch('/api/fzsj/jyllm/save-draft', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          task_id: taskId,
          user_id: 'admin',
          scheme_name: name,
          current_step: currentStep,
          messages: messages,
          planner_prompts: plannerPrompts,
          selected_equipment: selectedEquipmentNodes,
          selected_environments: configModalEnvNames,
          selected_projects: selectedProjects,
          selected_indicators: selectedIndicators,
          selected_methods: selectedMethods,
          selected_equipment_keys: selectedEquipmentKeys,
          chapters: modules,
          page_type: 'jyllm',
          generation_status: status,
          device_display_name: deviceDisplayName,
          environment_conditions_text: environmentConditionsText,
          outline_selection: outlineSelection,
          overall_score: overallScore,
          overall_comment: overallComment,
        }),
      });
    } catch (err) {
      console.error('自动保存失败:', err);
    }
  }, [taskId, currentStep, messages, plannerPrompts, selectedEquipmentNodes, configModalEnvNames, selectedProjects, selectedIndicators, selectedMethods, selectedEquipmentKeys, modules, appliedTemplateName, deviceDisplayName, environmentConditionsText, outlineSelection, overallScore, overallComment]);

  const debouncedAutoSave = useCallback((status: string = 'generating') => {
    if (autoSaveTimerRef.current) {
      clearTimeout(autoSaveTimerRef.current);
    }
    autoSaveTimerRef.current = setTimeout(() => {
      autoSaveHistory(status);
    }, 1000);
  }, [autoSaveHistory]);

  useEffect(() => {
    (async () => {
      try {
        // const [tree, envs] = await Promise.all([fetchEquipmentTree(), fetchEnvironments()]);
        const tree = await fetchEquipmentTree();
        const devices: { key: string; name: string; category: string; type?: string }[] = [];
        for (const cat of (tree as any[]) || []) {
          const catName = cat.title || '';
          for (const t of cat.children || []) {
            const typeName = t.title || '';
            for (const eq of t.children || []) {
              if (eq.isLeaf) {
                devices.push({
                  key: eq.key || '',
                  name: eq.title || '',
                  category: catName,
                  type: typeName,
                });
              }
            }
          }
        }
        setFlatDevices(devices);
      } catch (err) {
        console.error('Failed to fetch equipment tree:', err);
      }
    })();
  }, []);

  useEffect(() => {
    modulesRef.current = modules;
  }, [modules]);

  useEffect(() => {
    try {
      const saved = typeof window !== 'undefined' ? localStorage.getItem(JYLLM_LLM_STORAGE_KEY) : '';
      if (saved) setLlmModelKey(saved);
    } catch {
      /* ignore */
    }
    (async () => {
      try {
        const res = await fetch('/api/xtgl/mxgl/llm-models?active_only=1');
        const j = await res.json();
        const rows = (j?.data || []) as { model_key: string; model_name: string; description?: string }[];
        setLlmOptions(
          rows.map((r) => ({
            value: r.model_key,
            label: `${r.model_name} (${r.model_key})`,
          })),
        );
      } catch {
        /* ignore */
      }
    })();
  }, []);

  useEffect(() => {
    const taskIdParam = searchParams.get('task_id');
    const loadHistory = searchParams.get('load_history');
    if (taskIdParam && loadHistory === 'true') {
      handleHistoryClick(taskIdParam);
    }
  }, [searchParams]);

  useEffect(() => {
    if (messages.length === 0) {
      // 固定为配置模式
      const intro = '你好！我是辅助设计助手，将帮助您设计试验方案。\n\n请点击下方"配置试验参数"按钮，选择设备、项目、指标和方法。';
      setMessages([{
        id: generateId(),
        role: 'assistant',
        content: intro,
        contentType: 'text',
        timestamp: new Date().toISOString(),
        metadata: { agent: 'planner' }
      }]);
    }
  }, []);

  useEffect(() => {
    if (chatBoxRef.current) {
      chatBoxRef.current.scrollTop = chatBoxRef.current.scrollHeight;
    }
  }, [messages]);

  useEffect(() => {
    schemePanelWidthRef.current = schemePanelWidth;
  }, [schemePanelWidth]);

  useEffect(() => {
    try {
      const raw = localStorage.getItem(SCHEME_PANEL_WIDTH_LS);
      const n = raw ? parseInt(raw, 10) : NaN;
      if (Number.isFinite(n) && n >= 280 && n <= 2400) {
        schemePanelWidthRef.current = n;
        setSchemePanelWidth(n);
      }
    } catch {
      /* ignore */
    }
  }, []);

  const onSchemeResizeMouseDown = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      schemeResizeRef.current = { startX: e.clientX, startW: schemePanelWidthRef.current };
      document.body.style.cursor = 'col-resize';
      document.body.style.userSelect = 'none';

      const onMove = (ev: MouseEvent) => {
        const d = schemeResizeRef.current;
        if (!d) return;
        const historyW = historyCollapsed ? 36 : 260;
        const minChat = 280;
        const maxW = Math.max(280, window.innerWidth - historyW - minChat - 12);
        const delta = d.startX - ev.clientX;
        const next = Math.min(Math.max(d.startW + delta, 280), maxW);
        setSchemePanelWidth(next);
      };

      const onUp = () => {
        schemeResizeRef.current = null;
        document.body.style.cursor = '';
        document.body.style.userSelect = '';
        window.removeEventListener('mousemove', onMove);
        window.removeEventListener('mouseup', onUp);
        try {
          localStorage.setItem(SCHEME_PANEL_WIDTH_LS, String(schemePanelWidthRef.current));
        } catch {
          /* ignore */
        }
      };

      window.addEventListener('mousemove', onMove);
      window.addEventListener('mouseup', onUp);
    },
    [historyCollapsed],
  );

  const addAIMessage = useCallback((content: string, contentType: ChatMessageType['contentType'] = 'text', extra?: Partial<ChatMessageType>) => {
    if (!content || !content.trim()) return '';
    const msg: ChatMessageType = {
      id: generateId(),
      role: 'assistant',
      content,
      contentType,
      timestamp: new Date().toISOString(),
      ...extra,
    };
    setMessages(prev => [...prev, msg]);
    return msg.id;
  }, []);

  const updateAIMessage = useCallback((id: string, updater: (msg: ChatMessageType) => ChatMessageType) => {
    setMessages(prev => prev.map(m => m.id === id ? updater(m) : m));
  }, []);

  useEffect(() => {
    const mid = planStreamMsgIdRef.current;
    if (!mid) return;
    updateAIMessage(mid, (msg) => ({
      ...msg,
      thinking: [
        {
          agent: 'planner',
          content: planThinkStreamShown,
          timestamp: msg.thinking?.[0]?.timestamp || new Date().toISOString(),
          isCollapsed: msg.thinking?.[0]?.isCollapsed ?? false,
        },
      ],
    }));
  }, [planThinkStreamShown, updateAIMessage]);

  const addUserMessage = useCallback((content: string) => {
    const msg: ChatMessageType = {
      id: generateId(),
      role: 'user',
      content,
      contentType: 'text',
      timestamp: new Date().toISOString(),
    };
    setMessages(prev => [...prev, msg]);
    return msg.id;
  }, []);

  /**
   * 调用 Planner 流式生成大纲。环境配置步骤下线后，由「配置试验参数」保存后直接触发，
   * 故必须传入当次勾选的 equipment/projects/...，避免闭包读到尚未提交的 state。
   */
  const runPlannerStream = useCallback(
    async (args: {
      taskIdForRequest: string;
      equipment: any[];
      projects: any[];
      indicators: any[];
      methods: any[];
      environments?: Array<{ name: string; projects?: any[] }>;
      environment_config?: string;
    }) => {
      const environment_config = args.environment_config ?? '';
      const environments =
        args.environments ??
        (args as { envNames?: string[] }).envNames?.map((n) => ({ name: n })) ??
        [];
      useDeterministicEngineRef.current = false;
      setCurrentStep('planner_thinking');
      setPlannerMarkdown('');
      setIsStreaming(true);

      const payload = {
        task_id: args.taskIdForRequest,
        equipment: args.equipment,
        environments,
        projects: args.projects,
        indicators: args.indicators,
        methods: args.methods,
        environment_config,
        uploaded_files: [] as any[],
        template_override: appliedTemplateContent || undefined,
        llm_model_key: llmModelKey || undefined,
      };

      const msgId = addAIMessage('正在规划试验方案大纲...', 'thinking', {
        thinking: [],
        isStreaming: true,
        metadata: { agent: 'planner' },
      });

      try {
        const response = await fetch('/api/fzsj/jyllm/planner', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });

        if (!response.ok) {
          const errText = await response.text();
          throw new Error(`HTTP ${response.status}: ${errText}`);
        }

        const reader = response.body?.getReader();
        if (!reader) throw new Error('No reader');
        const decoder = new TextDecoder();
        let sseBuffer = '';
        const plannerOutlineBuf = new JyllmTagStreamBuffer('content');
        const plannerChatThinkBuf = new JyllmTagStreamBuffer('thinking');

        const handlePlannerSseEvent = (ev: { type: string; data: any }) => {
          const data = ev.data;
          if (ev.type === 'thinking') {
            const agent = data.agent || 'planner';
            const content = data.content || '';
            const cleanedContent = content.replace(/▌$/, '');
            if (!cleanedContent) return;

            if (agent === 'planner_outline') {
              const delta = plannerOutlineBuf.append(cleanedContent);
              if (delta) setPlannerMarkdown((prev) => prev + delta);
              return;
            }

            const thinkDelta = plannerChatThinkBuf.append(cleanedContent);
            if (!thinkDelta) return;

            updateAIMessage(msgId, (msg) => {
              const existing = msg.thinking || [];
              const last = existing.length > 0 ? existing[existing.length - 1] : null;
              const mergeAgents = new Set(['planner', 'context_compress', 'writer', 'reviewer']);
              if (last && mergeAgents.has(agent) && last.agent === agent) {
                const updated = [...existing];
                updated[updated.length - 1] = {
                  ...last,
                  content: last.content + thinkDelta,
                };
                return { ...msg, thinking: updated };
              }

              const isContinuation =
                existing.length > 0 &&
                existing[existing.length - 1].agent === agent &&
                !thinkDelta.includes('\n') &&
                existing[existing.length - 1].content.endsWith('...');

              if (isContinuation) {
                const updated = [...existing];
                updated[updated.length - 1] = {
                  ...updated[updated.length - 1],
                  content: updated[updated.length - 1].content + thinkDelta,
                };
                return { ...msg, thinking: updated };
              }
              return {
                ...msg,
                thinking: [
                  ...existing,
                  {
                    agent,
                    content: thinkDelta,
                    timestamp: new Date().toISOString(),
                    isCollapsed: false,
                  },
                ],
              };
            });
            return;
          }

          if (ev.type === 'planner_complete') {
            const oTail = plannerOutlineBuf.flushFinal();
            const tTail = plannerChatThinkBuf.flushFinal();
            plannerOutlineBuf.reset();
            plannerChatThinkBuf.reset();

            if (tTail) {
              updateAIMessage(msgId, (msg) => {
                const existing = msg.thinking || [];
                if (!existing.length) {
                  return {
                    ...msg,
                    thinking: [
                      {
                        agent: 'planner',
                        content: tTail,
                        timestamp: new Date().toISOString(),
                        isCollapsed: false,
                      },
                    ],
                  };
                }
                const u = [...existing];
                u[u.length - 1] = { ...u[u.length - 1], content: u[u.length - 1].content + tTail };
                return { ...msg, thinking: u };
              });
            }

            if (data.chapters && data.chapters.length > 0) {
              const normalizedPrompts = data.chapters.map((ch: any, i: number) => ({
                chapter_index: ch.index ?? i + 1,
                chapter_title: ch.title || ch.chapter_title || `第${ch.index || i + 1}章`,
                writer_prompt: ch.writer_prompt || '',
                reviewer_prompt: ch.reviewer_prompt || '',
              }));
              setPlannerPrompts(normalizedPrompts);
              const rawMd = typeof data.planner_markdown === 'string' ? data.planner_markdown.trim() : '';
              setPlannerMarkdown((prev) => (rawMd ? rawMd + oTail : prev + oTail));
              setIsStreaming(false);
              updateAIMessage(msgId, (msg) => ({
                ...msg,
                isStreaming: false,
                content: '✅ 大纲规划完成，请在下方查看规划要点（Markdown）。',
              }));
              setCurrentStep('planner_review');
            } else {
              throw new Error('Planner未生成有效章节，请重试');
            }
            return;
          }

          if (ev.type === 'error') {
            throw new Error(data.message || 'Planner执行出错');
          }
        };

        while (true) {
          const { done, value } = await reader.read();
          if (done) {
            sseBuffer += decoder.decode();
            break;
          }
          sseBuffer += decoder.decode(value, { stream: true });
          const { events: parsedEvents, remaining } = parseSSEStream(sseBuffer);
          sseBuffer = remaining;

          for (const ev of parsedEvents) {
            handlePlannerSseEvent(ev);
          }
        }

        const { events: tailPlannerEvents } = parseSSEStream(sseBuffer.trim() ? `${sseBuffer}\n\n` : sseBuffer);
        for (const ev of tailPlannerEvents) {
          handlePlannerSseEvent(ev);
        }

        const od = plannerOutlineBuf.flushFinal();
        if (od) setPlannerMarkdown((prev) => prev + od);
        const td = plannerChatThinkBuf.flushFinal();
        if (td) {
          updateAIMessage(msgId, (msg) => {
            const existing = msg.thinking || [];
            if (!existing.length) {
              return {
                ...msg,
                thinking: [
                  {
                    agent: 'planner',
                    content: td,
                    timestamp: new Date().toISOString(),
                    isCollapsed: false,
                  },
                ],
              };
            }
            const u = [...existing];
            u[u.length - 1] = { ...u[u.length - 1], content: u[u.length - 1].content + td };
            return { ...msg, thinking: u };
          });
        }
      } catch (err: any) {
        setIsStreaming(false);
        updateAIMessage(msgId, (msg) => ({
          ...msg,
          isStreaming: false,
          content: `生成大纲失败：${err.message}`,
        }));
      }
    },
    [
      addAIMessage,
      updateAIMessage,
      appliedTemplateContent,
      llmModelKey,
    ],
  );

  const runBuildSkeleton = useCallback(
    async (args: {
      taskIdForRequest: string;
      equipment: any[];
      projects: any[];
      indicators: any[];
      methods: any[];
      envNames?: string[];
      environments?: Array<{ name: string; projects?: any[] }>;
      deviceDisplayName?: string;
      environmentConditionsText?: string;
    }) => {
      setCurrentStep('planner_thinking');
      setPlannerMarkdown('');
      setPlannerSkeletonTarget('');
      setPlanThinkStreamTarget('');
      setPlanOutlineStreamTarget('');
      setPlanThinkStreamAnim(false);
      setPlanOutlineStreamAnim(false);
      planStreamMsgIdRef.current = null;
      planStreamThinkingRef.current = '';
      setIsStreaming(true);
      useDeterministicEngineRef.current = false;

      const msgId = addAIMessage('正在根据图数据库构建确定性章节骨架…', 'thinking', {
        thinking: [],
        isStreaming: true,
        metadata: { agent: 'planner' },
      });
      planStreamMsgIdRef.current = msgId;

      try {
        const environmentsPayload =
          args.environments ??
          (args.envNames || []).map((n) => ({ name: n }));

        const res = await fetch('/api/fzsj/jyllm/plan-stream', {
          method: 'POST',
          cache: 'no-store',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            task_id: args.taskIdForRequest,
            equipment: args.equipment,
            environments: environmentsPayload,
            projects: args.projects,
            indicators: args.indicators,
            methods: args.methods,
            llm_model_key: llmModelKey || undefined,
            device_display_name: args.deviceDisplayName || undefined,
            environment_conditions: args.environmentConditionsText || undefined,
          }),
        });
        if (!res.ok) {
          let detail = '';
          try {
            const ej = await res.json();
            detail = ej.error || ej.detail || JSON.stringify(ej);
          } catch {
            detail = await res.text();
          }
          throw new Error(detail || `HTTP ${res.status}`);
        }
        const reader = res.body?.getReader();
        if (!reader) throw new Error('无法读取规划流');

        const decoder = new TextDecoder();
        let sseBuffer = '';
        let sawComplete = false;

        const processPlanEvent = async (ev: { type: string; data: any }) => {
          const { type, data } = ev;
          if (type === 'plan_building') {
            updateAIMessage(msgId, (msg) => ({
              ...msg,
              content: String(data.message || '正在构建…'),
            }));
            return;
          }
          if (type === 'error') {
            throw new Error(data.message || '规划失败');
          }
          if (type === 'plan_thinking_start') {
            planStreamThinkingRef.current = '';
            setPlanThinkStreamTarget('');
            setPlanThinkStreamAnim(false);
            updateAIMessage(msgId, (msg) => ({
              ...msg,
              thinking: [
                {
                  agent: 'planner',
                  content: '',
                  timestamp: new Date().toISOString(),
                  isCollapsed: false,
                },
              ],
            }));
            return;
          }
          if (type === 'plan_thinking_blob') {
            const md = String(data.markdown || '');
            planStreamThinkingRef.current = md;
            setPlanThinkStreamAnim(false);
            requestAnimationFrame(() => {
              setPlanThinkStreamTarget(md);
              setPlanThinkStreamAnim(true);
            });
            return;
          }
          if (type === 'plan_thinking_done') {
            setPlanThinkStreamAnim(false);
            return;
          }
          if (type === 'plan_outline_start') {
            setPlannerSkeletonTarget('');
            setPlanOutlineStreamTarget('');
            setPlanOutlineStreamAnim(false);
            return;
          }
          if (type === 'plan_outline_blob') {
            const md = String(data.markdown || '');
            setPlannerSkeletonTarget(md);
            setPlanOutlineStreamAnim(false);
            requestAnimationFrame(() => {
              setPlanOutlineStreamTarget(md);
              setPlanOutlineStreamAnim(true);
            });
            return;
          }
          if (type === 'plan_outline_done') {
            setPlanOutlineStreamAnim(false);
            return;
          }
          if (type === 'plan_complete') {
            sawComplete = true;
            useDeterministicEngineRef.current = true;
            setTaskId(data.task_id || args.taskIdForRequest);
            const chapters = (data.chapters || []).map((ch: any, i: number) => {
              const idx = ch.index ?? ch.chapter_index ?? i + 1;
              return {
                chapter_index: idx,
                chapter_title: ch.chapter_title || ch.title || `第${idx}章`,
                writer_prompt: ch.writer_prompt || '',
                reviewer_prompt: ch.reviewer_prompt || '',
              };
            });
            setPlannerPrompts(chapters);
            const sk = typeof data.skeleton_markdown_display === 'string' ? data.skeleton_markdown_display.trim() : '';
            if (sk) {
              setPlannerSkeletonTarget((prev) => (prev && prev.length >= sk.length ? prev : sk));
              setPlanOutlineStreamTarget((prev) => (prev && prev.length >= sk.length ? prev : sk));
            }
            const tree = data.outline_tree;
            if (Array.isArray(tree) && tree.length > 0) {
              setOutlineTree(tree as OutlineTreeNode[]);
            }
          }
        };

        while (true) {
          const { done, value } = await reader.read();
          if (done) {
            sseBuffer += decoder.decode();
            break;
          }
          sseBuffer += decoder.decode(value, { stream: true });
          const { events: parsedEvents, remaining } = parseSSEStream(sseBuffer);
          sseBuffer = remaining;

          for (const ev of parsedEvents) {
            await processPlanEvent(ev);
          }
        }
        if (sseBuffer.trim()) {
          const { events: tailEvents } = parseSSEStream(sseBuffer + '\n\n');
          for (const ev of tailEvents) {
            await processPlanEvent(ev);
          }
        }

        if (!sawComplete) {
          throw new Error('规划流未正常结束（缺少 plan_complete）');
        }

        setIsStreaming(false);
        updateAIMessage(msgId, (msg) => ({
          ...msg,
          isStreaming: false,
          content: '✅ 规则化规划与章节骨架已就绪。请展开「思考过程」查看分析步骤，并在下方确认方案骨架后生成。',
        }));
        setCurrentStep('planner_review');
      } catch (err: any) {
        setIsStreaming(false);
        setPlanThinkStreamAnim(false);
        setPlanOutlineStreamAnim(false);
        useDeterministicEngineRef.current = false;
        updateAIMessage(msgId, (msg) => ({
          ...msg,
          isStreaming: false,
          content: `构建骨架失败：${err.message || err}`,
        }));
        message.error(err.message || '构建骨架失败');
      }
    },
    [addAIMessage, updateAIMessage, llmModelKey],
  );

  const handleConfigModalSave = async (data: {
    selectedEquipmentKeys: string[];
    selectedProjects: any[];
    selectedIndicators: any[];
    selectedMethodIds: string[];
    selectedMethodNodes: any[];
    tableRows: any[];
    envNames: string[];
    projectEnvironmentById?: Record<string, string>;
    deviceDisplayName?: string;
    environmentConditionsText?: string;
  }) => {
    setConfigModalOpen(false);
    
    setSelectedEquipmentKeys(data.selectedEquipmentKeys);
    
    const devNodes = data.selectedEquipmentKeys.map(key => {
      const device = flatDevices.find(d => d.key === key);
      return device ? { id: key, name: device.name, category: device.category, type: device.type } : { id: key, name: key };
    });
    setSelectedEquipmentNodes(devNodes);
    
    const projEnvMap = data.projectEnvironmentById || {};
    const projectForest = buildJyllmProjectsPayload(
      data.selectedProjects,
      data.tableRows,
      projEnvMap,
    );
    const projectsByEnv = splitProjectForestByEnvironment(
      projectForest,
      data.envNames,
      projEnvMap,
      data.tableRows,
    );
    const environmentsPayload = data.envNames.map((name) => ({
      name,
      projects: projectsByEnv.get(name) || [],
    }));
    const projNodes = projectForest;
    const indNodes = buildJyllmIndicatorsPayload(data.selectedIndicators, data.tableRows);
    setSelectedProjects(projNodes);
    setSelectedIndicators(indNodes);
    
    const methodNodes = data.selectedMethodNodes.length > 0 ? data.selectedMethodNodes : data.selectedMethodIds.map(id => ({ id, name: id }));
    setSelectedMethods(methodNodes);
    
    setConfigModalTableRows(data.tableRows);
    setConfigModalEnvNames(data.envNames);
    setDeviceDisplayName(data.deviceDisplayName || '');
    setEnvironmentConditionsText(data.environmentConditionsText || '');
    setOutlineSelection(null);

    /* 环境配置步骤下线：原「设备-环境」矩阵仅用于环境设置页，暂不再写入
    const selectedProjIds = new Set(data.selectedProjects.map(p => p.projectId));
    const devEnvMap: Record<string, string[]> = {};
    for (const row of data.tableRows) {
      const eqId = row.equipmentTypeId;
      const envs = Object.keys(row.projectsByEnv || {}).filter(envName => {
        const projects = row.projectsByEnv[envName];
        return projects && projects.some((p: any) => selectedProjIds.has(p.id));
      });
      if (envs.length > 0) {
        devEnvMap[eqId] = envs;
      }
    }
    setDeviceEnvMap(devEnvMap);
    */
    setDeviceEnvMap({});

    const devNames = devNodes.map((d: any) => d.name);
    const projNames = projNodes.flatMap((p) => {
      const children = (p.children || []) as Array<{ name?: string; id?: string }>;
      if (children.length > 0) {
        return [`${p.name || p.id}（${children.map((c) => c.name || c.id).join('、')}）`];
      }
      return [p.name || p.id];
    });
    const indNames = indNodes.flatMap((i) => {
      const children = (i.children || []) as Array<{ name?: string; id?: string }>;
      if (children.length > 0) {
        return [`${i.name || i.id}（${children.map((c) => c.name || c.id).join('、')}）`];
      }
      return [i.name || i.id];
    });
    const methodNames = methodNodes.length > 0
      ? methodNodes.map((m: any) => m.name || m.id)
      : ['（未选择，将按项目/指标生成方案）'];

    addUserMessage(
      `已配置试验参数：\n- 设备：${devNames.join('、')}\n- 设备名：${data.deviceDisplayName || devNames.join('、')}\n- 环境条件：${data.environmentConditionsText || '（未填写）'}\n- 项目：${projNames.join('、')}\n- 指标：${indNames.join('、')}\n- 方法：${methodNames.join('、')}`,
    );

    const newTaskId = `jyllm_${Date.now()}`;
    setTaskId(newTaskId);
    setHistoryViewTaskId(null);
    setHistoryViewFeedback({});
    setEnvConfigEdit('');
    setEnvLibrarianResults({});

    if (JYLLM_ENGINE_V2) {
      void runBuildSkeleton({
        taskIdForRequest: newTaskId,
        equipment: devNodes,
        projects: projNodes,
        indicators: indNodes,
        methods: methodNodes,
        environments: environmentsPayload,
        envNames: data.envNames,
        deviceDisplayName: data.deviceDisplayName,
        environmentConditionsText: data.environmentConditionsText,
      });
    } else {
      void runPlannerStream({
        taskIdForRequest: newTaskId,
        equipment: devNodes,
        projects: projNodes,
        indicators: indNodes,
        methods: methodNodes,
        environment_config: '',
        environments: environmentsPayload,
      });
    }

    setTimeout(() => {
      autoSaveHistory('generating');
    }, 500);
  };


  /* 环境配置步骤已下线：原「资料员批量分析」实现保留在版本库历史中；恢复时需重新启用 fetchEnvironments、callLibrarian 导入及环境页 UI。 */
  const handleCallLibrarianForAll = async () => {
    message.info('环境配置步骤已暂时关闭');
  };

  /* 原「保存环境设置并生成大纲」已由配置保存后的 runPlannerStream 替代。 */
  const handleEnvSaveAndContinue = async () => {
    message.info('环境配置步骤已暂时关闭，请通过「配置试验参数」保存以生成大纲');
  };

  const handleOutlineConfirm = (selection: OutlineSelection) => {
    setOutlineSelection(selection);
    setIsReviewingPrompts(false);
    setCurrentStep('generating');

    const think = planStreamThinkingRef.current.trim();
    const skel = plannerSkeletonTarget.trim();
    const confirmedPlanMarkdown = [
      think ? `### 规划分析（思考过程）\n\n${think}` : '',
      skel ? `### 方案骨架\n\n${skel}` : '',
    ]
      .filter(Boolean)
      .join('\n\n---\n\n');

    addAIMessage('已确认方案大纲，开始生成…', 'planner_prompts', {
      metadata: {
        plannerPrompts: [...plannerPrompts],
        ...(confirmedPlanMarkdown ? { confirmedPlanMarkdown } : {}),
      },
    });

    debouncedAutoSave('generating');
    startGeneration({ outlineSelection: selection });
  };

  /** 将各章合并为一条 Markdown，供反馈重写时使用 */
  const buildFullSchemeMarkdown = useCallback((mods: typeof modules) => buildJyllmFullSchemeMarkdown(mods), []);

  const handleChapterContentChange = useCallback((chapterIndex: number, md: string) => {
    setModules((prev) => prev.map((mod) => (mod.index === chapterIndex ? { ...mod, content: md } : mod)));
  }, []);

  const startGeneration = async (opts?: { userSupplement?: string; outlineSelection?: OutlineSelection }) => {
    const userSupplement = (opts?.userSupplement || '').trim();
    const genSelection = opts?.outlineSelection || outlineSelection;
    setIsStreaming(true);
    setModules([]);
    setDrawerOpen(true);
    setDrawerMode('generating');
    setGenerationProgress(0);
    setCurrentGeneratingChapter(null);
    setShowAllMessages(false);

    const msgId = addAIMessage('开始生成试验方案...', 'text', { thinking: [], isStreaming: true, metadata: { agent: 'planner' } });

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      if (useDeterministicEngineRef.current) {
        const v2response = await fetch('/api/fzsj/jyllm/generate-content', {
          method: 'POST',
          signal: controller.signal,
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            task_id: taskId,
            llm_model_key: llmModelKey || undefined,
            selected_sections: genSelection?.selectedSections,
            section_options: genSelection?.sectionOptions,
          }),
        });
        if (!v2response.ok) throw new Error(`HTTP ${v2response.status}`);
        const reader = v2response.body?.getReader();
        if (!reader) throw new Error('No reader');
        const decoder = new TextDecoder();
        let buffer = '';
        let methodTotal = 0;
        let methodDone = 0;
        let generationCompleteReceived = false;

        const v2Ch2 = {
          skel: '',
          methodOrder: [] as string[],
          completed: {} as Record<string, string>,
          streamMid: null as string | null,
          streamBuf: '',
        };

        const finalizeV2FromChapters = (chs: any[]) => {
          if (generationCompleteReceived) return;
          generationCompleteReceived = true;
          setModules(
            chs.map((c: any) => ({
              index: c.index,
              title: c.title,
              content: c.content || '',
              thinking: '',
              keywords: '',
              searchContext: '',
              score: typeof c.score === 'number' ? c.score : 100,
              status: 'complete' as const,
            })),
          );
          setIsStreaming(false);
          setDrawerMode('reviewing');
          setGenerationProgress(100);
          setCurrentGeneratingChapter(null);
          setCurrentStep('reviewing');
          setShowAllMessages(true);
          updateAIMessage(msgId, (msg) => ({ ...msg, isStreaming: false }));
          addAIMessage(
            '✅ 试验方案生成完成（确定性引擎）。请在右侧查看与编辑；无自动评审打分，确认后保存即可。',
            'text',
            { metadata: { agent: 'planner' } },
          );
          debouncedAutoSave('reviewing');
        };

        const dispatchV2 = (ev: { type: string; data: any }) => {
          const { type, data } = ev;

          if (type === 'chapter1_start') {
            setModules([
              {
                index: 1,
                title: '1. 试验依据',
                content: '',
                thinking: '',
                keywords: '',
                searchContext: '',
                score: 0,
                status: 'generating',
              },
              {
                index: 2,
                title: '2. 试验内容及方法',
                content: '',
                thinking: '',
                keywords: '',
                searchContext: '',
                score: 0,
                status: 'generating',
              },
            ]);
            setCurrentGeneratingChapter(1);
            setGenerationProgress(6);
            return;
          }
          if (type === 'chapter1_context') {
            const sc = String(data.search_context || '');
            setModules((prev) =>
              prev.map((m) => (m.index === 1 ? { ...m, searchContext: sc } : m)),
            );
            return;
          }
          if (type === 'thinking' && data.chapter_index === 1) {
            const raw = String(data.content || '').replace(/▌$/, '');
            if (!raw) return;
            setModules((prev) =>
              prev.map((m) => (m.index === 1 ? { ...m, thinking: (m.thinking || '') + raw } : m)),
            );
            return;
          }
          if (type === 'chapter1_delta') {
            const piece = String(data.text || '');
            if (!piece) return;
            setModules((prev) =>
              prev.map((m) =>
                m.index === 1 ? { ...m, content: (m.content || '') + piece, status: 'generating' as const } : m,
              ),
            );
            setGenerationProgress((p) => Math.max(p, 8));
            return;
          }
          if (type === 'chapter1_done') {
            const txt = (data.content || '').trim();
            setModules((prev) =>
              prev.map((m) =>
                m.index === 1 ? { ...m, content: txt || m.content, status: 'generating' as const } : m,
              ),
            );
            setCurrentGeneratingChapter(2);
            setGenerationProgress(18);
            return;
          }
          if (type === 'ch2_outline_start') {
            v2Ch2.methodOrder = Array.isArray(data.method_ids) ? [...data.method_ids] : [];
            methodTotal = v2Ch2.methodOrder.length;
            methodDone = 0;
            return;
          }
          if (type === 'ch2_outline_progress') {
            const cum = String(data.markdown_cumulative || '');
            setModules((prev) =>
              prev.map((m) =>
                m.index === 2
                  ? {
                      ...m,
                      content: composeChapter2Markdown(
                        cum,
                        v2Ch2.methodOrder,
                        v2Ch2.completed,
                        v2Ch2.streamMid,
                        v2Ch2.streamBuf,
                      ),
                      status: 'generating' as const,
                    }
                  : m,
              ),
            );
            setGenerationProgress((p) => Math.max(p, 20));
            return;
          }
          if (type === 'skeleton') {
            v2Ch2.skel = data.skeleton_markdown || '';
            v2Ch2.methodOrder = Array.isArray(data.method_ids) ? [...data.method_ids] : [];
            v2Ch2.completed = {};
            v2Ch2.streamMid = null;
            v2Ch2.streamBuf = '';
            methodTotal = v2Ch2.methodOrder.length;
            methodDone = 0;
            setModules((prev) =>
              prev.map((m) =>
                m.index === 2
                  ? {
                      ...m,
                      content: composeChapter2Markdown(
                        v2Ch2.skel,
                        v2Ch2.methodOrder,
                        v2Ch2.completed,
                        v2Ch2.streamMid,
                        v2Ch2.streamBuf,
                      ),
                    }
                  : m,
              ),
            );
            setGenerationProgress(22);
            return;
          }
          if (type === 'method_start') {
            v2Ch2.streamMid = (data.method_id || '').trim() || null;
            v2Ch2.streamBuf = '';
            return;
          }
          if (type === 'chapter2_delta') {
            const piece = String(data.text || '');
            v2Ch2.streamBuf += piece;
            setModules((prev) =>
              prev.map((m) =>
                m.index === 2
                  ? {
                      ...m,
                      content: composeChapter2Markdown(
                        v2Ch2.skel,
                        v2Ch2.methodOrder,
                        v2Ch2.completed,
                        v2Ch2.streamMid,
                        v2Ch2.streamBuf,
                      ),
                      status: 'generating' as const,
                    }
                  : m,
              ),
            );
            return;
          }
          if (type === 'method_content') {
            const mid = String(data.method_id || '').trim();
            if (mid) v2Ch2.completed[mid] = String(data.content || '');
            v2Ch2.streamMid = null;
            v2Ch2.streamBuf = '';
            methodDone += 1;
            if (methodTotal) {
              setGenerationProgress(Math.min(94, 22 + Math.round((methodDone / methodTotal) * 72)));
            }
            const full = data.full_chapter2 as string | undefined;
            if (full) v2Ch2.skel = full;
            setModules((prev) =>
              prev.map((m) =>
                m.index === 2
                  ? {
                      ...m,
                      content: full
                        ? full
                        : composeChapter2Markdown(
                            v2Ch2.skel,
                            v2Ch2.methodOrder,
                            v2Ch2.completed,
                            v2Ch2.streamMid,
                            v2Ch2.streamBuf,
                          ),
                    }
                  : m,
              ),
            );
            return;
          }
          if (type === 'method_error') {
            message.warning(`方法 ${data.method_id || ''} 生成失败：${data.error || '未知错误'}`);
            methodDone += 1;
            if (methodTotal) {
              setGenerationProgress(Math.min(94, 22 + Math.round((methodDone / methodTotal) * 72)));
            }
            return;
          }
          if (type === 'complete') {
            const chs = data.chapters || [];
            finalizeV2FromChapters(chs);
            return;
          }
          if (type === 'error') {
            throw new Error(data.message || 'generate_content 失败');
          }
        };

        while (true) {
          const { done, value } = await reader.read();
          if (done) {
            buffer += decoder.decode();
            break;
          }
          buffer += decoder.decode(value, { stream: true });
          const { events: parsedEvents, remaining } = parseSSEStream(buffer);
          buffer = remaining;
          for (const ev of parsedEvents) dispatchV2(ev);
        }
        if (buffer.trim()) {
          const { events: tailEvents } = parseSSEStream(buffer + '\n\n');
          for (const ev of tailEvents) dispatchV2(ev);
        }
        // 未收到 complete 时用快照收口；延后一帧以便 modulesRef 跟上最后一次 method_content
        const tryFinalizeV2Fallback = () => {
          if (generationCompleteReceived) return;
          const snap = modulesRef.current;
          const ch1 = snap.find((m) => m.index === 1);
          const ch2 = snap.find((m) => m.index === 2);
          const allMethodsDone = methodTotal > 0 && methodDone >= methodTotal;
          const hasBody =
            Boolean((ch1?.content || '').trim().length > 20) &&
            Boolean((ch2?.content || '').trim().length > 20);
          if (allMethodsDone && ch1 && ch2 && methodTotal > 0) {
            finalizeV2FromChapters([
              {
                index: 1,
                title: ch1.title,
                content: ch1.content || '',
                score: typeof ch1.score === 'number' ? ch1.score : 100,
              },
              {
                index: 2,
                title: ch2.title,
                content: ch2.content || '',
                score: typeof ch2.score === 'number' ? ch2.score : 100,
              },
            ]);
          }
        };
        window.setTimeout(tryFinalizeV2Fallback, 400);
        return;
      }

      const response = await fetch('/api/fzsj/jyllm/continue', {
        method: 'POST',
        signal: controller.signal,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          task_id: taskId,
          equipment: selectedEquipmentNodes,
          environments: [],
          projects: selectedProjects,
          indicators: selectedIndicators,
          methods: selectedMethods,
          environment_config: '',
          uploaded_files: selectedUploadedFiles,
          chapters: plannerPrompts,
          template_override: appliedTemplateContent || undefined,
          llm_model_key: llmModelKey || undefined,
          /** 后端 multi_agent_engine：写入 writer 提示，供「重新生成」参考用户修订稿 */
          ...(userSupplement ? { user_supplement: userSupplement } : {}),
        }),
      });

      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const reader = response.body?.getReader();
      if (!reader) throw new Error('No reader');
      const decoder = new TextDecoder();
      let buffer = '';
      const chapterStreamBufs = new Map<
        number,
        { thinking: JyllmTagStreamBuffer; content: JyllmTagStreamBuffer }
      >();
      const getChapterBufs = (idx: number) => {
        let e = chapterStreamBufs.get(idx);
        if (!e) {
          e = {
            thinking: new JyllmTagStreamBuffer('thinking'),
            content: new JyllmTagStreamBuffer('content'),
          };
          chapterStreamBufs.set(idx, e);
        }
        return e;
      };

      const dispatchGenFromBuffer = (event: { type: string; data: any }) => {
        const { type, data } = event;

        switch (type) {
            case 'thinking': {
              const chIdx = data.chapter_index;
              if (
                chIdx !== undefined &&
                (data.agent === 'writer' ||
                  data.agent === 'context_compress' ||
                  data.agent === 'planner')
              ) {
                const raw = String(data.content || '').replace(/▌$/, '');
                const bufs = getChapterBufs(chIdx);
                const delta = bufs.thinking.append(raw);
                if (!delta) break;
                setModules((prev) =>
                  prev.map((mod) =>
                    mod.index === chIdx ? { ...mod, thinking: (mod.thinking || '') + delta } : mod,
                  ),
                );
              }
              break;
            }
            case 'writer_stream': {
              setCurrentGeneratingChapter(data.chapter_index);
              if (data.status === 'started') {
                setModules(prev => {
                  const existing = prev.find(mod => mod.index === data.chapter_index);
                  if (existing) return prev;
                  return [...prev, {
                    index: data.chapter_index,
                    title: data.chapter_title || `章节 ${data.chapter_index}`,
                    content: '',
                    thinking: '',
                    keywords: '',
                    searchContext: '',
                    score: 0,
                    status: 'generating' as const,
                  }];
                });
                break;
              }
              if (!data.raw) {
                const raw = String(data.content || '').replace(/▌$/, '');
                const bufs = getChapterBufs(data.chapter_index);
                const delta = bufs.content.append(raw);
                if (!delta) break;
                setModules((prev) => {
                  const existing = prev.find((mod) => mod.index === data.chapter_index);
                  if (existing) {
                    return prev.map((mod) =>
                      mod.index === data.chapter_index
                        ? {
                            ...mod,
                            content: mod.content + delta,
                            status: 'generating' as const,
                          }
                        : mod,
                    );
                  }
                  return [
                    ...prev,
                    {
                      index: data.chapter_index,
                      title: data.chapter_title || `章节 ${data.chapter_index}`,
                      content: delta,
                      thinking: '',
                      keywords: '',
                      searchContext: '',
                      score: 0,
                      status: 'generating' as const,
                    },
                  ];
                });
              }
              break;
            }
            case 'writer_parsed': {
              const idx = data.chapter_index;
              const bufs = chapterStreamBufs.get(idx);
              if (bufs) {
                bufs.thinking.flushFinal();
                bufs.content.flushFinal();
                bufs.thinking.reset();
                bufs.content.reset();
              }
              setModules((prev) =>
                prev.map((mod) =>
                  mod.index === data.chapter_index
                    ? {
                        ...mod,
                        content: data.content || mod.content,
                        thinking: data.thinking || '',
                        keywords: data.keywords || '',
                        searchContext: data.search_context || '',
                        status: 'generating' as const,
                      }
                    : mod,
                ),
              );
              break;
            }
            case 'chapter_complete': {
              const idx = data.chapter_index;
              const bufs = chapterStreamBufs.get(idx);
              if (bufs) {
                bufs.thinking.flushFinal();
                bufs.content.flushFinal();
                bufs.thinking.reset();
                bufs.content.reset();
              }
              setModules((prev) => {
                const exists = prev.find(mod => mod.index === data.chapter_index);
                if (exists) {
                  return prev.map(mod =>
                    mod.index === data.chapter_index
                      ? { 
                          ...mod, 
                          content: data.content || '', 
                          score: data.score || 0, 
                          status: 'complete' as const,
                          thinking: data.thinking || mod.thinking || '',
                          keywords: data.keywords || mod.keywords || '',
                          searchContext: data.search_context || mod.searchContext || '',
                        }
                      : mod
                  );
                }
                return [...prev, {
                  index: data.chapter_index,
                  title: data.chapter_title || `章节 ${data.chapter_index}`,
                  content: data.content || '',
                  thinking: data.thinking || '',
                  keywords: data.keywords || '',
                  searchContext: data.search_context || '',
                  score: data.score || 0,
                  status: 'complete' as const,
                }];
              });
              if (plannerPrompts.length > 0) {
                setGenerationProgress(Math.round(((data.chapter_index + 1) / plannerPrompts.length) * 100));
              }
              break;
            }
            case 'complete': {
              setIsStreaming(false);
              setDrawerMode('reviewing');
              setGenerationProgress(100);
              setCurrentGeneratingChapter(null);
              setShowAllMessages(true);
              updateAIMessage(msgId, msg => ({ ...msg, isStreaming: false }));
              addAIMessage(
                '✅ 试验方案生成完成！请在右侧面板查看完整内容，可保存评价或点击「反馈重写」按意见修改。确认无误后点击「确认方案」保存。',
                'text',
                { metadata: { agent: 'planner' } }
              );
              debouncedAutoSave('reviewing');
              break;
            }
        }
      };

      while (true) {
        const { done, value } = await reader.read();
        if (done) {
          buffer += decoder.decode();
          break;
        }
        buffer += decoder.decode(value, { stream: true });
        const { events: parsedEvents, remaining } = parseSSEStream(buffer);
        buffer = remaining;

        for (const event of parsedEvents) {
          dispatchGenFromBuffer(event);
        }
      }

      if (buffer.trim()) {
        const { events: tailEvents } = parseSSEStream(buffer + '\n\n');
        for (const event of tailEvents) {
          dispatchGenFromBuffer(event);
        }
      }

      chapterStreamBufs.forEach((bufs, chIdx) => {
        const t = bufs.thinking.flushFinal();
        const c = bufs.content.flushFinal();
        if (!t && !c) return;
        setModules((prev) =>
          prev.map((mod) =>
            mod.index !== chIdx
              ? mod
              : {
                  ...mod,
                  thinking: (mod.thinking || '') + t,
                  content: (mod.content || '') + c,
                },
          ),
        );
      });
    } catch (err: any) {
      if (err.name !== 'AbortError') {
        addAIMessage(`生成失败：${err.message}`, 'text');
      }
      setIsStreaming(false);
    }
  };

  /** 放在 startGeneration 之后，避免闭包拿到过期的生成函数；正文快照来自 modulesRef */
  const handleRegenerate = () => {
    if (!taskId) {
      message.warning('缺少任务 ID');
      return;
    }
    if (!plannerPrompts.length) {
      message.warning('缺少大纲章节，请重新规划后再生成');
      return;
    }
    const supplement = buildFullSchemeMarkdown(modulesRef.current);
    void startGeneration({ userSupplement: supplement });
  };

  const handleStop = () => {
    abortRef.current?.abort();
    setIsStreaming(false);
    addAIMessage('生成已停止。', 'text');
    debouncedAutoSave('stopped');
  };

  const handleFeedbackSave = async (score: number, comment: string) => {
    try {
      await submitFeedback({
        task_id: taskId,
        chapter_index: -1,
        score,
        comment,
        action: 'save',
      });
      setOverallScore(score);
      setOverallComment(comment);
      setHistoryViewFeedback({ score, comment: comment || null });
      debouncedAutoSave('generating');
    } catch {
      message.error('保存失败');
    }
  };

  const handleFeedbackRewrite = async (comment: string) => {
    if (!taskId) {
      message.warning('缺少任务 ID');
      return;
    }
    setFeedbackRewriteLoading(true);
    setIsStreaming(true);
    setDrawerOpen(true);
    const controller = new AbortController();
    abortRef.current = controller;

    const applyRewrittenModules = (text: string) => {
      const { ch1, ch2 } = splitPureLlmSchemeMarkdown(text);
      setModules((prev) => {
        const next = [...prev];
        const upsert = (idx: number, title: string, content: string) => {
          const i = next.findIndex((m) => m.index === idx);
          const row: ChapterModule = {
            index: idx,
            title,
            content,
            score: 100,
            status: 'rewriting',
            thinking: '',
            keywords: '',
            searchContext: '',
          };
          if (i >= 0) next[i] = { ...next[i], ...row };
          else next.push(row);
        };
        if (ch1 || !ch2) upsert(1, '1. 试验依据', ch1);
        if (ch2) upsert(2, '2. 试验内容及方法', ch2 || text);
        return next.sort((a, b) => a.index - b.index);
      });
    };

    try {
      const fullScheme = buildJyllmFullSchemeMarkdown(modulesRef.current);
      const response = await fetch('/api/fzsj/jyllm/scheme-feedback-rewrite', {
        method: 'POST',
        signal: controller.signal,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          task_id: taskId,
          comment,
          full_scheme: fullScheme,
          llm_model_key: llmModelKey || undefined,
          base_config: {
            device_display_name: deviceDisplayName,
            environment_conditions: environmentConditionsText,
            equipment: selectedEquipmentNodes,
            projects: selectedProjects,
            indicators: selectedIndicators,
            methods: selectedMethods,
          },
        }),
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const reader = response.body?.getReader();
      if (!reader) throw new Error('No reader');
      const decoder = new TextDecoder();
      let buffer = '';
      let rewritten = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const { events, remaining } = parseSSEStream(buffer);
        buffer = remaining;
        for (const ev of events) {
          if (ev.type === 'rewrite_delta') {
            rewritten += String(ev.data.text || '');
            if (rewritten.trim()) applyRewrittenModules(rewritten);
          }
          if (ev.type === 'rewrite_complete') {
            rewritten = String(ev.data.full_scheme || ev.data.content || rewritten);
          }
          if (ev.type === 'error') {
            throw new Error(ev.data.message || '重写失败');
          }
        }
      }

      if (rewritten.trim()) {
        const { ch1, ch2 } = splitPureLlmSchemeMarkdown(rewritten);
        setModules([
          { index: 1, title: '1. 试验依据', content: ch1, score: 100, status: 'complete', thinking: '', keywords: '', searchContext: '' },
          { index: 2, title: '2. 试验内容及方法', content: ch2 || rewritten, score: 100, status: 'complete', thinking: '', keywords: '', searchContext: '' },
        ]);
        setDrawerMode('reviewing');
        message.success('已根据反馈重写方案');
        debouncedAutoSave('reviewing');
      } else {
        message.warning('重写未返回内容');
      }
    } catch (err: any) {
      if (err.name !== 'AbortError') {
        message.error(err.message || '反馈重写失败');
      }
    } finally {
      setFeedbackRewriteLoading(false);
      setIsStreaming(false);
    }
  };

  const openRegionRewrite = useCallback((spec: RegionRewriteSpec, preview: string) => {
    setRegionRewriteSpec(spec);
    setRegionRewritePreview(preview);
    setRegionRewriteOriginal(spec.original_region || preview);
    setRegionRewriteComment('');
    setRegionRewriteStreaming('');
    setRegionRewriteMerged(null);
    setRegionRewriteDiffConfirmed(false);
    setRegionRewriteWarnings([]);
    setRegionRewriteDiffStats(null);
    setRegionRewriteOpen(true);
  }, []);

  const handleRequestRegionRewrite = useCallback(
    (spec: RegionRewriteSpec, preview: string) => {
      openRegionRewrite(spec, preview);
    },
    [openRegionRewrite],
  );

  const runRegionRewrite = async () => {
    if (!taskId || !regionRewriteSpec) return;
    const comment = regionRewriteComment.trim();
    if (!comment) {
      message.warning('请填写修改意见');
      return;
    }
    setRegionRewriteLoading(true);
    setRegionRewriteStreaming('');
    setRegionRewriteMerged(null);
    setRegionRewriteDiffConfirmed(false);
    setRegionRewriteWarnings([]);
    setRegionRewriteDiffStats(null);
    const controller = new AbortController();
    abortRef.current = controller;

    const chIdx = regionRewriteSpec.chapter_index;
    setModules((prev) =>
      prev.map((m) => (m.index === chIdx ? { ...m, status: 'rewriting' as const } : m)),
    );

    try {
      const latestChapter =
        modulesRef.current.find((m) => m.index === chIdx)?.content ?? regionRewriteSpec.chapter_content;
      const specWithLatest = { ...regionRewriteSpec, chapter_content: latestChapter };
      const body = buildRegionRewriteBody(
        specWithLatest,
        comment,
        taskId,
        {
          device_display_name: deviceDisplayName,
          environment_conditions: environmentConditionsText,
          equipment: selectedEquipmentNodes,
          projects: selectedProjects,
          indicators: selectedIndicators,
          methods: selectedMethods,
          planner_prompts: plannerPrompts,
        },
        llmModelKey || undefined,
        {
          fullSchemeExcerpt: buildFullSchemeExcerpt(modulesRef.current),
          originalRegion: regionRewriteOriginal || regionRewritePreview,
        },
      );
      const response = await fetch('/api/fzsj/jyllm/region-rewrite', {
        method: 'POST',
        signal: controller.signal,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const reader = response.body?.getReader();
      if (!reader) throw new Error('No reader');
      const decoder = new TextDecoder();
      let buffer = '';
      let streamAcc = '';
      let merged = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const { events, remaining } = parseSSEStream(buffer);
        buffer = remaining;
        for (const ev of events) {
          if (ev.type === 'region_rewrite_start') {
            const orig = String(ev.data.original_region || '');
            if (orig) setRegionRewriteOriginal(orig);
          }
          if (ev.type === 'region_revision_rejected') {
            const errs = (ev.data.errors as string[]) || [];
            if (ev.data.kept_first_pass) {
              message.info('重试未通过，已保留首次修订结果，请核对 diff 后应用');
            } else if (ev.data.retrying) {
              message.info('正在以更严格模式重试…');
            } else if (errs.length) {
              message.warning(errs.join('；'));
            }
          }
          if (ev.type === 'region_rewrite_delta') {
            if (ev.data.retry) {
              // 重试流不覆盖首次已展示的正确结果，最终以 complete.new_region 为准
            } else if (ev.data.table_patch) {
              streamAcc = String(ev.data.text || '');
              setRegionRewriteStreaming(streamAcc);
            } else {
              streamAcc += String(ev.data.text || '');
              if (streamAcc.includes('【生成出错】') || /maximum context length/i.test(streamAcc)) {
                throw new Error(streamAcc.replace(/^[\s\S]*?【生成出错】/, '').trim() || streamAcc.trim());
              }
              setRegionRewriteStreaming(streamAcc);
            }
          }
          if (ev.type === 'region_rewrite_complete') {
            merged = String(ev.data.merged_chapter || '');
            streamAcc = String(ev.data.new_region || streamAcc);
            const orig = String(ev.data.original_region || regionRewriteOriginal || '');
            if (orig) {
              setRegionRewriteOriginal(orig);
              setRegionRewritePreview(orig.slice(0, 4000));
            }
            setRegionRewriteStreaming(streamAcc);
            setRegionRewriteMerged(merged);
            setRegionRewriteDiffConfirmed(false);
            const val = ev.data.validation as { warnings?: string[] } | undefined;
            setRegionRewriteWarnings(val?.warnings || []);
            setRegionRewriteDiffStats((ev.data.diff_stats as typeof regionRewriteDiffStats) || null);
          }
          if (ev.type === 'error') {
            const msg = ev.data.message || '区域修订失败';
            const warns = (ev.data.warnings as string[]) || [];
            if (warns.length) setRegionRewriteWarnings(warns);
            throw new Error(typeof msg === 'string' ? msg : '区域修订失败');
          }
        }
      }

      if (merged) {
        message.success('修订完成，请核对 diff 后确认应用');
      } else {
        message.warning('修订未返回可应用内容');
      }
    } catch (err: unknown) {
      if (err instanceof Error && err.name !== 'AbortError') {
        message.error(err.message || '区域重写失败');
      }
    } finally {
      setRegionRewriteLoading(false);
      setModules((prev) =>
        prev.map((m) => (m.index === chIdx && m.status === 'rewriting' ? { ...m, status: 'complete' as const } : m)),
      );
    }
  };

  const applyRegionRewrite = () => {
    if (!regionRewriteSpec || !regionRewriteMerged || !regionRewriteDiffConfirmed) return;
    const chIdx = regionRewriteSpec.chapter_index;
    const before =
      modulesRef.current.find((m) => m.index === chIdx)?.content ?? regionRewriteSpec.chapter_content;
    setRevisionHistory((prev) => [
      ...prev,
      {
        timestamp: Date.now(),
        region_type: regionRewriteSpec.region_type,
        comment: regionRewriteComment,
        chapter_index: chIdx,
        before,
        after: regionRewriteMerged,
      },
    ]);
    setModules((prev) =>
      prev.map((m) =>
        m.index === chIdx ? { ...m, content: regionRewriteMerged, status: 'complete' as const } : m,
      ),
    );
    setRegionRewriteOpen(false);
    setRegionRewriteSpec(null);
    message.success('已应用区域修改');
    debouncedAutoSave('reviewing');
  };

  const undoLastRevision = () => {
    setRevisionHistory((prev) => {
      if (!prev.length) return prev;
      const last = prev[prev.length - 1]!;
      setModules((mods) =>
        mods.map((m) =>
          m.index === last.chapter_index ? { ...m, content: last.before, status: 'complete' as const } : m,
        ),
      );
      message.success('已撤销上次修订');
      debouncedAutoSave('reviewing');
      return prev.slice(0, -1);
    });
  };

  const stopRegionRewrite = () => {
    abortRef.current?.abort();
    setRegionRewriteLoading(false);
  };

  const defaultSchemeSaveName = useCallback(() => {
    return buildDefaultSchemeSaveName(selectedEquipmentNodes, deviceDisplayName);
  }, [selectedEquipmentNodes, deviceDisplayName]);

  const handleConfirm = async () => {
    try {
      const schemes = await fetchSchemes('admin');
      const base = buildDefaultSchemeSaveName(selectedEquipmentNodes, deviceDisplayName);
      const name = resolveUniqueSchemeName(base, schemes as { task_id?: string; scheme_name?: string }[], taskId || undefined);
      setSchemeNameInput(name);
    } catch {
      setSchemeNameInput(defaultSchemeSaveName());
    }
    setShowSchemeNameModal(true);
  };

  const handleSaveScheme = async () => {
    if (!schemeNameInput.trim()) {
      message.warning('请输入方案名称');
      return;
    }
    setShowSchemeNameModal(false);
    const fullScheme = buildJyllmFullSchemeMarkdown(modules);
    try {
      const result = await saveScheme({
        task_id: taskId,
        full_scheme: fullScheme,
        chapters: modules,
        metadata: {
          user_id: 'admin',
          scheme_name: schemeNameInput.trim(),
          devices: deviceSelection.selectedDevices,
          environments: [],
          projects: selectedProjects.map((p: any) => p.name || p.id || p),
          indicators: selectedIndicators.map((i: any) => i.name || i.id || i),
          methods: selectedMethods.map((m: any) => m.name || m.id || m),
          selected_files: selectedUploadedFiles,
          environment_config: environmentConditionsText,
          device_display_name: deviceDisplayName,
          environment_conditions_text: environmentConditionsText,
          outline_selection: outlineSelection,
          save_to_minio: true,
          save_to_chroma: true,
          save_to_neo4j: true,
          neo4j_node_type: 'Scheme',
          current_step: 'complete',
          messages: messages,
          planner_prompts: plannerPrompts,
          selected_equipment_keys: selectedEquipmentKeys,
          page_type: 'jyllm',
          overall_score: overallScore,
          overall_comment: overallComment,
        }             
      });
      if (!result) {
        message.error('保存失败，请检查后端连接');
        return;
      }
      const dbResults = (result as any).db_results || {};
      const dbSummary = Object.entries(dbResults)
        .filter(([, v]) => {
          if (v == null || v === '') return false;
          if (typeof v === 'string') return !v.startsWith('error');
          if (typeof v === 'object' && v !== null && 'error' in (v as object)) return false;
          return true;
        })
        .map(([k]) => k)
        .join('、');
      setDrawerMode('complete');
      setCurrentStep('complete');
      setAppliedTemplateName(schemeNameInput.trim());
      setHistoryViewFeedback({ score: overallScore, comment: overallComment || null });
      setHistoryRefreshKey((k) => k + 1);
      message.success(dbSummary ? `方案已保存 (${dbSummary})` : '方案已保存');
    } catch (err: any) {
      message.error(`保存失败: ${err.message}`);
    }
  };

  const handleResave = async () => {
    const fullScheme = buildJyllmFullSchemeMarkdown(modules);
    try {
      const result = await updateScheme({
        task_id: taskId,
        full_scheme: fullScheme,
        chapters: modules,
        metadata: {
          user_id: 'admin',
          scheme_name: appliedTemplateName.trim() || defaultSchemeSaveName(),
          devices: deviceSelection.selectedDevices,
          environments: [],
          projects: selectedProjects.map((p: any) => p.name || p.id || p),
          indicators: selectedIndicators.map((i: any) => i.name || i.id || i),
          methods: selectedMethods.map((m: any) => m.name || m.id || m),
          selected_files: selectedUploadedFiles,
          environment_config: '',
          save_to_minio: true,
          save_to_chroma: true,
          save_to_neo4j: false,
          current_step: currentStep,
          messages: messages,
          planner_prompts: plannerPrompts,
          selected_equipment_keys: selectedEquipmentKeys,
          page_type: 'jyllm',
          overall_score: overallScore,
          overall_comment: overallComment,
        },
      });
      if (!result) {
        message.error('重新保存失败');
        return;
      }
      const dbResults = (result as any).db_results || {};
      const dbSummary = Object.entries(dbResults)
        .filter(([, v]) => {
          if (v == null || v === '') return false;
          if (typeof v === 'string') return !v.startsWith('error');
          if (typeof v === 'object' && v !== null && 'error' in (v as object)) return false;
          return true;
        })
        .map(([k]) => k)
        .join('、');
      message.success(dbSummary ? `编辑后方案已重新保存 (${dbSummary})` : '编辑后方案已重新保存');
      setHistoryRefreshKey((k) => k + 1);
    } catch (err: any) {
      message.error(`重新保存失败: ${err.message}`);
    }
  };

  const buildJyllmMetaForFile = (format: SchemeExportFormat): UploadFileMeta => {
    const ext = format === 'word' ? 'docx' : format === 'pdf' ? 'pdf' : 'md';
    return buildJyllmKmMeta({
      schemeName: schemeNameInput.trim() || appliedTemplateName || undefined,
      taskId,
      deviceDisplayName,
      environmentConditionsText,
      projects: selectedProjects,
      indicators: selectedIndicators,
      fileType: ext,
    });
  };

  const openKmSyncModal = (blob: Blob, filename: string, format: SchemeExportFormat) => {
    setKmSyncFile(fileFromExport(blob, filename));
    setKmSyncMeta(buildJyllmMetaForFile(format));
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
    if (!taskId) {
      message.warning('缺少任务 ID');
      return;
    }
    try {
      setExportingKind(format);
      const { blob, filename } = await exportJyllmBlob(taskId, format);
      downloadBlob(blob, filename);
      message.success(`已导出为 ${format.toUpperCase()}`);
      promptSyncAfterExport(blob, filename, format);
    } catch (err: unknown) {
      message.error(err instanceof Error ? err.message : '导出失败');
    } finally {
      setExportingKind(null);
    }
  };

  const handleSyncToKm = async () => {
    if (!taskId) {
      message.warning('缺少任务 ID');
      return;
    }
    try {
      setSyncingToKm(true);
      const { blob, filename } = await exportJyllmBlob(taskId, 'word');
      downloadBlob(blob, filename);
      openKmSyncModal(blob, filename, 'word');
    } catch (err: unknown) {
      message.error(err instanceof Error ? err.message : 'Word 生成失败');
    } finally {
      setSyncingToKm(false);
    }
  };

  const handleApplyTemplate = (tpl: { name: string; content: string; sections: any[] }) => {
    setAppliedTemplateName(tpl.name);
    setAppliedTemplateContent(tpl.content);
  };

  const handleHistoryClick = async (historyTaskId: string) => {
    try {
      const data = await fetchScheme(historyTaskId);
      if (data) {
        setTaskId(historyTaskId);
        setHistoryViewTaskId(historyTaskId);

        const scoreRaw = (data as any).overall_score;
        const commentRaw = (data as any).overall_comment;
        const restoredScore = scoreRaw != null && scoreRaw !== '' ? Number(scoreRaw) : null;
        const restoredComment = commentRaw != null ? String(commentRaw) : '';
        setOverallScore(restoredScore != null && !Number.isNaN(restoredScore) ? restoredScore : 90);
        setOverallComment(restoredComment);
        setHistoryViewFeedback({
          score: restoredScore,
          comment: restoredComment || null,
        });
        
        const metadata = (data as any).metadata || {};
        let extraMeta: Record<string, any> = {};
        try {
          const desc = (data as any).scheme_description;
          if (desc && typeof desc === 'string' && desc.startsWith('{')) {
            extraMeta = JSON.parse(desc);
          }
        } catch {
          extraMeta = {};
        }
        if (extraMeta.device_display_name) setDeviceDisplayName(extraMeta.device_display_name);
        if (extraMeta.environment_conditions_text) setEnvironmentConditionsText(extraMeta.environment_conditions_text);
        if (extraMeta.outline_tree?.length) setOutlineTree(extraMeta.outline_tree);
        if (extraMeta.outline_selection) setOutlineSelection(extraMeta.outline_selection);

        if (metadata.devices && metadata.devices.length > 0) {
          setSelectedEquipmentNodes(metadata.devices);
          setSelectedEquipmentKeys(metadata.devices.map((d: any) => d.id || d.key || d));
        }
        
        if (metadata.projects) {
          setSelectedProjects(metadata.projects);
        }
        if (metadata.indicators) {
          setSelectedIndicators(metadata.indicators);
        }
        if (metadata.methods) {
          setSelectedMethods(metadata.methods);
        }
        
        const schemeModules = (data as any).chapters || [];
        if (schemeModules.length > 0) {
          setModules(schemeModules.map((ch: any, i: number) => ({
            index: ch.chapter_index ?? ch.index ?? i + 1,
            title: ch.chapter_title || ch.title || `章节 ${i + 1}`,
            content: ch.content || '',
            score: ch.score || ch.auto_score || 0,
            status: (ch.status || ch.review_status || 'approved') as 'pending' | 'generating' | 'complete' | 'rewriting' | 'approved',
            thinking: ch.thinking || '',
            keywords: ch.keywords || '',
            searchContext: ch.search_context || ch.searchContext || '',
          })));
        }
        
        const savedMessages = (data as any).messages;
        if (savedMessages && Array.isArray(savedMessages) && savedMessages.length > 0) {
          setMessages(savedMessages);
        }
        
        const savedPrompts = (data as any).planner_prompts;
        if (savedPrompts && Array.isArray(savedPrompts) && savedPrompts.length > 0) {
          setPlannerPrompts(savedPrompts);
          setPlannerMarkdown(buildPlannerMarkdownFromPrompts(savedPrompts));
        }
        
        const savedStep = (data as any).current_step;
        if (savedStep) {
          let step: FlowStep = savedStep;
          if (step === 'environment') {
            const hasPlanner =
              savedPrompts && Array.isArray(savedPrompts) && savedPrompts.length > 0;
            step = hasPlanner ? 'planner_review' : 'device';
          }
          setCurrentStep(step);
          if (step === 'generating' || step === 'reviewing' || step === 'complete') {
            setDrawerOpen(true);
            setDrawerMode(step === 'complete' ? 'complete' : step === 'reviewing' ? 'reviewing' : 'generating');
          }
        } else if (schemeModules.length > 0) {
          setDrawerOpen(true);
          setDrawerMode('complete');
          setCurrentStep('complete');
        }
        
        if ((data as any).generation_status === 'completed') {
          setDrawerMode('complete');
          setCurrentStep('complete');
        }
      }
    } catch (err) {
      console.error('加载历史记录失败:', err);
    }
  };

  const runPureLlmPipeline = async (description: string) => {
    const newTaskId = `pure_${Date.now()}`;
    setTaskId(newTaskId);
    setHistoryViewTaskId(null);
    setHistoryViewFeedback({});
    setIsStreaming(true);
    setDrawerOpen(true);
    setDrawerMode('generating');
    setModules([]);
    setPureLlmFigures({});
    setCurrentStep('generating');
    setCurrentGeneratingChapter(1);
    setGenerationProgress(3);
    setShowAllMessages(false);

    const msgId = addAIMessage('纯 LLM 模式：检索 → PDDL 规划 → 撰写方案（流式输出）…', 'text', {
      thinking: [],
      isStreaming: true,
      metadata: { agent: 'pure_llm' },
    });

    const controller = new AbortController();
    abortRef.current = controller;

    let progress = 5;
    let schemeBuf = '';
    let pddlBuf = '';
    let retrieveThink = '';
    const planSteps: string[] = [];

    const appendThinking = (agent: string, piece: string) => {
      if (!piece) return;
      updateAIMessage(msgId, (m) => {
        const entries = m.thinking ? [...m.thinking] : [];
        const last = entries[entries.length - 1];
        if (last && last.agent === agent) {
          entries[entries.length - 1] = { ...last, content: last.content + piece };
        } else {
          entries.push({
            agent,
            content: piece,
            timestamp: new Date().toISOString(),
            isCollapsed: false,
          });
        }
        return { ...m, thinking: entries };
      });
    };

    const dispatchPureLlmEvent = (ev: { type: string; data: any }) => {
      const { type, data } = ev;
      switch (type) {
        case 'error':
          throw new Error(data?.message || '纯 LLM 生成失败');
        case 'agent_retrieve':
          updateAIMessage(msgId, (m) => ({ ...m, content: data?.message || '正在检索图谱与标准库…' }));
          progress = Math.max(progress, 8);
          break;
        case 'retrieve_delta':
          retrieveThink += String(data?.text || '');
          appendThinking('retrieve', String(data?.text || ''));
          break;
        case 'tool_round': {
          const tool = data?.tool ?? '';
          const argsPreview = JSON.stringify(data?.args || {}).slice(0, 120);
          appendThinking('tool', `\n🔧 ${tool} ${argsPreview}\n`);
          updateAIMessage(msgId, (m) => ({
            ...m,
            content: `正在调用工具：${tool}…`,
          }));
          progress = Math.max(progress, 12);
          break;
        }
        case 'tool_result':
          appendThinking(
            'tool',
            ` → Neo4j ${data?.neo_count ?? 0} 条，Chroma ${data?.chroma_count ?? 0} 条\n`,
          );
          break;
        case 'retrieve_done':
          progress = Math.max(progress, 22);
          updateAIMessage(msgId, (m) => ({
            ...m,
            content: `检索完成：试验方法 ${data?.method_count ?? 0} 条，Chroma ${data?.chroma_count ?? 0} 条。正在生成 PDDL…`,
          }));
          break;
        case 'pddl_llm':
          pddlBuf = '';
          appendThinking('pddl', '\n--- PDDL 生成 ---\n');
          updateAIMessage(msgId, (m) => ({ ...m, content: data?.message || '正在流式生成 PDDL…' }));
          progress = Math.max(progress, 28);
          break;
        case 'pddl_delta':
          pddlBuf += String(data?.text || '');
          appendThinking('pddl', String(data?.text || ''));
          progress = Math.max(progress, 32);
          break;
        case 'pddl_repair':
          appendThinking('pddl', `\n[校验修正] ${(data?.errors || []).join('; ')}\n`);
          break;
        case 'pddl_llm_done':
          progress = Math.max(progress, 40);
          updateAIMessage(msgId, (m) => ({
            ...m,
            content: `PDDL 已生成。${data?.rationale ? `${String(data.rationale).slice(0, 60)}… ` : ''}规划器求解中…`,
          }));
          break;
        case 'plan_solve':
          updateAIMessage(msgId, (m) => ({ ...m, content: '经典规划器求解中…' }));
          progress = Math.max(progress, 45);
          break;
        case 'plan_step':
          planSteps.push(`[${data?.phase}] ${data?.action}`);
          appendThinking('plan', `${data?.index}. ${data?.action}\n`);
          progress = Math.max(progress, 48);
          break;
        case 'plan_solved':
          progress = Math.max(progress, 52);
          updateAIMessage(msgId, (m) => ({
            ...m,
            content: `规划完成（${data?.solver ?? ''}，${(data?.plan as string[])?.length ?? 0} 步）。注入图谱方法并重规划…`,
          }));
          break;
        case 'pddl_enrich':
          updateAIMessage(msgId, (m) => ({ ...m, content: '注入 TestMethod 并重规划…' }));
          progress = Math.max(progress, 58);
          break;
        case 'enrich_done':
          progress = Math.max(progress, 65);
          updateAIMessage(msgId, (m) => ({
            ...m,
            content: `重规划完成（${(data?.plan as string[])?.length ?? 0} 步）。生成可视化…`,
          }));
          break;
        case 'visualize':
          progress = Math.max(progress, 70);
          break;
        case 'visualize_done':
          if (data?.figures) setPureLlmFigures(data.figures as Record<string, string>);
          progress = Math.max(progress, 75);
          break;
        case 'write':
          updateAIMessage(msgId, (m) => ({ ...m, content: '正在流式撰写试验方案（右侧实时预览）…' }));
          progress = Math.max(progress, 78);
          break;
        case 'scheme_start':
          schemeBuf = '';
          setModules(
            pureLlmModulesFromSchemeBuf('', 'generating').length > 0
              ? pureLlmModulesFromSchemeBuf('', 'generating')
              : [
                  {
                    index: 1,
                    title: '1. 试验依据',
                    content: '',
                    thinking: '',
                    keywords: '',
                    searchContext: '',
                    score: 0,
                    status: 'generating',
                  },
                  {
                    index: 2,
                    title: '2. 试验内容及方法',
                    content: '',
                    thinking: '',
                    keywords: '',
                    searchContext: '',
                    score: 0,
                    status: 'generating',
                  },
                ],
          );
          setCurrentGeneratingChapter(1);
          progress = Math.max(progress, 80);
          break;
        case 'scheme_delta': {
          const piece = String(data?.text || '');
          if (!piece) break;
          schemeBuf += piece;
          const mods = pureLlmModulesFromSchemeBuf(schemeBuf, 'generating');
          if (mods.length) {
            setModules(mods);
            const hasCh2 = mods.some((x) => x.index === 2 && (x.content || '').length > 0);
            setCurrentGeneratingChapter(hasCh2 ? 2 : 1);
          }
          progress = Math.min(96, Math.max(progress, 82));
          break;
        }
        case 'scheme_done':
          if (data?.content) schemeBuf = String(data.content);
          setModules(pureLlmModulesFromSchemeBuf(schemeBuf, 'generating'));
          progress = Math.max(progress, 95);
          break;
        case 'complete': {
          progress = 100;
          const chapters = (data?.chapters || []) as { index: number; title: string; content: string }[];
          const schemeMd = (data?.scheme_markdown as string) || schemeBuf;
          const mods =
            chapters.length > 0
              ? chapters.map((c) => ({
                  index: c.index,
                  title: c.title,
                  content: c.content || '',
                  thinking: '',
                  keywords: '',
                  searchContext: '',
                  score: 100,
                  status: 'complete' as const,
                }))
              : pureLlmModulesFromSchemeBuf(schemeMd, 'complete');
          setModules(mods);
          const figs = (data?.figures || {}) as Record<string, string>;
          setPureLlmFigures(figs);
          const figBlock = Object.entries(figs)
            .map(([k, url]) => {
              const name = url.split('/').pop() || k;
              if (url.endsWith('.html') || url.endsWith('.pddl')) {
                return `- [${k}: ${name}](${url})`;
              }
              return `![${k}](${url})`;
            })
            .join('\n');
          const summary = (data?.summary as string) || '';
          updateAIMessage(msgId, (m) => ({
            ...m,
            isStreaming: false,
            content: `纯 LLM 方案已生成。\n\n${summary}\n\n${figBlock ? `### 过程可视化\n\n${figBlock}\n\n` : ''}请在右侧面板查看与编辑正文。`,
          }));
          setIsStreaming(false);
          setDrawerMode('reviewing');
          setCurrentStep('reviewing');
          setGenerationProgress(100);
          setShowAllMessages(true);
          debouncedAutoSave('generating');
          break;
        }
        default:
          break;
      }
      setGenerationProgress(progress);
    };

    try {
      const response = await fetch('/api/fzsj/jyllm/pure-llm/run', {
        method: 'POST',
        signal: controller.signal,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          task_id: newTaskId,
          description,
          llm_model_key: llmModelKey || undefined,
        }),
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const reader = response.body?.getReader();
      if (!reader) throw new Error('No reader');
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const { events, remaining } = parseSSEStream(buffer);
        buffer = remaining;
        for (const ev of events) {
          dispatchPureLlmEvent(ev);
        }
      }
      if (buffer.trim()) {
        const { events: tail } = parseSSEStream(`${buffer}\n\n`);
        for (const ev of tail) dispatchPureLlmEvent(ev);
      }
    } catch (err: unknown) {
      const emsg = err instanceof Error ? err.message : String(err);
      if (emsg !== 'AbortError') {
        message.error(emsg || '纯 LLM 生成失败');
        updateAIMessage(msgId, (m) => ({ ...m, isStreaming: false, content: `生成失败：${emsg}` }));
      }
      setIsStreaming(false);
      // 固定为配置模式
      // setCurrentStep(generationMode === 'pure_llm' ? 'device' : currentStep);
      setCurrentStep(currentStep);
    } finally {
      abortRef.current = null;
    }
  };

  const handleClear = () => {
    setMessages([]);
    setDeviceSelection({ searchKey: '', selectedDevices: [], modelInput: '' });
    setEnvSelection({ selectedEnvs: [], envConfig: '', librarianSuggestion: '' });
    setSelectedEquipmentKeys([]);
    setSelectedEquipmentNodes([]);
    setSelectedProjects([]);
    setSelectedIndicators([]);
    setSelectedMethods([]);
    setAllProjects([]);
    setAllIndicators([]);
    setAllMethods([]);
    setModules([]);
    setPlannerPrompts([]);
    setPlannerMarkdown('');
    setPlannerSkeletonTarget('');
    setPlanThinkStreamTarget('');
    setPlanOutlineStreamTarget('');
    setPlanThinkStreamAnim(false);
    setPlanOutlineStreamAnim(false);
    planStreamMsgIdRef.current = null;
    planStreamThinkingRef.current = '';
    setCurrentStep('device');
    setDrawerOpen(false);
    setConfigModalTableRows([]);
    setConfigModalEnvNames([]);
    setDeviceEnvMap({});
    setEnvLibrarianResults({});
    setEnvConfigEdit('');
    filteredMethodsRef.current = [];
    setPureLlmFigures({});

    // 固定为配置模式
    // const clearedIntro =
    //   generationMode === 'pure_llm'
    //     ? '对话已清空。请输入自然语言描述试验任务开始生成。'
    //     : '对话已清空。请点击"配置试验参数"按钮开始设计。';
    const clearedIntro = '对话已清空。请点击"配置试验参数"按钮开始设计。';
    setMessages([{
      id: generateId(),
      role: 'assistant',
      content: clearedIntro,
      contentType: 'text',
      timestamp: new Date().toISOString(),
      metadata: { agent: 'planner' },
    }]);
  };

  const renderStepContent = () => {
    // 固定为配置模式
    // if (generationMode === 'pure_llm') {
    //   return null;
    // }
    switch (currentStep) {
      case 'device':
        return (
          <div style={{ background: '#fff', padding: 24, borderRadius: 12, boxShadow: '0 2px 8px rgba(0,0,0,0.04)', textAlign: 'center' }}>
            <Button 
              type="primary" 
              size="large" 
              onClick={() => setConfigModalOpen(true)}
              style={{ marginBottom: 16 }}
            >
              配置试验参数
            </Button>
            <div style={{ fontSize: 13, color: '#666' }}>
              点击按钮打开配置弹窗，选择设备、项目、指标和方法
            </div>
          </div>
        );
      case 'environment':
        /* 环境配置步骤已下线：原「资料员 + 环境设置文本 + 保存并生成大纲」整段 UI 保留在 Git 历史，此处不再渲染 */
        return null;
      case 'planner_thinking':
        return null;
      case 'planner_review':
        return (
          <div style={{ background: '#fff', padding: 24, borderRadius: 12, boxShadow: '0 2px 8px rgba(0,0,0,0.04)' }}>
            <Title level={5} style={{ marginBottom: 8 }}>检查方案目录</Title>
            <Text type="secondary" style={{ display: 'block', marginBottom: 12 }}>
              勾选需要生成的章节；有数据库检索的方法可预览内容并选择生成方式与参考文件。
            </Text>
            {outlineTree.length > 0 ? (
              <PlanOutlinePanel
                outlineTree={outlineTree}
                initialSelection={outlineSelection}
                onConfirm={handleOutlineConfirm}
                loading={isStreaming}
              />
            ) : (
              <>
                <Card size="small" styles={{ body: { maxHeight: 520, overflow: 'auto', padding: 16 } }}>
                  <MarkdownRenderer
                    content={
                      JYLLM_ENGINE_V2
                        ? planOutlineStreamShown || plannerSkeletonTarget
                        : plannerMarkdown || buildPlannerMarkdownFromPrompts(plannerPrompts)
                    }
                  />
                </Card>
                <div style={{ marginTop: 24, textAlign: 'center' }}>
                  <Button type="primary" size="large" onClick={() => handleOutlineConfirm({ selectedSections: ['ch1', 'ch2'], sectionOptions: {} })}>
                    开始生成
                  </Button>
                </div>
              </>
            )}
          </div>
        );
      case 'generating':
        return null;
      default:
        return null;
    }
  };

  const visibleMessages = (showAllMessages || !isStreaming ? messages : messages.slice(Math.max(0, messages.length - 3)))
    .filter(msg => msg.content && msg.content.trim().length > 0);

  return (
    <div
      style={{
        display: 'flex',
        width: '100%',
        height: '100%',
        minHeight: 0,
        overflow: 'hidden',
        boxSizing: 'border-box',
        background: '#f5f5f5',
      }}
    >
      <div
        style={{
          width: historyCollapsed ? 36 : 260,
          minWidth: historyCollapsed ? 36 : 260,
          height: '100%',
          minHeight: 0,
          flexShrink: 0,
          background: '#fff',
          borderRight: '1px solid #e5e7eb',
          display: 'flex',
          flexDirection: 'column',
          zIndex: 10,
          overflow: 'hidden',
          transition: 'all 0.3s cubic-bezier(0.4, 0, 0.2, 1)',
        }}
      >
        {historyCollapsed ? (
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', paddingTop: 12 }}>
            <Button type="primary" icon={<MenuUnfoldOutlined />} onClick={() => setHistoryCollapsed(false)} style={{ padding: 8, minWidth: 28, height: 28, borderRadius: 6 }} />
          </div>
        ) : (
          <>
            <div style={{ padding: '8px 12px', borderBottom: '1px solid #e5e7eb', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <Text strong style={{ fontSize: 13 }}>左侧面板</Text>
              <Button type="text" size="small" icon={<MenuFoldOutlined style={{ fontSize: 12 }} />} onClick={() => setHistoryCollapsed(true)} style={{ padding: 0, minWidth: 20 }} />
            </div>
            <div style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column' }}>
              <div style={{ flex: 1, minHeight: 0, borderBottom: '1px solid #f0f0f0' }}>
                <div style={{ padding: '8px 12px', fontSize: 12, color: '#8c8c8c' }}>
                  <HistoryOutlined style={{ marginRight: 6 }} />
                  历史记录
                </div>
                <div style={{ height: 'calc(100% - 34px)', overflow: 'auto', padding: '4px 0' }}>
                  <HistoryList
                    onRecordClick={handleHistoryClick}
                    userId="admin"
                    currentTaskId={taskId}
                    refreshKey={historyRefreshKey}
                    onDeleted={(id) => {
                      if (id === taskId) {
                        message.info('已删除当前正在查看的历史方案，可继续新建或选择其他记录。');
                      }
                    }}
                  />
                </div>
              </div>
              {/* <div style={{ flex: 1, minHeight: 0 }}>
                <div style={{ padding: '8px 12px', fontSize: 12, color: '#8c8c8c', borderBottom: '1px solid #f0f0f0' }}>
                  模板库
                </div>
                <div style={{ height: 'calc(100% - 34px)', overflow: 'hidden' }}>
                  <TemplateSidebar userId="admin" onApplyTemplate={handleApplyTemplate} />
                </div>
              </div> */}
            </div>
          </>
        )}
      </div>

      <div
        style={{
          flex: 1,
          display: 'flex',
          flexDirection: 'row',
          minWidth: 0,
          minHeight: 0,
          overflow: 'hidden',
          alignItems: 'stretch',
        }}
      >
        <div
          style={{
            flex: 1,
            display: 'flex',
            flexDirection: 'column',
            minWidth: 280,
            minHeight: 0,
            overflow: 'hidden',
            position: 'relative',
          }}
        >
        <div style={{ padding: '12px 24px', background: '#fff', borderBottom: '1px solid #e5e7eb', zIndex: 10, flexShrink: 0 }}>
          <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
            <div>
              <Title level={4} style={{ margin: 0, fontSize: 16 }}>基于大模型的辅助设计</Title>
              <Text type="secondary" style={{ fontSize: 12 }}>
                {/* 固定为配置模式 */}
                {`当前阶段：${currentStep === 'device' ? '配置参数' : currentStep === 'environment' ? '（环境步骤已跳过）' : currentStep === 'planner_thinking' ? '规划大纲中' : currentStep === 'planner_review' ? '检查大纲' : currentStep === 'generating' ? '生成中' : currentStep === 'reviewing' ? '审核确认' : '完成'}`}
              </Text>
            </div>
            {/* 模式选择已注释：页面固定为配置模式 */}
            {/* <Segmented
              options={[
                { label: '配置模式', value: 'config' },
                { label: '纯LLM模式', value: 'pure_llm' },
              ]}
              value={generationMode}
              disabled={isStreaming}
              onChange={(v) => {
                const mode = v as GenerationMode;
                setGenerationMode(mode);
                try {
                  localStorage.setItem(JYLLM_MODE_STORAGE_KEY, mode);
                } catch {
                  // ignore
                }
              }}
            /> */}
            <div style={{ minWidth: 220 }}>
              <Text type="secondary" style={{ fontSize: 12, display: 'block', marginBottom: 4 }}>生成用 LLM（MXGL 配置）</Text>
              <Select
                allowClear
                placeholder="默认首个已启用模型"
                options={llmOptions}
                value={llmModelKey || undefined}
                onChange={(v) => {
                  const next = (v as string) || '';
                  setLlmModelKey(next);
                  try {
                    if (next) localStorage.setItem(JYLLM_LLM_STORAGE_KEY, next);
                    else localStorage.removeItem(JYLLM_LLM_STORAGE_KEY);
                  } catch {
                    /* ignore */
                  }
                }}
                style={{ width: '100%' }}
              />
            </div>
          </div>
        </div>

        <div
          ref={chatBoxRef}
          style={{
            flex: 1,
            minHeight: 0,
            overflowY: 'auto',
            overflowX: 'hidden',
            padding: '24px',
            background: '#f9fafb',
            display: 'flex',
            flexDirection: 'column',
          }}
        >
          <div style={{ maxWidth: 900, margin: '0 auto', width: '100%', paddingBottom: isStreaming ? 100 : 0 }}>
            {messages.length === 0 ? (
              <div style={{ textAlign: 'center', padding: '60px 20px' }}>
                <Spin size="large" />
                <Text type="secondary" style={{ display: 'block', marginTop: 16 }}>正在初始化...</Text>
              </div>
            ) : (
              <>
                {isStreaming && messages.length > 3 && !showAllMessages && (
                  <div style={{ textAlign: 'center', marginBottom: 24 }}>
                    <Button 
                      type="dashed" 
                      size="small" 
                      onClick={() => setShowAllMessages(true)}
                      style={{ borderRadius: 16, fontSize: 12, color: '#8c8c8c' }}
                    >
                      展开历史消息 ({messages.length - 3} 条)
                    </Button>
                  </div>
                )}
                
                {visibleMessages.map((msg) => (
                  <ChatMessage key={msg.id} message={msg} />
                ))}
                
                <div style={{ marginTop: 24 }}>
                  {renderStepContent()}
                </div>
              </>
            )}
          </div>
        </div>

        {isStreaming && currentStep === 'generating' && (
          <div style={{ 
            position: 'absolute', 
            bottom: 80, 
            left: '50%', 
            transform: 'translateX(-50%)', 
            zIndex: 100,
            width: '100%',
            maxWidth: 600,
            padding: '0 24px',
            animation: 'slideUp 0.3s ease-out'
          }}>
            <Card 
              size="small" 
              style={{ 
                borderRadius: 12, 
                boxShadow: '0 4px 16px rgba(0,0,0,0.08)',
                border: '1px solid #1890ff',
                cursor: 'pointer',
                animation: 'pulse 2s infinite'
              }}
              onClick={() => setDrawerOpen(!drawerOpen)}
            >
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                  <Spin size="small" />
                  <div>
                    <div style={{ fontSize: 14, fontWeight: 500, color: '#1890ff' }}>
                      正在生成第 {currentGeneratingChapter !== null ? currentGeneratingChapter : 1} 章...
                    </div>
                    <div style={{ fontSize: 12, color: '#8c8c8c' }}>
                      {drawerOpen ? '点击收起右侧面板' : '点击展开右侧面板查看实时内容'}
                    </div>
                  </div>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
                  <Progress type="circle" percent={generationProgress} size={40} />
                  {drawerOpen ? <MenuFoldOutlined style={{ color: '#1890ff' }} /> : <MenuUnfoldOutlined style={{ color: '#1890ff' }} />}
                </div>
              </div>
            </Card>
          </div>
        )}

        <div style={{ zIndex: 10, flexShrink: 0 }}>
          <ChatInput
            onSend={(input) => {
              addUserMessage(input);
              // 固定为配置模式，纯LLM模式已注释
              // if (generationMode === 'pure_llm') {
              //   void runPureLlmPipeline(input);
              // } else {
                addAIMessage('请使用"配置试验参数"按钮来设置试验参数。', 'text');
              // }
            }}
            onClear={handleClear}
            onStop={handleStop}
            onConfig={() => setConfigModalOpen(true)}
            isStreaming={isStreaming}
            disabled={
              // 固定为配置模式的禁用逻辑
              currentStep === 'reviewing' || currentStep === 'complete' || currentStep === 'planner_review' || currentStep === 'planner_thinking'
            }
            placeholder={
              // 固定为配置模式的占位符
              currentStep === 'planner_thinking'
                ? 'Planner正在规划大纲...'
                : isStreaming
                  ? '生成中，请稍候...'
                  : '点击下方"配置试验参数"按钮开始...'
            }
          />
        </div>
        </div>

        {drawerOpen && (
          <div
            role="separator"
            aria-orientation="vertical"
            title="拖动调整方案栏宽度"
            onMouseDown={onSchemeResizeMouseDown}
            style={{
              width: 6,
              flexShrink: 0,
              cursor: 'col-resize',
              alignSelf: 'stretch',
              marginLeft: -1,
              borderLeft: '1px solid #e5e7eb',
              background: 'linear-gradient(90deg, #ebebeb 0%, #f7f7f7 55%, #fafafa 100%)',
            }}
          />
        )}

        <ContentDrawer
          open={drawerOpen}
          onClose={() => setDrawerOpen(false)}
          modules={modules}
          isGenerating={isStreaming}
          currentGeneratingChapter={currentGeneratingChapter}
          taskId={taskId}
          onFeedbackSave={handleFeedbackSave}
          onFeedbackRewrite={handleFeedbackRewrite}
          feedbackRewriteLoading={feedbackRewriteLoading}
          onConfirm={handleConfirm}
          onExport={handleExport}
          onSyncToKm={handleSyncToKm}
          onResave={handleResave}
          mode={drawerMode}
          panelWidthPx={schemePanelWidth}
          progress={generationProgress}
          onChapterContentChange={handleChapterContentChange}
          onRequestRegionRewrite={handleRequestRegionRewrite}
          onUndoRevision={undoLastRevision}
          canUndoRevision={revisionHistory.length > 0}
          exportLoading={exportingKind !== null}
          syncLoading={syncingToKm}
          overallScore={overallScore}
          overallComment={overallComment}
          onOverallScoreChange={setOverallScore}
          onOverallCommentChange={setOverallComment}
          historyViewMode={!!historyViewTaskId && historyViewTaskId === taskId}
          historyFeedback={historyViewFeedback}
        />
      </div>
      
      <Modal
        title="保存方案"
        open={showSchemeNameModal}
        onOk={handleSaveScheme}
        onCancel={() => setShowSchemeNameModal(false)}
        okText="保存"
        cancelText="取消"
      >
        <div style={{ marginBottom: 8 }}>请输入方案名称：</div>
        <Input
          value={schemeNameInput}
          onChange={(e) => setSchemeNameInput(e.target.value)}
          placeholder="例如：无人机20260707_103045"
          onPressEnter={handleSaveScheme}
          autoFocus
        />
        {appliedTemplateName && (
          <div style={{ marginTop: 12 }}>
            <Tag color="blue">已应用模板：{appliedTemplateName}</Tag>
          </div>
        )}
      </Modal>
      
      <ConfigModal
        open={configModalOpen}
        onClose={() => setConfigModalOpen(false)}
        onSave={handleConfigModalSave}
        contentHeight="calc(90vh - 200px)"
      />

      <SchemeKmSyncModal
        open={kmSyncModalOpen}
        initialMeta={kmSyncMeta || buildJyllmKmMeta({ taskId: taskId || 'unknown', fileType: 'docx' })}
        file={kmSyncFile}
        onClose={() => {
          setKmSyncModalOpen(false);
          setKmSyncFile(null);
          setKmSyncMeta(null);
        }}
      />

      <RegionRewriteDrawer
        open={regionRewriteOpen}
        spec={regionRewriteSpec}
        regionPreview={regionRewritePreview}
        originalRegion={regionRewriteOriginal}
        streamingText={regionRewriteStreaming}
        loading={regionRewriteLoading}
        mergedChapter={regionRewriteMerged}
        comment={regionRewriteComment}
        diffConfirmed={regionRewriteDiffConfirmed}
        validationWarnings={regionRewriteWarnings}
        diffStats={regionRewriteDiffStats || undefined}
        onCommentChange={setRegionRewriteComment}
        onDiffConfirmedChange={setRegionRewriteDiffConfirmed}
        onClose={() => {
          if (regionRewriteLoading) stopRegionRewrite();
          setRegionRewriteOpen(false);
        }}
        onStart={() => void runRegionRewrite()}
        onStop={stopRegionRewrite}
        onApply={applyRegionRewrite}
      />
      
      <style dangerouslySetInnerHTML={{__html: `
        @keyframes pulse {
          0% { box-shadow: 0 0 0 0 rgba(24, 144, 255, 0.4); }
          70% { box-shadow: 0 0 0 6px rgba(24, 144, 255, 0); }
          100% { box-shadow: 0 0 0 0 rgba(24, 144, 255, 0); }
        }
        @keyframes slideUp {
          from { transform: translate(-50%, 20px); opacity: 0; }
          to { transform: translate(-50%, 0); opacity: 1; }
        }
        @keyframes fadeIn {
          from { opacity: 0; transform: translateY(10px); }
          to { opacity: 1; transform: translateY(0); }
        }
      `}} />
    </div>
  );
}

function JYLLMConfigPage() {
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
      <JYLLMConfigPageContent />
    </Suspense>
  );
}

export default JYLLMConfigPage;