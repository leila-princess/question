'use client';

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Modal, Steps, Upload, Button, Space, Checkbox, message, Row, Col, Tree, Alert,
  Typography, Empty, Radio, Progress, Select,
} from 'antd';
import { InboxOutlined, FileSearchOutlined, PlusOutlined, DeleteOutlined, ClearOutlined } from '@ant-design/icons';
import MethodFileContentReview from './MethodFileContentReview';
import MethodFileMetadataReview from './MethodFileMetadataReview';
import type { UploadFileItem } from '@/app/ZSGL/WJGL/components/UploadModal';
import WjglLibraryFilePicker from '@/app/ZSGL/WJGL/components/WjglLibraryFilePicker';
import {
  DEFAULT_WJGL_CATEGORY,
  METHOD_EXTRACT_CATEGORY_OPTIONS,
} from '@/app/ZSGL/WJGL/constants/categories';
import type { WjglFileRecord } from '@/app/ZSGL/WJGL/utils/wjglFileList';
import {
  commitWjglFileUpload,
  enrichUploadMetaFromMarkdown,
  fallbackMetaFromFileName,
  getWjglUploadUserLabel,
  parseUploadFileMetadata,
  resolveMetaForLibraryCommit,
  syncWjglFileMetadata,
} from '@/app/ZSGL/WJGL/utils/wjglUploadPipeline';
import {
  buildTocTreeNodes,
  fetchFullDocumentHtmlUpload,
  fetchMineruPreviewStatus,
  formatMineruDoneMessage,
  formatMineruPollMessage,
  isMineruPollTimeout,
  loadMineruPreviewResult,
  pollMineruPreviewHtml,
  findTocNodeBySectionNo,
  normalizeSectionNo,
  tocTreeNodesToItems,
  loadLibraryDocumentContent,
  type MineruPreviewResult,
  type TocItem,
  type TocTreeNode,
} from '@/app/ZSGL/WJGL/components/documentTocUtils';
import type { GraphRecommendation } from '@/app/ZSGL/WJGL/components/semanticTypes';
import {
  collectDescendantKeys,
  createMethodDraftFromSelection,
  draftsToSectionGroups,
  findTocNodeByKey,
  buildParserHeadingTree,
  type MethodExtractDraft,
  type FullHeadingItem,
} from '../utils/methodExtractUtils';
import { extractMethodFromSections, extractMethodFromSectionsFile, extractMethodAuto, extractMethodAutoStream, fetchDocumentHeadings, fetchPreviewParseContent, checkMethodFileDuplicate, type FileDuplicateCheckResult } from '../api';

const { Dragger } = Upload;
const { Text } = Typography;
type ExtractMode = 'llm' | 'manual';
type FileSource = 'upload' | 'library';

const LLM_ACCEPT = '.pdf,.doc,.docx,.md,.txt';
const MANUAL_ACCEPT = '.pdf,.doc,.docx';
const LLM_EXTENSIONS = ['pdf', 'doc', 'docx', 'md', 'txt'];
const MANUAL_EXTENSIONS = ['pdf', 'doc', 'docx'];

function newFileItem(file: File): UploadFileItem {
  const uid = `method-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  return {
    uid,
    file,
    name: file.name,
    parseStatus: 'pending',
    uploadStatus: 'local',
    syncChroma: false,
    meta: {
      title: file.name.replace(/\.[^/.]+$/, ''),
      std_code: '',
      std_name: file.name.replace(/\.[^/.]+$/, ''),
      publish_date: '',
      issuer: '',
      abstract: '',
      keywords: [],
      file_type: file.name.split('.').pop() || '',
    },
    tocTree: [],
    documentToc: [],
    selectedSectionKey: '',
    scrollSectionNo: '',
    fullHtml: '',
    fullLoading: true,
    graphCandidates: [],
    equipmentTabs: [],
  };
}

function getUploadUserLabel(): string {
  return getWjglUploadUserLabel();
}

function libraryStubFileItem(record: WjglFileRecord): UploadFileItem {
  const displayName = record.title || record.std_name || record.doc_id;
  return {
    uid: `lib-${record.doc_id}`,
    file: new File([], displayName),
    name: displayName,
    parseStatus: 'ready',
    uploadStatus: 'uploaded',
    syncChroma: false,
    meta: {
      title: record.title || record.std_name || displayName,
      std_name: record.std_name || record.title || '',
      std_code: record.std_code || '',
      publish_date: record.publish_date || '',
      issuer: record.issuer || '',
      abstract: record.abstract || '',
      keywords: record.keywords ? String(record.keywords).split(/[,，;；]/).map((s) => s.trim()).filter(Boolean) : [],
      file_type: record.file_type || '',
    },
    tocTree: [],
    documentToc: [],
    selectedSectionKey: '',
    scrollSectionNo: '',
    fullHtml: '',
    fullLoading: false,
    graphCandidates: [],
    equipmentTabs: [],
  };
}

export interface MethodExtractWizardResult {
  methods: unknown[];
  methods_count?: number;
  batch_count?: number;
  source_file_id?: string;
  object_name?: string;
  graphCandidates?: GraphRecommendation[];
}

export interface MethodStreamContext {
  jobId: string;
  methodIndex: number;
  documentDir: string;
  sourceFileId?: string;
}

interface MethodExtractWizardProps {
  open: boolean;
  onClose: () => void;
  onComplete: (result: MethodExtractWizardResult) => void;
  onStreamStart?: () => void;
  onStreamMethod?: (method: unknown, ctx: MethodStreamContext) => void;
  onStreamDone?: (ctx: { jobId?: string; documentDir?: string; methodsCount?: number }) => void;
}

function buildLibraryCommitExtras(item: UploadFileItem) {
  return {
    documentToc: item.documentToc?.length
      ? item.documentToc
      : (item.tocTree?.length ? tocTreeNodesToItems(item.tocTree) : undefined),
    documentContent: {
      html: item.fullHtml,
      markdown: item.fullMarkdown,
      mineruKey: item.mineruKey || item.contentDocId,
    },
  };
}

export default function MethodExtractWizard({
  open,
  onClose,
  onComplete,
  onStreamStart,
  onStreamMethod,
  onStreamDone,
}: MethodExtractWizardProps) {
  const [step, setStep] = useState(0);
  const [fileSource, setFileSource] = useState<FileSource>('upload');
  const [category, setCategory] = useState(DEFAULT_WJGL_CATEGORY);
  const [libraryRecord, setLibraryRecord] = useState<WjglFileRecord | null>(null);
  const [libraryLoading, setLibraryLoading] = useState(false);
  const [fileItem, setFileItem] = useState<UploadFileItem | null>(null);
  const [saveToLibrary, setSaveToLibrary] = useState(true);
  const [libraryDocId, setLibraryDocId] = useState('');
  const [libraryObjectName, setLibraryObjectName] = useState('');
  const [useChromaSource, setUseChromaSource] = useState(false);
  const [duplicateInfo, setDuplicateInfo] = useState<FileDuplicateCheckResult | null>(null);
  /** 目录树多选（定义方法步骤） */
  const [checkedTreeKeys, setCheckedTreeKeys] = useState<string[]>([]);
  /** 用户手动定义的本批待提取方法 */
  const [methodDrafts, setMethodDrafts] = useState<MethodExtractDraft[]>([]);
  /** 多批次累计已提取的方法 */
  const [accumulatedMethods, setAccumulatedMethods] = useState<unknown[]>([]);
  const [extractBatchCount, setExtractBatchCount] = useState(0);
  const [useLlmMerge, setUseLlmMerge] = useState(false);
  const [extracting, setExtracting] = useState(false);
  const [savingLibrary, setSavingLibrary] = useState(false);
  /** 编辑某条 draft 的子节勾选 */
  const [editingDraftId, setEditingDraftId] = useState<string | null>(null);
  const [draftChildKeys, setDraftChildKeys] = useState<string[]>([]);
  const [extractMode, setExtractMode] = useState<ExtractMode>('manual');
  const [previewCacheKey, setPreviewCacheKey] = useState('');
  const [metaLoading, setMetaLoading] = useState(false);
  const [metaExtracted, setMetaExtracted] = useState(false);
  const tocFromSectionsRef = useRef(false);
  const tocLockedRef = useRef(false);
  const [llmExtracting, setLlmExtracting] = useState(false);
  const [llmProgressText, setLlmProgressText] = useState('');
  const [llmMethodsReady, setLlmMethodsReady] = useState(0);
  const streamAbortRef = React.useRef<AbortController | null>(null);
  const tocClickSuppressRef = useRef(false);
  const tocTreePanelRef = useRef<HTMLDivElement>(null);
  const [activeSectionNo, setActiveSectionNo] = useState('');

  const reset = (opts?: { abortStream?: boolean }) => {
    if (opts?.abortStream) {
      streamAbortRef.current?.abort();
      streamAbortRef.current = null;
    }
    setStep(0);
    setFileSource('upload');
    setCategory(DEFAULT_WJGL_CATEGORY);
    setLibraryRecord(null);
    setLibraryLoading(false);
    setFileItem(null);
    setSaveToLibrary(true);
    setLibraryDocId('');
    setLibraryObjectName('');
    setUseChromaSource(false);
    setDuplicateInfo(null);
    setCheckedTreeKeys([]);
    setMethodDrafts([]);
    setAccumulatedMethods([]);
    setExtractBatchCount(0);
    setUseLlmMerge(false);
    setExtracting(false);
    setSavingLibrary(false);
    setEditingDraftId(null);
    setDraftChildKeys([]);
    setExtractMode('manual');
    setPreviewCacheKey('');
    setMetaLoading(false);
    setMetaExtracted(false);
    tocFromSectionsRef.current = false;
    tocLockedRef.current = false;
    setLlmExtracting(false);
    setLlmProgressText('');
    setLlmMethodsReady(0);
    setActiveSectionNo('');
  };

  const handleClose = () => {
    reset({ abortStream: true });
    onClose();
  };

  useEffect(() => {
    // 流式提取时 handleStreamStart 会关向导但不应 abort SSE（否则前端显示「已取消」而后台仍跑）
    if (!open && !llmExtracting) {
      reset();
    }
  }, [open, llmExtracting]);

  const updateItem = useCallback((patch: Partial<UploadFileItem>) => {
    setFileItem((prev) => (prev ? { ...prev, ...patch } : prev));
  }, []);

  const applyDuplicateInfo = useCallback((dup: FileDuplicateCheckResult) => {
    if (!dup.duplicate || !dup.doc_id) return;
    setLibraryDocId(dup.doc_id);
    setLibraryObjectName(dup.minio_id || '');
    setUseChromaSource(!!dup.use_chroma_source);
    setDuplicateInfo(dup);
    const methodNames = (dup.linked_methods || []).map((m) => m.name || m.id).filter(Boolean);
    const methodHint = methodNames.length
      ? `关联试验方法：${methodNames.join('、')}`
      : '当前尚无关联试验方法';
    const chromaHint = dup.use_chroma_source ? '；PDF 将使用已有向量库文本进行 LLM 提取' : '';
    message.info(`文件已存在于库中（${dup.file_name || dup.doc_id}），${methodHint}${chromaHint}，已跳过入库`);
  }, []);

  const checkDuplicateForFile = useCallback(async (file: File) => {
    try {
      const dup = await checkMethodFileDuplicate(file);
      if (dup.duplicate) {
        applyDuplicateInfo(dup);
      } else {
        setLibraryDocId('');
        setLibraryObjectName('');
        setUseChromaSource(false);
        setDuplicateInfo(null);
      }
    } catch {
      /* 查重失败不阻断流程 */
    }
  }, [applyDuplicateInfo]);

  const showMetadataStep = useMemo(
    () => fileSource === 'upload' && saveToLibrary && !libraryDocId && !!fileItem,
    [fileSource, saveToLibrary, libraryDocId, fileItem],
  );

  const stepItems = useMemo(() => {
    const items = [{ title: '选择文件', content: '来源与类别' }];
    items.push({ title: '文档预览', content: '解析正文与目录' });
    if (showMetadataStep) {
      items.push({ title: '元数据审核', content: '确认入库信息' });
    }
    items.push(
      extractMode === 'llm'
        ? { title: 'LLM 提取', content: '自动发现方法 · 流式审阅' }
        : { title: '选章并提取', content: '阅读器 · 目录 · 方法列表' },
    );
    return items;
  }, [showMetadataStep, extractMode]);

  const contentStepIndex = 1;
  const extractStepIndex = Math.max(0, stepItems.length - 1);
  const metadataStepIndex = showMetadataStep ? extractStepIndex - 1 : -1;
  const stepIsContent = step === contentStepIndex;
  const stepIsMetadata = showMetadataStep && step === metadataStepIndex;
  const stepIsExtract = step === extractStepIndex;

  /** 元数据入库后 showMetadataStep 变 false，步骤条少一项；若 step 仍停在旧末位会只剩步骤条无内容 */
  useEffect(() => {
    const maxStep = Math.max(0, stepItems.length - 1);
    setStep((prev) => (prev > maxStep ? maxStep : prev));
  }, [stepItems.length]);

  const acceptExtensions = extractMode === 'llm' ? LLM_EXTENSIONS : MANUAL_EXTENSIONS;

  const loadLibraryFile = useCallback(async (record: WjglFileRecord) => {
    setLibraryRecord(record);
    setLibraryDocId(record.doc_id);
    setLibraryObjectName(record.object_name);
    setLibraryLoading(true);
    setDuplicateInfo(null);
    setUseChromaSource(false);
    const stub = libraryStubFileItem(record);
    setFileItem({ ...stub, fullLoading: true });
    try {
      const loaded = await loadLibraryDocumentContent({
        docId: record.doc_id,
        category: record.category || category,
        objectName: record.object_name,
        onProgress: (msg) => message.loading({ content: msg, key: 'lib-toc', duration: 0 }),
      });
      message.destroy('lib-toc');
      const tocTree = buildTocTreeNodes(loaded.toc);
      setFileItem({
        ...stub,
        tocTree,
        documentToc: loaded.toc,
        fullHtml: loaded.html,
        fullMarkdown: loaded.markdown,
        fullLoading: false,
        contentDocId: record.doc_id,
        contentCategory: record.category || category,
        selectedSectionKey: '',
        scrollSectionNo: '',
      });
      setCheckedTreeKeys([]);
      setStep(1);
      if (!tocTree.length) {
        message.warning('未识别到章节目录，请确认文档已解析完成');
      }
    } catch (e: unknown) {
      message.destroy('lib-toc');
      message.error(e instanceof Error ? e.message : '加载文件库目录失败');
      setFileItem(null);
      setLibraryRecord(null);
      setLibraryDocId('');
      setLibraryObjectName('');
    } finally {
      setLibraryLoading(false);
    }
  }, [category]);

  const commitFileToLibrary = useCallback(async (item: UploadFileItem) => {
    const meta = await resolveMetaForLibraryCommit(item);
    const extras = buildLibraryCommitExtras({ ...item, meta });
    const isPdf = meta.file_type?.toLowerCase() === 'pdf';
    const result = await commitWjglFileUpload({
      file: item.file,
      uid: item.uid,
      meta,
      category,
      syncChroma: item.syncChroma ?? isPdf,
      duplicateAction: 'skip',
      ...extras,
    });
    if (result.duplicate && result.doc_id) {
      await syncWjglFileMetadata({ docId: result.doc_id, category, meta, ...extras });
    }
    return { result, meta };
  }, [category]);

  const syncExistingLibraryMetadata = useCallback(async (docId: string, item: UploadFileItem) => {
    const meta = await resolveMetaForLibraryCommit(item);
    await syncWjglFileMetadata({
      docId,
      category,
      meta,
      ...buildLibraryCommitExtras({ ...item, meta }),
    });
    return meta;
  }, [category]);

  const handleMetadataConfirm = async () => {
    if (!fileItem?.file?.size) {
      message.warning('请先选择本地文件');
      return;
    }
    setSavingLibrary(true);
    try {
      const dup = await checkMethodFileDuplicate(fileItem.file);
      if (dup.duplicate && dup.doc_id) {
        applyDuplicateInfo(dup);
        const meta = await syncExistingLibraryMetadata(dup.doc_id, fileItem);
        updateItem({ meta, metaExtracted: true });
        message.info(`文件已存在于库中（${dup.doc_id}），已同步元数据`);
        setStep(extractStepIndex);
        return;
      }
      const { result, meta } = await commitFileToLibrary(fileItem);
      updateItem({ meta, metaExtracted: true });
      setLibraryDocId(result.doc_id);
      setLibraryObjectName(result.object_name);
      if (result.duplicate) {
        message.info(`文件已存在于库中（${result.doc_id}），已同步元数据`);
      } else {
        message.success(`已加入文件库（${result.doc_id}）`);
      }
      setStep(extractStepIndex);
    } catch (e: unknown) {
      message.error(e instanceof Error ? e.message : '入库失败');
    } finally {
      setSavingLibrary(false);
    }
  };

  const applyMineruResult = useCallback((key: string, result: MineruPreviewResult) => {
    const patch: Partial<UploadFileItem> = {
      fullHtml: result.html,
      fullMarkdown: result.markdown || '',
      contentDocId: key,
      contentCategory: '_preview',
      mineruKey: key,
      mineruLoading: false,
      mineruStatus: formatMineruDoneMessage({ mdPath: result.mdPath, mineruCached: result.mineruCached }),
      contentError: undefined,
    };
    if (result.toc?.length && !tocFromSectionsRef.current && !tocLockedRef.current) {
      const nextToc = buildTocTreeNodes(result.toc as Parameters<typeof buildTocTreeNodes>[0]);
      patch.tocTree = nextToc;
      patch.documentToc = result.toc as TocItem[];
    }
    updateItem(patch);
  }, [updateItem]);

  const resumeMineruPoll = useCallback(async (entry: UploadFileItem) => {
    const key = (entry.mineruKey || entry.contentDocId || '').trim();
    if (!key) {
      message.warning('无 MinerU 任务可继续，请重新上传');
      return;
    }
    updateItem({
      mineruLoading: true,
      mineruStatus: '正在恢复 MinerU 轮询…',
      contentError: undefined,
    });
    try {
      const status = await fetchMineruPreviewStatus(key);
      if (status.status === 'done') {
        applyMineruResult(key, await loadMineruPreviewResult(key));
        return;
      }
      if (status.status === 'error') {
        throw new Error(String(status.error || 'MinerU 解析失败'));
      }
      const result = await pollMineruPreviewHtml(key, {
        onProgress: (task) => {
          updateItem({
            mineruLoading: true,
            mineruStatus: formatMineruPollMessage(task),
          });
        },
      });
      applyMineruResult(key, result);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : 'MinerU 轮询失败';
      updateItem({
        mineruLoading: false,
        mineruStatus: msg,
        mineruKey: key,
        contentDocId: key,
        contentCategory: '_preview',
        ...(isMineruPollTimeout(e) ? { contentError: msg } : {}),
      });
    }
  }, [updateItem, applyMineruResult]);

  const loadMetadata = useCallback(async (entry: UploadFileItem) => {
    if (!entry.file?.size || metaExtracted) return;
    setMetaLoading(true);
    try {
      let parseWarnings: string[] = [];
      try {
        const meta = await resolveMetaForLibraryCommit(entry);
        updateItem({
          meta,
          parseWarnings: entry.parseWarnings || [],
          syncChroma: (meta.file_type || '').toLowerCase() === 'pdf',
        });
      } catch (metaErr) {
        console.warn('metadata resolve failed:', metaErr);
        parseWarnings = ['元数据自动提取失败，已使用文件名兜底'];
        updateItem({ parseWarnings: [...(entry.parseWarnings || []), ...parseWarnings] });
      }
      setMetaExtracted(true);
    } finally {
      setMetaLoading(false);
    }
  }, [metaExtracted, updateItem]);

  const parseFile = useCallback(async (entry: UploadFileItem, mode: 'full' | 'resume-mineru' = 'full') => {
    if (mode === 'resume-mineru') {
      await resumeMineruPoll(entry);
      return;
    }
    setMetaExtracted(false);
    tocFromSectionsRef.current = false;
    tocLockedRef.current = false;
    updateItem({
      parseStatus: 'parsing',
      parseError: undefined,
      contentError: undefined,
      fullLoading: true,
      fullHtml: '',
      fullMarkdown: '',
      mineruKey: undefined,
      contentDocId: undefined,
      contentCategory: undefined,
      mineruLoading: false,
      mineruStatus: undefined,
      meta: fallbackMetaFromFileName(entry.name),
      parseWarnings: [],
      tocTree: [],
      documentToc: [],
      tocSource: undefined,
      tocRoutingRev: undefined,
      tocLocked: false,
      graphCandidates: [],
      equipmentTabs: [],
    });
    try {
      const ext = entry.name.split('.').pop()?.toLowerCase() || '';
      const useFlglHeadings = ['pdf', 'doc', 'docx'].includes(ext);
      let tocTree: TocTreeNode[] = [];
      let documentToc: TocItem[] = [];
      let parseWarnings: string[] = [];
      let tocSource = '';
      let tocRoutingRev = '';
      let tocLocked = false;

      let fullHtml = '';
      let fullMarkdown = '';
      let mineruLoading = false;
      let mineruStatus: string | undefined;
      let storedMineruKey: string | undefined;
      let previewKeyForContent = '';

      if (useFlglHeadings) {
        try {
          const headingsRes = await fetchDocumentHeadings(entry.file, { mode: 'full' });
          if (headingsRes.success && headingsRes.data) {
            setPreviewCacheKey(String(headingsRes.data.preview_cache_key || ''));
            const cacheKey = String(headingsRes.data.preview_cache_key || '').trim();
            previewKeyForContent = cacheKey;
            const extractMethod = String(
              (headingsRes.data as { extract_method?: string }).extract_method || '',
            );
            tocSource = String((headingsRes.data as { toc_source?: string }).toc_source || extractMethod);
            tocRoutingRev = String((headingsRes.data as { toc_routing_rev?: string }).toc_routing_rev || '');
            const fromSections = tocSource.includes('parse_sections') || extractMethod.includes('parse_sections');
            tocFromSectionsRef.current = fromSections;
            tocLocked = fromSections;
            tocLockedRef.current = fromSections;
            const fullHeadings = (headingsRes.data.full_headings || []) as FullHeadingItem[];
            if (fromSections && fullHeadings.length) {
              tocTree = buildParserHeadingTree(fullHeadings);
              documentToc = tocTreeNodesToItems(tocTree);
            } else if (headingsRes.data.toc?.length) {
              documentToc = headingsRes.data.toc as TocItem[];
              tocTree = buildTocTreeNodes(documentToc);
            }
            const debugDir = String((headingsRes.data as { debug_dir?: string }).debug_dir || '');
            const sectionsCount = (headingsRes.data as { sections_count?: number }).sections_count;
            const hasPreviewContent = Boolean((headingsRes.data as { has_preview_content?: boolean }).has_preview_content);
            if (debugDir) {
              console.info(
                '[MethodExtract] 解析调试目录:',
                debugDir,
                'method=',
                extractMethod,
                'toc=',
                tocSource,
                'rev=',
                tocRoutingRev,
                'sections=',
                sectionsCount,
              );
            }
            if (fromSections) {
              parseWarnings.push(
                `目录已过滤封面/目录页，从正式章节展示（${fullHeadings.length || documentToc.length} 项，rev=${tocRoutingRev || '未知'}）`,
              );
            } else if (documentToc.length) {
              parseWarnings.push(
                `目录走 heading_extract（非 04），sections_count=${sectionsCount ?? 0}；若混乱请确认 MinerU 已启用且后端 rev=${tocRoutingRev || '未返回'}`,
              );
            } else {
              parseWarnings.push('未获取到章节目录，请检查后端 FLGL 解析服务');
            }

            if (cacheKey && hasPreviewContent) {
              try {
                const [htmlFromCache, mdFromCache] = await Promise.all([
                  fetchPreviewParseContent(cacheKey, 'html').catch(() => ''),
                  fetchPreviewParseContent(cacheKey, 'md').catch(() => ''),
                ]);
                if (htmlFromCache.trim()) fullHtml = htmlFromCache;
                if (mdFromCache.trim()) fullMarkdown = mdFromCache;
                if (fullHtml.trim() || fullMarkdown.trim()) {
                  parseWarnings.push('正文已与目录同源解析（跳过二次 MinerU 上传）');
                }
              } catch (previewErr: unknown) {
                const msg = previewErr instanceof Error ? previewErr.message : String(previewErr);
                if (msg !== 'PARSE_PENDING') {
                  console.warn('[MethodExtract] preview-parse-content failed:', previewErr);
                }
              }
            }
          }
        } catch (headErr) {
          console.warn('document-headings-upload failed:', headErr);
          parseWarnings.push('章节目录解析失败，可稍后重试或继续仅预览正文');
        }
      }

      if (!fullHtml.trim() && !fullMarkdown.trim()) {
        const { html: initialHtml, mineruKey, mineruOnly } = await fetchFullDocumentHtmlUpload(entry.file);
        fullHtml = initialHtml;
        mineruLoading = Boolean(mineruKey);
        mineruStatus = mineruKey
          ? (mineruOnly ? 'MinerU 解析中（未使用 fitz 预览）…' : 'MinerU 后台解析中，完成后自动替换正文…')
          : undefined;
        storedMineruKey = mineruKey;

        if (mineruKey) {
          if (mineruOnly) {
            try {
              const polled = await pollMineruPreviewHtml(mineruKey, {
                onProgress: (task) => {
                  updateItem({
                    mineruLoading: true,
                    mineruStatus: formatMineruPollMessage(task),
                  });
                },
              });
              fullHtml = polled.html;
              fullMarkdown = polled.markdown || '';
              if (polled.toc?.length && !tocFromSectionsRef.current && !tocLocked) {
                documentToc = polled.toc as TocItem[];
                tocTree = buildTocTreeNodes(documentToc);
              }
              mineruLoading = false;
              mineruStatus = formatMineruDoneMessage({
                mdPath: polled.mdPath,
                mineruCached: polled.mineruCached,
              });
            } catch (pollErr: unknown) {
              const msg = pollErr instanceof Error ? pollErr.message : 'MinerU 解析失败';
              mineruLoading = false;
              mineruStatus = msg;
              if (!fullHtml.trim()) {
                throw new Error(msg);
              }
              parseWarnings.push(`MinerU 高清解析未完成：${msg}；当前使用快速预览正文`);
            }
          } else {
            void pollMineruPreviewHtml(mineruKey, {
              onProgress: (task) => {
                updateItem({
                  mineruLoading: true,
                  mineruStatus: formatMineruPollMessage(task),
                });
              },
            })
              .then((polled) => {
                applyMineruResult(mineruKey, polled);
              })
              .catch((err: unknown) => {
                const msg = err instanceof Error ? err.message : 'MinerU 解析失败';
                updateItem({
                  mineruLoading: false,
                  mineruStatus: msg,
                  mineruKey,
                  contentDocId: mineruKey,
                  contentCategory: '_preview',
                  ...(isMineruPollTimeout(err) ? { contentError: msg } : {}),
                });
              });
          }
        }
      } else if (previewKeyForContent) {
        storedMineruKey = previewKeyForContent;
      }

      if (!fullHtml.trim() && !fullMarkdown.trim() && !documentToc.length) {
        throw new Error('未能提取到正文或目录，请确认 PDF 有效且已安装 PyMuPDF（pip install pymupdf）或 MinerU 服务可用');
      }

      const resolvedDocumentToc = documentToc.length ? documentToc : tocTreeNodesToItems(tocTree);

      let prefetchedMeta = fallbackMetaFromFileName(entry.name);
      try {
        prefetchedMeta = await resolveMetaForLibraryCommit({
          ...entry,
          fullHtml,
          fullMarkdown,
        });
      } catch {
        /* 摘要预提取失败不阻断正文预览 */
      }

      setCheckedTreeKeys([]);
      updateItem({
        fullHtml,
        fullMarkdown,
        parseWarnings,
        tocTree,
        documentToc: resolvedDocumentToc,
        tocSource: tocSource || undefined,
        tocRoutingRev: tocRoutingRev || undefined,
        tocLocked,
        fullLoading: false,
        parseStatus: 'ready',
        mineruLoading,
        mineruStatus,
        contentDocId: storedMineruKey || undefined,
        contentCategory: storedMineruKey ? '_preview' : undefined,
        mineruKey: storedMineruKey,
        selectedSectionKey: '',
        scrollSectionNo: '',
        meta: prefetchedMeta,
        metaExtracted: Boolean(prefetchedMeta.abstract?.trim()),
      });
      setStep(contentStepIndex);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : '解析失败';
      updateItem({
        parseStatus: 'error',
        parseError: msg,
        fullLoading: false,
        mineruLoading: false,
      });
      setStep(contentStepIndex);
    }
  }, [updateItem, resumeMineruPoll, applyMineruResult, contentStepIndex]);

  const goBackToFileSelect = () => {
    setStep(0);
    setFileItem(null);
    setLibraryRecord(null);
    setLibraryDocId('');
    setLibraryObjectName('');
    setDuplicateInfo(null);
    setUseChromaSource(false);
    setCheckedTreeKeys([]);
    setMethodDrafts([]);
    setPreviewCacheKey('');
  };

  const handlePickFile = (file: File) => {
    const ext = file.name.split('.').pop()?.toLowerCase() || '';
    const allowed = extractMode === 'llm' ? LLM_EXTENSIONS : MANUAL_EXTENSIONS;
    if (!allowed.includes(ext)) {
      message.warning(
        extractMode === 'llm'
          ? 'LLM 模式支持 PDF、Word、Markdown、纯文本'
          : '手动模式仅支持 PDF、Word（.doc/.docx）',
      );
      return false;
    }
    const item = newFileItem(file);
    if (extractMode === 'llm' && (ext === 'md' || ext === 'txt')) {
      item.parseStatus = 'parsing';
      item.fullLoading = true;
      setFileItem(item);
      setLibraryRecord(null);
      void checkDuplicateForFile(file);
      void (async () => {
        try {
          let meta = fallbackMetaFromFileName(file.name);
          let parseWarnings: string[] = [];
          try {
            const parsed = await parseUploadFileMetadata(file);
            meta = parsed.meta;
            parseWarnings = parsed.warnings;
          } catch {
            parseWarnings = ['元数据自动提取失败，已使用文件名兜底'];
          }
          const text = await file.text();
          const enriched = await enrichUploadMetaFromMarkdown(text, meta);
          updateItem({
            fullMarkdown: text,
            fullLoading: false,
            parseStatus: 'ready',
            meta: enriched.meta,
            parseWarnings: [...parseWarnings, ...enriched.warnings],
            syncChroma: false,
          });
          setStep(1);
        } catch {
          updateItem({ fullLoading: false, parseStatus: 'error', parseError: '读取文本失败' });
        }
      })();
      return false;
    }
    setLibraryRecord(null);
    setFileItem(item);
    setStep(1);
    void checkDuplicateForFile(file);
    void parseFile(item);
    return false;
  };

  const ensureLibraryFile = async (): Promise<{ docId: string; objectName: string } | null> => {
    if (!fileItem) return null;
    if (libraryDocId) {
      if (saveToLibrary && (fileItem.file?.size || fileItem.fullMarkdown?.trim())) {
        setSavingLibrary(true);
        try {
          const meta = await syncExistingLibraryMetadata(libraryDocId, fileItem);
          updateItem({ meta, metaExtracted: true });
        } catch (e: unknown) {
          message.warning(e instanceof Error ? e.message : '元数据同步失败');
        } finally {
          setSavingLibrary(false);
        }
      }
      return { docId: libraryDocId, objectName: libraryObjectName };
    }
    if (!fileItem.file?.size) return null;
    setSavingLibrary(true);
    try {
      const dup = await checkMethodFileDuplicate(fileItem.file);
      if (dup.duplicate && dup.doc_id) {
        applyDuplicateInfo(dup);
        const meta = await syncExistingLibraryMetadata(dup.doc_id, fileItem);
        updateItem({ meta, metaExtracted: true });
        message.info(`文件已存在于库中（${dup.doc_id}），已同步元数据`);
        return { docId: dup.doc_id, objectName: dup.minio_id || '' };
      }
      const { result, meta } = await commitFileToLibrary(fileItem);
      updateItem({ meta, metaExtracted: true });
      setLibraryDocId(result.doc_id);
      setLibraryObjectName(result.object_name);
      if (result.duplicate) {
        message.info(`文件已存在于库中（${result.doc_id}），已同步元数据`);
      } else {
        message.success(`已加入文件库（${result.doc_id}）`);
      }
      return { docId: result.doc_id, objectName: result.object_name };
    } catch (e: unknown) {
      message.error(e instanceof Error ? e.message : '加入文件库失败');
      return null;
    } finally {
      setSavingLibrary(false);
    }
  };

  const saveFileToLibrary = ensureLibraryFile;

  const handleConfirmExtract = async () => {
    if (!fileItem) return;
    if (methodDrafts.length === 0) {
      message.warning('请至少添加 1 个方法（在目录中勾选章节后点「添加为一个方法」）');
      return;
    }
    if (saveToLibrary) {
      const saved = await saveFileToLibrary();
      if (!saved) return;
    }
    const sectionsData = draftsToSectionGroups(methodDrafts, treeData);
    if (!sectionsData.length || sectionsData.length !== methodDrafts.length) {
      message.error('部分方法章节无效，请检查待提取列表');
      return;
    }
    const result = await runExtract(sectionsData);
    if (!result?.methods?.length) return;
    const allMethods = [...accumulatedMethods, ...(result.methods || [])];
    onComplete({
      methods: allMethods,
      methods_count: allMethods.length,
      batch_count: extractBatchCount + 1,
      graphCandidates: fileItem.graphCandidates as GraphRecommendation[],
      source_file_id: libraryDocId ? `WJ_${libraryDocId}` : undefined,
      object_name: libraryObjectName || undefined,
    });
    handleClose();
  };

  const handleLlmExtract = async () => {
    if (!fileItem && !libraryDocId) return;
    if (saveToLibrary && fileSource === 'upload' && fileItem?.file?.size) {
      const saved = await ensureLibraryFile();
      if (!saved) return;
    }
    setLlmExtracting(true);
    setLlmMethodsReady(0);
    setLlmProgressText('正在连接 LLM 智能提取服务…');
    onStreamStart?.();
    const hide = message.loading('LLM 智能提取中（流式）…', 0);
    const ac = new AbortController();
    streamAbortRef.current = ac;
    let streamJobId = '';
    let streamDocumentDir = '';
    let methodsReceived = 0;
    let resolvedDocId = libraryDocId;
    let resolvedUseChroma = useChromaSource;
    try {
        if (!resolvedDocId) {
        const dup = fileItem?.file?.size
          ? await checkMethodFileDuplicate(fileItem.file).catch(() => null)
          : null;
        if (dup?.duplicate && dup.doc_id) {
          applyDuplicateInfo(dup);
          resolvedDocId = dup.doc_id;
          resolvedUseChroma = !!dup.use_chroma_source;
        }
      }
      const sourceFileId = resolvedDocId ? `WJ_${resolvedDocId}` : undefined;
      const streamFile = fileItem?.file?.size ? fileItem.file : null;
      const result = await extractMethodAutoStream({
        file: streamFile,
        objectName: libraryObjectName || undefined,
        userId: getUploadUserLabel(),
        skipRecall: false,
        sourceFileId,
        useChromaSource: resolvedUseChroma,
        signal: ac.signal,
        onEvent: (evt) => {
          if (evt.type === 'job_started') {
            streamJobId = String(evt.data.job_id || '');
            streamDocumentDir = String(evt.data.document_dir || '');
            setLlmProgressText('已启动提取任务，等待首个方法…');
          }
          if (evt.type === 'llm_extract_started') {
            setLlmProgressText('正在识别文档结构并逐条提取试验方法…');
          }
          if (evt.type === 'organization_judged') {
            const mode = String(evt.data.organization_mode || '');
            setLlmProgressText(
              mode ? `文档形态：${mode}，准备逐条提取…` : '已识别文档形态，准备逐条提取…',
            );
          }
          if (evt.type === 'project_discovered' || evt.type === 'project_extract_started') {
            const name = String(evt.data.project_name || '');
            const idx = evt.data.index ?? evt.data.project_index;
            const total = evt.data.total ?? evt.data.project_total;
            setLlmProgressText(
              name && idx && total
                ? `正在提取项目「${name}」（${idx}/${total}）…`
                : name
                  ? `正在提取项目「${name}」…`
                  : '正在逐项目提取试验方法…',
            );
          }
          if (evt.type === 'method_extract_started') {
            const subName = String(evt.data.sub_method_name || evt.data.method_name || '');
            const idx = evt.data.index;
            const total = evt.data.total;
            setLlmProgressText(
              subName && idx && total
                ? `LLM 正在提取「${subName}」（${idx}/${total}）…`
                : subName
                  ? `LLM 正在提取「${subName}」…`
                  : 'LLM 正在提取下一条试验方法…',
            );
          }
          if (evt.type === 'method_ready') {
            const method = evt.data.method || evt.data;
            const methodIndex = Number(
              (evt.data as { method_index?: number }).method_index
              || (method as { method_index?: number }).method_index
              || 0,
            );
            methodsReceived += 1;
            setLlmMethodsReady(methodsReceived);
            setLlmProgressText(`已收到 ${methodsReceived} 个方法，继续提取中…`);
            onStreamMethod?.(method, {
              jobId: streamJobId || String((evt.data as { job_id?: string }).job_id || ''),
              methodIndex,
              documentDir: streamDocumentDir || String((method as { document_dir?: string }).document_dir || ''),
              sourceFileId: resolvedDocId ? `WJ_${resolvedDocId}` : undefined,
            });
          }
          if (evt.type === 'method_failed') {
            message.warning(String((evt.data as { error?: string }).error || '某条方法提取失败'));
          }
          if (evt.type === 'done') {
            streamJobId = String(evt.data.job_id || streamJobId);
            streamDocumentDir = String(evt.data.document_dir || streamDocumentDir);
            onStreamDone?.({
              jobId: streamJobId,
              documentDir: streamDocumentDir,
              methodsCount: Number(evt.data.methods_count || 0),
            });
          }
        },
      });
      hide();
      if ((result.methodsCount ?? 0) === 0 && methodsReceived === 0) {
        message.error('LLM 提取未返回方法');
        return;
      }
      handleClose();
    } catch (e: unknown) {
      hide();
      if ((e as Error)?.name === 'AbortError') {
        message.info('已取消 LLM 提取');
      } else {
        message.error(e instanceof Error ? e.message : 'LLM 提取失败，尝试阻塞模式…');
        try {
          const data = await extractMethodAuto({
            file: fileItem?.file?.size ? fileItem.file : null,
            objectName: libraryObjectName || undefined,
            userId: getUploadUserLabel(),
            skipRecall: false,
            sourceFileId: resolvedDocId ? `WJ_${resolvedDocId}` : undefined,
            useChromaSource: resolvedUseChroma,
          });
          if (data?.success && data.data?.methods?.length) {
            onComplete({
              methods: data.data.methods,
              methods_count: data.data.methods_count ?? data.data.methods.length,
              batch_count: 1,
              source_file_id: libraryDocId ? `WJ_${libraryDocId}` : undefined,
              object_name: libraryObjectName || undefined,
              graphCandidates: (fileItem?.graphCandidates || []) as GraphRecommendation[],
            });
            handleClose();
          }
        } catch {
          /* 降级也失败 */
        }
      }
    } finally {
      setLlmExtracting(false);
      setLlmProgressText('');
      streamAbortRef.current = null;
    }
  };

  const treeData = useMemo(() => fileItem?.tocTree || [], [fileItem?.tocTree]);

  useEffect(() => {
    if (!fileItem?.selectedSectionKey || activeSectionNo) return;
    const node = findTocNodeByKey(treeData, fileItem.selectedSectionKey);
    if (node?.sectionNo) {
      setActiveSectionNo(node.sectionNo);
    }
  }, [fileItem?.selectedSectionKey, treeData, activeSectionNo]);

  useEffect(() => {
    if (!activeSectionNo || !tocTreePanelRef.current) return;
    const node = findTocNodeBySectionNo(treeData, activeSectionNo);
    if (!node) return;
    window.setTimeout(() => {
      const panel = tocTreePanelRef.current;
      if (!panel) return;
      const selected = panel.querySelector('.ant-tree-node-selected .ant-tree-title') as HTMLElement | null;
      selected?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    }, 80);
  }, [activeSectionNo, treeData]);

  const handleReaderActiveSection = useCallback((sectionNo: string) => {
    if (tocClickSuppressRef.current) {
      tocClickSuppressRef.current = false;
      return;
    }
    const node = findTocNodeBySectionNo(treeData, sectionNo);
    if (!node) return;
    setActiveSectionNo(sectionNo);
    updateItem({
      selectedSectionKey: String(node.key),
      scrollSectionNo: sectionNo,
    });
  }, [treeData, updateItem]);

  const handleTocNavigate = useCallback((key: string, node?: TocTreeNode | null) => {
    if (!key || !node) return;
    tocClickSuppressRef.current = true;
    const sectionNo = node.sectionNo || '';
    setActiveSectionNo(sectionNo);
    updateItem({
      selectedSectionKey: key,
      scrollSectionNo: sectionNo,
    });
  }, [updateItem]);

  const handleTreeCheck = (keys: React.Key[] | { checked: React.Key[]; halfChecked: React.Key[] }) => {
    const list = Array.isArray(keys) ? keys : keys.checked;
    setCheckedTreeKeys(list.map(String));
  };

  const handleClearTreeSelection = () => {
    setCheckedTreeKeys([]);
  };

  const handleAddMethodDraft = () => {
    if (!checkedTreeKeys.length) {
      message.warning('请先在中间目录中勾选至少一个章节');
      return;
    }
    const draft = createMethodDraftFromSelection(checkedTreeKeys, treeData);
    if (!draft) {
      message.warning('无法根据所选章节创建方法，请重新选择');
      return;
    }
    const existingKeys = new Set(methodDrafts.flatMap((d) => [d.rootKey, ...d.childKeys]));
    if (checkedTreeKeys.some((k) => existingKeys.has(k))) {
      message.warning('所选章节与待提取列表中已有方法重叠，请先删除已有项或调整选择');
      return;
    }
    setMethodDrafts((prev) => [...prev, draft]);
    setCheckedTreeKeys([]);
    message.success('已将当前所选目录添加为 1 个方法');
  };

  const handleRemoveDraft = (id: string) => {
    setMethodDrafts((prev) => prev.filter((d) => d.id !== id));
    if (editingDraftId === id) {
      setEditingDraftId(null);
      setDraftChildKeys([]);
    }
  };

  const startEditDraftChildren = (draft: MethodExtractDraft) => {
    setEditingDraftId(draft.id);
    setDraftChildKeys([...draft.childKeys]);
    setCheckedTreeKeys((prev) => (prev.includes(draft.rootKey) ? prev : [...prev, draft.rootKey]));
  };

  const saveDraftChildren = () => {
    if (!editingDraftId) return;
    setMethodDrafts((prev) =>
      prev.map((d) => (d.id === editingDraftId ? { ...d, childKeys: draftChildKeys } : d)),
    );
    setEditingDraftId(null);
    setDraftChildKeys([]);
    message.success('已更新子节范围');
  };

  const runExtract = async (sectionsData: ReturnType<typeof draftsToSectionGroups>) => {
    if ((!fileItem && !libraryDocId) || !sectionsData.length) return null;
    setExtracting(true);
    const hide = message.loading(useLlmMerge ? '正在 LLM 理解结构并提取…' : '正在按章节提取方法…', 0);
    try {
      let data: { success?: boolean; data?: MethodExtractWizardResult; error?: string };
      if (libraryDocId) {
        data = await extractMethodFromSections({
          sourceFileId: `WJ_${libraryDocId}`,
          objectName: libraryObjectName,
          previewCacheKey: undefined,
          sections: sectionsData,
          userId: getUploadUserLabel(),
          useLlmMerge,
        });
      } else if (fileItem?.file?.size) {
        data = await extractMethodFromSectionsFile({
          file: fileItem.file,
          previewCacheKey: previewCacheKey || (fileItem.contentCategory === '_preview' ? fileItem.contentDocId : undefined),
          sections: sectionsData,
          userId: getUploadUserLabel(),
          useLlmMerge,
        });
      } else {
        message.error('缺少本地文件或库内文件引用');
        hide();
        return null;
      }
      if (!data?.success || !data.data) {
        hide();
        message.error(data?.error || '提取失败');
        return null;
      }
      hide();
      const debugDir = String((data.data as { debug_dir?: string }).debug_dir || '');
      if (debugDir) {
        console.info('[MethodExtract] 章节提取调试目录:', debugDir);
        message.info(`调试文件已保存，详见控制台路径`, 4);
      }
      return data.data;
    } catch (e: unknown) {
      hide();
      message.error(e instanceof Error ? e.message : '提取失败');
      return null;
    } finally {
      setExtracting(false);
    }
  };

  const editingDraft = editingDraftId ? methodDrafts.find((d) => d.id === editingDraftId) : undefined;
  const editingRootNode = editingDraft ? findTocNodeByKey(treeData, editingDraft.rootKey) : undefined;
  const childCandidateKeys = editingRootNode ? collectDescendantKeys(editingRootNode) : [];

  const duplicateAlertDescription = useMemo(() => {
    if (!duplicateInfo?.duplicate) return '';
    const names = (duplicateInfo.linked_methods || []).map((m) => m.name || m.id).filter(Boolean);
    const methodPart = names.length
      ? `库中关联试验方法：${names.join('、')}`
      : '库中尚无关联试验方法';
    const chromaPart = duplicateInfo.use_chroma_source
      ? '；PDF 将直接使用向量库已有文本进行 LLM 提取'
      : '';
    return `${methodPart}${chromaPart}。本次不会重复入库。`;
  }, [duplicateInfo]);

  const manualStepActive = stepIsExtract && extractMode === 'manual';
  const modalWidth = manualStepActive ? 1480 : (step >= contentStepIndex ? 960 : 960);

  const handleContentNext = useCallback(async () => {
    if (!fileItem) return;
    if (showMetadataStep) {
      setStep(metadataStepIndex);
      await loadMetadata(fileItem);
      return;
    }
    setStep(extractStepIndex);
  }, [fileItem, showMetadataStep, metadataStepIndex, extractStepIndex, loadMetadata]);

  useEffect(() => {
    if (!stepIsMetadata || !fileItem?.file?.size || metaExtracted || metaLoading) return;
    void loadMetadata(fileItem);
  }, [stepIsMetadata, fileItem, metaExtracted, metaLoading, loadMetadata]);

  return (
    <Modal
      title="从标准文件提取试验方法"
      open={open}
      onCancel={handleClose}
      width={modalWidth}
      footer={null}
      destroyOnHidden
      mask={{ closable: false }}
    >
      <Steps current={step} items={stepItems} size="small" style={{ marginBottom: 20 }} />

      {step === 0 && (
        <div>
          <div style={{ marginBottom: 16 }}>
            <Text strong style={{ display: 'block', marginBottom: 8 }}>提取模式</Text>
            <Radio.Group
              value={extractMode}
              onChange={(e) => setExtractMode(e.target.value as ExtractMode)}
            >
              <Radio.Button value="llm">LLM 智能提取</Radio.Button>
              <Radio.Button value="manual">手动选章提取</Radio.Button>
            </Radio.Group>
            <div style={{ marginTop: 8 }}>
              <Text type="secondary" style={{ fontSize: 12 }}>
                {extractMode === 'llm'
                  ? '自动发现试验项目/方法并结构化，支持 PDF/Word/Markdown/文本，结果进入审阅'
                  : '解析文档后自行勾选章节组成方法，支持 PDF/Word'}
              </Text>
            </div>
          </div>

          <div style={{ marginBottom: 16 }}>
            <Text strong style={{ display: 'block', marginBottom: 8 }}>文件来源</Text>
            <Radio.Group
              value={fileSource}
              onChange={(e) => {
                setFileSource(e.target.value as FileSource);
                setFileItem(null);
                setLibraryRecord(null);
                setLibraryDocId('');
                setLibraryObjectName('');
              }}
            >
              <Radio.Button value="upload">本地上传</Radio.Button>
              <Radio.Button value="library">从文件库选择</Radio.Button>
            </Radio.Group>
          </div>

          <div style={{ marginBottom: 16 }}>
            <Text strong style={{ display: 'block', marginBottom: 8 }}>文件类别</Text>
            <Select
              style={{ width: 280 }}
              value={category}
              onChange={setCategory}
              options={METHOD_EXTRACT_CATEGORY_OPTIONS.map((o) => ({ value: o.label, label: o.label }))}
            />
            <div style={{ marginTop: 6 }}>
              <Text type="secondary" style={{ fontSize: 12 }}>
                {fileSource === 'library' ? '按类别筛选文件库列表' : '入库时将保存到所选类别'}
              </Text>
            </div>
          </div>

          {fileSource === 'upload' ? (
            <>
              <div style={{ marginBottom: 12 }}>
                <Checkbox checked={saveToLibrary} onChange={(e) => setSaveToLibrary(e.target.checked)}>
                  加入文件库（推荐，便于后续再次提取与关联）
                </Checkbox>
              </div>
              <Dragger
                accept={extractMode === 'llm' ? LLM_ACCEPT : MANUAL_ACCEPT}
                maxCount={1}
                showUploadList={false}
                beforeUpload={(f) => handlePickFile(f as File)}
              >
                <p className="ant-upload-drag-icon">
                  <InboxOutlined />
                </p>
                <p className="ant-upload-text">
                  {extractMode === 'llm'
                    ? '点击或拖拽上传文档（PDF / Word / .md / .txt）'
                    : '点击或拖拽上传标准文件（PDF / Word）'}
                </p>
                <p className="ant-upload-hint">
                  {saveToLibrary
                    ? '上传后将自动提取元数据，确认后入库再进入提取'
                    : '不入库时仅用于本次提取，不会写入文件库'}
                </p>
              </Dragger>
            </>
          ) : (
            <WjglLibraryFilePicker
              category={category}
              selectedDocId={libraryRecord?.doc_id}
              acceptExtensions={acceptExtensions}
              onSelect={(record) => void loadLibraryFile(record)}
            />
          )}
          {libraryLoading ? (
            <div style={{ marginTop: 12, textAlign: 'center' }}>
              <Text type="secondary">正在加载文件库目录…</Text>
            </div>
          ) : null}
        </div>
      )}

      {stepIsContent && fileItem && (
        <div>
          <Alert
            type="info"
            showIcon
            style={{ marginBottom: 12 }}
            title="文档预览"
            description="请先确认解析后的正文与目录；元数据将在下一步（入库前）提取。"
          />
          <div style={{ marginBottom: 12 }}>
            <Text>已选文件：<Text strong>{fileItem.name}</Text></Text>
          </div>
          <MethodFileContentReview
            item={fileItem}
            onChange={(patch) => updateItem(patch)}
            onResumeMineru={() => void parseFile(fileItem, 'resume-mineru')}
            onForceReparse={() => void parseFile(fileItem, 'full')}
            contentMode={['md', 'txt'].includes((fileItem.name.split('.').pop() || '').toLowerCase()) ? 'auto' : 'html'}
          />
          <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 16 }}>
            <Button onClick={goBackToFileSelect}>重新选择</Button>
            <Button
              type="primary"
              disabled={fileItem.parseStatus === 'parsing' || (fileItem.fullLoading && !fileItem.fullHtml?.trim())}
              onClick={() => void handleContentNext()}
            >
              {showMetadataStep ? '下一步：元数据' : '下一步：提取方法'}
            </Button>
          </div>
        </div>
      )}

      {stepIsMetadata && fileItem && (
        <div>
          <Alert
            type="info"
            showIcon
            style={{ marginBottom: 12 }}
            title="元数据审核"
            description={`确认后将文件入库至「${category}」，再进入方法提取。`}
          />
          <div style={{ marginBottom: 12 }}>
            <Text>已选文件：<Text strong>{fileItem.name}</Text></Text>
          </div>
          {duplicateInfo?.duplicate ? (
            <Alert
              type="warning"
              showIcon
              style={{ marginBottom: 12 }}
              title={`文件已在库中（${duplicateInfo.file_name || duplicateInfo.doc_id}）`}
              description={duplicateAlertDescription}
            />
          ) : null}
          <MethodFileMetadataReview
            item={fileItem}
            onChange={(patch) => updateItem(patch)}
            metaLoading={metaLoading}
          />
          <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 16 }}>
            <Space>
              <Button onClick={() => setStep(contentStepIndex)}>上一步</Button>
              <Button onClick={goBackToFileSelect}>重新选择</Button>
            </Space>
            <Button type="primary" loading={savingLibrary || metaLoading} onClick={() => void handleMetadataConfirm()}>
              确认入库并继续
            </Button>
          </div>
        </div>
      )}

      {stepIsExtract && !fileItem && (
        <div style={{ textAlign: 'center', padding: 24 }}>
          <Spin description="正在进入提取步骤…" />
        </div>
      )}

      {stepIsExtract && fileItem && extractMode === 'llm' && (
        <div>
          <Alert
            type="info"
            showIcon
            style={{ marginBottom: 12 }}
            title="LLM 智能提取"
            description="系统将自动识别文档中的试验项目与方法，结构化后进入审阅面板（含全文溯源）。"
          />
          <div style={{ marginBottom: 16 }}>
            <Text>已选文件：<Text strong>{fileItem.name}</Text></Text>
          </div>
          {duplicateInfo?.duplicate ? (
            <Alert
              type="warning"
              showIcon
              style={{ marginBottom: 12 }}
              title={`文件已在库中（${duplicateInfo.file_name || duplicateInfo.doc_id}）`}
              description={duplicateAlertDescription}
            />
          ) : null}
          {llmProgressText || llmMethodsReady > 0 ? (
            <div style={{ marginBottom: 16 }}>
              <Progress
                percent={llmExtracting ? undefined : 100}
                status={llmExtracting ? 'active' : 'success'}
              />
              <Text type="secondary">
                {llmProgressText || (llmMethodsReady > 0 ? `已收到 ${llmMethodsReady} 个方法 Tab` : '')}
              </Text>
            </div>
          ) : null}
          {(fileItem.fullHtml || fileItem.fullMarkdown || fileItem.parseStatus === 'parsing') ? (
            <div style={{ marginBottom: 16 }}>
              <MethodFileContentReview
                item={fileItem}
                onChange={(patch) => updateItem(patch)}
                onResumeMineru={() => void parseFile(fileItem, 'resume-mineru')}
                onForceReparse={() => void parseFile(fileItem, 'full')}
                contentMode={['md', 'txt'].includes((fileItem.name.split('.').pop() || '').toLowerCase()) ? 'auto' : 'html'}
              />
            </div>
          ) : null}
          <div style={{ display: 'flex', justifyContent: 'space-between' }}>
            <Button onClick={goBackToFileSelect}>重新选择文件</Button>
            <Button
              type="primary"
              loading={llmExtracting}
              disabled={fileItem.parseStatus === 'error'}
              onClick={() => void handleLlmExtract()}
            >
              开始 LLM 智能提取
            </Button>
          </div>
        </div>
      )}

      {stepIsExtract && fileItem && extractMode === 'manual' && (
        <div>
          <Alert
            type="info"
            showIcon
            style={{ marginBottom: 12 }}
            title="手动选章提取"
            description="左侧阅读文档，中间勾选目录章节，右侧将所选合并为方法；可添加多个方法后一次性确定提取。"
          />
          <div
            style={{
              marginBottom: 12,
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
              flexWrap: 'wrap',
              gap: 8,
            }}
          >
            <Text>
              已选文件：
              <Text strong>{fileItem.name}</Text>
              {libraryDocId ? (
                <Text type="secondary" style={{ marginLeft: 8 }}>（库内 {libraryDocId}）</Text>
              ) : null}
            </Text>
            {fileSource === 'upload' && !libraryDocId ? (
              <Checkbox checked={saveToLibrary} onChange={(e) => setSaveToLibrary(e.target.checked)}>
                加入文件库（推荐）
              </Checkbox>
            ) : null}
          </div>
          {duplicateInfo?.duplicate ? (
            <Alert
              type="warning"
              showIcon
              style={{ marginBottom: 12 }}
              title={`文件已在库中（${duplicateInfo.file_name || duplicateInfo.doc_id}）`}
              description={duplicateAlertDescription}
            />
          ) : null}

          <Row gutter={12} style={{ marginBottom: 12 }}>
            <Col span={10}>
              <div style={{ border: '1px solid #f0f0f0', borderRadius: 6, padding: 8, height: 560, overflow: 'hidden' }}>
                <div style={{ fontWeight: 600, marginBottom: 8 }}>阅读器</div>
                <MethodFileContentReview
                  item={fileItem}
                  onChange={(patch) => updateItem(patch)}
                  onResumeMineru={() => void parseFile(fileItem, 'resume-mineru')}
                  onForceReparse={() => void parseFile(fileItem, 'full')}
                  layout="reader-only"
                  editorHeight={500}
                  defaultMode="preview"
                  showModeSwitch={false}
                  activeSectionNo={activeSectionNo}
                  onActiveSectionNo={handleReaderActiveSection}
                />
              </div>
            </Col>

            <Col span={7}>
              <div
                ref={tocTreePanelRef}
                style={{ border: '1px solid #f0f0f0', borderRadius: 6, padding: 8, height: 560, overflow: 'auto' }}
              >
                <div style={{ fontWeight: 600, marginBottom: 8 }}>
                  <FileSearchOutlined /> 目录
                  {treeData.length > 0 ? (
                    <Text type="secondary" style={{ fontWeight: 400, marginLeft: 8 }}>({treeData.length} 项)</Text>
                  ) : null}
                </div>
                <Text type="secondary" style={{ fontSize: 12, display: 'block', marginBottom: 8 }}>
                  已勾选 {checkedTreeKeys.length} 个章节；点击目录可定位正文，滚动正文将同步高亮目录
                </Text>
                {!treeData.length ? (
                  <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="目录加载中…" />
                ) : (
                  <Tree
                    checkable
                    selectable
                    treeData={treeData}
                    checkedKeys={checkedTreeKeys}
                    selectedKeys={fileItem.selectedSectionKey ? [fileItem.selectedSectionKey] : []}
                    onCheck={handleTreeCheck}
                    onSelect={(keys, info) => {
                      const key = String(Array.isArray(keys) ? keys[0] : keys);
                      if (!key) return;
                      handleTocNavigate(key, (info.node as TocTreeNode) || null);
                    }}
                    titleRender={(node) => {
                      const n = node as TocTreeNode;
                      const active = normalizeSectionNo(n.sectionNo || '') === normalizeSectionNo(activeSectionNo);
                      return (
                        <span
                          style={active ? {
                            background: '#fffbe6',
                            padding: '2px 6px',
                            borderRadius: 4,
                            boxShadow: 'inset 0 0 0 1px #ffe58f',
                          } : undefined}
                        >
                          {String(n.title ?? '')}
                        </span>
                      );
                    }}
                    defaultExpandAll
                  />
                )}
              </div>
            </Col>

            <Col span={7}>
              <div
                style={{
                  border: '1px solid #f0f0f0',
                  borderRadius: 6,
                  padding: 8,
                  height: 560,
                  display: 'flex',
                  flexDirection: 'column',
                }}
              >
                <div
                  style={{
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'center',
                    marginBottom: 8,
                    gap: 8,
                    flexWrap: 'wrap',
                  }}
                >
                  <div style={{ fontWeight: 600 }}>
                    已筛选方法（{methodDrafts.length}）
                  </div>
                  <Space size={4}>
                    <Button
                      size="small"
                      icon={<ClearOutlined />}
                      disabled={checkedTreeKeys.length === 0}
                      onClick={handleClearTreeSelection}
                    >
                      清除选择
                    </Button>
                    <Button type="primary" size="small" icon={<PlusOutlined />} onClick={handleAddMethodDraft}>
                      添加为一个方法
                    </Button>
                  </Space>
                </div>

                <div style={{ flex: 1, overflow: 'auto' }}>
                  {methodDrafts.length === 0 ? (
                    <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="从目录勾选章节后点「添加为一个方法」" />
                  ) : (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                      {methodDrafts.map((draft, index) => (
                        <div
                          key={draft.id}
                          style={{
                            display: 'flex',
                            justifyContent: 'space-between',
                            alignItems: 'flex-start',
                            gap: 8,
                            padding: '8px 0',
                            borderBottom: '1px solid #f0f0f0',
                          }}
                        >
                          <div>
                            <Text strong>方法 {index + 1}</Text>
                            <div style={{ fontSize: 13, marginTop: 4 }}>
                              {draft.rootSectionNo ? `${draft.rootSectionNo} ` : ''}{draft.rootTitle}
                            </div>
                            {draft.childKeys.length > 0 ? (
                              <Text type="secondary" style={{ fontSize: 12 }}>
                                共 {1 + draft.childKeys.length} 个章节
                              </Text>
                            ) : (
                              <Text type="secondary" style={{ fontSize: 12 }}>单章节</Text>
                            )}
                          </div>
                          <Space size={0}>
                            <Button type="link" size="small" onClick={() => startEditDraftChildren(draft)}>
                              子节
                            </Button>
                            <Button
                              type="link"
                              danger
                              size="small"
                              icon={<DeleteOutlined />}
                              onClick={() => handleRemoveDraft(draft.id)}
                            />
                          </Space>
                        </div>
                      ))}
                    </div>
                  )}

                  {editingDraft && childCandidateKeys.length > 0 ? (
                    <div style={{ marginTop: 12, padding: 8, background: '#fafafa', borderRadius: 6 }}>
                      <Text strong style={{ fontSize: 13 }}>选择要纳入的子节（可选）</Text>
                      <div style={{ maxHeight: 160, overflow: 'auto', marginTop: 8 }}>
                        <Checkbox.Group
                          value={draftChildKeys}
                          onChange={(vals) => setDraftChildKeys(vals as string[])}
                          style={{ display: 'flex', flexDirection: 'column', gap: 4 }}
                        >
                          {childCandidateKeys.map((k) => {
                            const n = findTocNodeByKey(treeData, k);
                            const label = n ? `${n.sectionNo || ''} ${String(n.rawTitle || n.title || '')}`.trim() : k;
                            return (
                              <Checkbox key={k} value={k}>
                                {label}
                              </Checkbox>
                            );
                          })}
                        </Checkbox.Group>
                      </div>
                      <Space style={{ marginTop: 8 }}>
                        <Button size="small" type="primary" onClick={saveDraftChildren}>确定</Button>
                        <Button size="small" onClick={() => { setEditingDraftId(null); setDraftChildKeys([]); }}>取消</Button>
                      </Space>
                    </div>
                  ) : null}
                </div>
              </div>
            </Col>
          </Row>

          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <Button onClick={goBackToFileSelect}>重新选择文件</Button>
            <Button
              type="primary"
              loading={extracting || savingLibrary}
              disabled={fileItem.parseStatus !== 'ready' || methodDrafts.length === 0}
              onClick={() => void handleConfirmExtract()}
            >
              确定提取
              {methodDrafts.length > 0 ? `（${methodDrafts.length} 个方法）` : ''}
            </Button>
          </div>
        </div>
      )}
    </Modal>
  );
}
