'use client';

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Modal, Upload, Steps, Button, Space, Switch, Select, Alert, List, Row, Col, message,
} from 'antd';
import {
  CloudUploadOutlined, LinkOutlined,
  CheckCircleOutlined, LoadingOutlined, CloseCircleOutlined,
} from '@ant-design/icons';
import UploadFileReview from './UploadFileReview';
import type { EquipmentTab } from './semanticTypes';
import { tabToConfirmLinks } from './semanticTypes';
import { equipmentTabsHasLinks } from './DeviceLinkTabs';
// 上传预览：快速目录 + 全文（正式入库后由 MinerU 落盘 MD/HTML）
import { buildTocTreeNodes, enrichMetadataFromText, fetchFullDocumentHtmlUpload, fetchMineruPreviewStatus, fetchPreviewGraphLinks, firstTocNodeKey, formatMineruDoneMessage, formatMineruPollMessage, isMineruPollTimeout, loadMineruPreviewResult, parseJsonResponse, pollMineruPreviewHtml, tocTreeNodesToItems, type MineruPreviewResult, type TocItem, type TocTreeNode } from './documentTocUtils';
import { commitWjglFileUpload, getWjglUploadUserLabel, metaFromAutoParseData } from '../utils/wjglUploadPipeline';
import { SCHEME_KM_SOURCE_LABELS } from '../constants/schemeSourceLabels';
import { wjglUploadUrl } from '../utils/wjglApi';

const { Option } = Select;

export interface GraphCandidate {
  slot: string;
  node_type: string;
  node_id: string;
  node_name: string;
  score: number;
  status: 'recommended' | 'tentative' | 'low';
  suggested_rel?: string;
}

export interface UploadFileItem {
  uid: string;
  file: File;
  name: string;
  parseStatus: 'pending' | 'parsing' | 'ready' | 'error';
  parseError?: string;
  uploadStatus: 'local' | 'uploading' | 'uploaded' | 'confirmed';
  doc_id?: string;
  syncChroma: boolean;
  meta: {
    title: string;
    std_code: string;
    std_name: string;
    publish_date: string;
    issuer: string;
    abstract: string;
    keywords: string[];
    file_type: string;
  };
  tocTree: TocTreeNode[];
  /** 与 tocTree 对应的原始目录结构，入库时写入元数据 */
  documentToc?: TocItem[];
  selectedSectionKey: string;
  scrollSectionNo: string;
  fullHtml: string;
  fullMarkdown?: string;
  contentDocId?: string;
  contentCategory?: string;
  /** MinerU 预览任务 key（文件内容哈希），用于续轮询 */
  mineruKey?: string;
  fullLoading: boolean;
  contentError?: string;
  mineruLoading?: boolean;
  mineruStatus?: string;
  parseWarnings?: string[];
  graphWarnings?: string[];
  /** FLGL 目录来源（parse_sections / heading_extract 等），用于 MinerU 不覆盖 */
  tocSource?: string;
  tocRoutingRev?: string;
  tocLocked?: boolean;
  graphCandidates: GraphCandidate[];
  equipmentTabs: EquipmentTab[];
}

function parseStatusIcon(status: UploadFileItem['parseStatus']) {
  if (status === 'ready') return <CheckCircleOutlined style={{ color: '#52c41a' }} />;
  if (status === 'parsing' || status === 'pending') return <LoadingOutlined style={{ color: '#1677ff' }} />;
  if (status === 'error') return <CloseCircleOutlined style={{ color: '#ff4d4f' }} />;
  return null;
}

interface UploadModalProps {
  open: boolean;
  onClose: () => void;
  category: string;
  onComplete?: () => void;
}

export default function UploadModal({ open, onClose, category, onComplete }: UploadModalProps) {
  const [items, setItems] = useState<UploadFileItem[]>([]);
  const [activeTab, setActiveTab] = useState('');
  const [allSyncChroma, setAllSyncChroma] = useState(false);
  const [duplicateAction, setDuplicateAction] = useState<'replace' | 'skip' | 'check'>('replace');
  const [submitting, setSubmitting] = useState(false);

  const reset = () => {
    setItems([]);
    setActiveTab('');
    setAllSyncChroma(false);
  };

  const handleClose = () => {
    reset();
    onClose();
  };

  const updateItem = useCallback((uid: string, patch: Partial<UploadFileItem>) => {
    setItems(prev => prev.map(it => (it.uid === uid ? { ...it, ...patch } : it)));
  }, []);

  const applyMineruResult = useCallback((uid: string, key: string, result: MineruPreviewResult) => {
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
    if (result.toc?.length) {
      const nextToc = buildTocTreeNodes(result.toc);
      patch.tocTree = nextToc;
      patch.documentToc = result.toc as TocItem[];
      patch.selectedSectionKey = firstTocNodeKey(nextToc);
      patch.scrollSectionNo = nextToc[0]?.sectionNo || '';
    }
    updateItem(uid, patch);
  }, [updateItem]);

  const resumeMineruPoll = useCallback(async (entry: UploadFileItem) => {
    const key = (entry.mineruKey || entry.contentDocId || '').trim();
    if (!key) {
      message.warning('无 MinerU 任务可继续，请重新上传');
      return;
    }
    updateItem(entry.uid, {
      mineruLoading: true,
      mineruStatus: '正在恢复 MinerU 轮询…',
      contentError: undefined,
    });
    const onMineruProgress = (task: Record<string, unknown>) => {
      updateItem(entry.uid, {
        mineruLoading: true,
        mineruStatus: formatMineruPollMessage(task),
      });
    };
    try {
      const status = await fetchMineruPreviewStatus(key);
      if (status.status === 'done') {
        applyMineruResult(entry.uid, key, await loadMineruPreviewResult(key));
        return;
      }
      if (status.status === 'error') {
        throw new Error(String(status.error || 'MinerU 解析失败'));
      }
      const result = await pollMineruPreviewHtml(key, { onProgress: onMineruProgress });
      applyMineruResult(entry.uid, key, result);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : 'MinerU 轮询失败';
      updateItem(entry.uid, {
        mineruLoading: false,
        mineruStatus: msg,
        ...(isMineruPollTimeout(e) ? { contentError: msg } : {}),
      });
    }
  }, [updateItem, applyMineruResult]);

  const parseItem = useCallback(async (entry: UploadFileItem, mode: 'full' | 'resume-mineru' = 'full') => {
    if (mode === 'resume-mineru') {
      await resumeMineruPoll(entry);
      return;
    }
    updateItem(entry.uid, {
      parseStatus: 'parsing',
      parseError: undefined,
      contentError: undefined,
      fullLoading: true,
      fullHtml: '',
      fullMarkdown: '',
      contentDocId: undefined,
      contentCategory: undefined,
      mineruKey: undefined,
      mineruLoading: false,
      mineruStatus: undefined,
    });
    try {
      const metaFd = new FormData();
      metaFd.append('file', entry.file);

      const metaRes = await fetch(wjglUploadUrl('/auto-parse'), { method: 'POST', body: metaFd });
      const metaJson = await parseJsonResponse(metaRes);

      if (!metaJson.success) throw new Error(String(metaJson.error || '元数据提取失败'));

      const m = metaJson.data as Record<string, unknown>;
      const meta = metaFromAutoParseData(m, entry.name);

      // 元数据先展示，正文/关联在后台加载，避免 MinerU 轮询期间界面空白
      updateItem(entry.uid, {
        parseStatus: 'ready',
        meta,
        parseWarnings: Array.isArray(m.warnings) ? (m.warnings as string[]) : [],
        tocTree: [],
        graphCandidates: [],
        equipmentTabs: [],
        selectedSectionKey: '',
        scrollSectionNo: '',
        fullHtml: '',
        fullLoading: true,
      });

      const graphPromise = fetchPreviewGraphLinks(meta).catch(() => ({ success: false, data: { candidates: [] } }));

      const contentPromise = (async () => {
        const { html: initialHtml, mineruKey, mineruOnly } = await fetchFullDocumentHtmlUpload(entry.file);
        let tocTree: TocTreeNode[] = [];
        let documentToc: TocItem[] = [];
        if (!mineruOnly) {
          const tocFd = new FormData();
          tocFd.append('file', entry.file);
          const tocRes = await fetch(wjglUploadUrl('/document-toc-upload'), { method: 'POST', body: tocFd });
          const tocJson = await parseJsonResponse(tocRes);
          if (tocJson.success && (tocJson.data as { toc?: TocItem[] })?.toc?.length) {
            documentToc = (tocJson.data as { toc: TocItem[] }).toc;
            tocTree = buildTocTreeNodes(documentToc);
          }
        }
        const firstKey = firstTocNodeKey(tocTree);
        const firstNode = tocTree.length ? tocTree[0] : null;
        const onMineruProgress = (task: Record<string, unknown>) => {
          updateItem(entry.uid, {
            mineruLoading: true,
            mineruStatus: formatMineruPollMessage(task),
          });
        };

        let fullHtml = initialHtml;
        let fullMarkdown = '';
        let contentDocId = mineruKey;
        let contentCategory = mineruKey ? '_preview' : undefined;
        let storedMineruKey = mineruKey;
        let nextMeta = meta;
        let parseWarnings = Array.isArray(m.warnings) ? [...m.warnings] : [];
        let mineruLoading = Boolean(mineruKey);
        let mineruStatus = mineruKey
          ? (mineruOnly ? 'MinerU 解析中（未使用 fitz 预览）…' : 'MinerU 后台解析中，完成后自动替换正文…')
          : undefined;
        let selectedSectionKey = firstKey;
        let scrollSectionNo = firstNode?.sectionNo || '';

        if (mineruKey) {
          if (mineruOnly) {
            try {
              const result = await pollMineruPreviewHtml(mineruKey, { onProgress: onMineruProgress });
              fullHtml = result.html;
              fullMarkdown = result.markdown || '';
              contentDocId = mineruKey;
              contentCategory = '_preview';
              storedMineruKey = mineruKey;
              if (result.toc?.length) {
                documentToc = result.toc as TocItem[];
                tocTree = buildTocTreeNodes(documentToc);
                selectedSectionKey = firstTocNodeKey(tocTree);
                scrollSectionNo = tocTree[0]?.sectionNo || '';
              }
              mineruLoading = false;
              mineruStatus = formatMineruDoneMessage({ mdPath: result.mdPath, mineruCached: result.mineruCached });
            } catch (pollErr: unknown) {
              const msg = pollErr instanceof Error ? pollErr.message : 'MinerU 解析失败';
              mineruLoading = false;
              mineruStatus = msg;
              throw new Error(msg);
            }
          } else {
            void pollMineruPreviewHtml(mineruKey, { onProgress: onMineruProgress })
              .then((result) => {
                applyMineruResult(entry.uid, mineruKey, result);
              })
              .catch((err: unknown) => {
                const msg = err instanceof Error ? err.message : 'MinerU 解析失败';
                updateItem(entry.uid, {
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

        if (fullMarkdown.trim()) {
          try {
            const enriched = await enrichMetadataFromText(fullMarkdown.slice(0, 8000), nextMeta);
            nextMeta = {
              ...nextMeta,
              std_name: enriched.std_name || nextMeta.std_name,
              std_code: enriched.std_code || nextMeta.std_code,
              publish_date: enriched.publish_date || nextMeta.publish_date,
              issuer: enriched.issuer || nextMeta.issuer,
              abstract: enriched.abstract || nextMeta.abstract,
              keywords: enriched.keywords?.length ? enriched.keywords : nextMeta.keywords,
              title: enriched.std_name || nextMeta.title,
            };
            if (Array.isArray(enriched.warnings)) {
              parseWarnings = [...parseWarnings, ...enriched.warnings];
            }
          } catch (err: unknown) {
            parseWarnings = [
              ...parseWarnings,
              err instanceof Error ? err.message : 'MinerU 正文元数据补全失败',
            ];
          }
        }

        return {
          tocTree,
          documentToc: documentToc.length ? documentToc : tocTreeNodesToItems(tocTree),
          fullHtml,
          fullMarkdown,
          contentDocId,
          contentCategory,
          mineruKey: storedMineruKey,
          meta: nextMeta,
          parseWarnings,
          selectedSectionKey,
          scrollSectionNo,
          fullLoading: false,
          mineruLoading,
          mineruStatus,
        };
      })();

      const [graphJson, contentPatch] = await Promise.all([graphPromise, contentPromise]);
      let graphCandidates: GraphCandidate[] = graphJson.success ? ((graphJson.data as { candidates?: GraphCandidate[] })?.candidates || []) : [];
      let graphWarnings: string[] = graphJson.success ? ((graphJson.data as { warnings?: string[] })?.warnings || []) : [];
      const finalMeta = contentPatch.meta || meta;
      if (finalMeta) {
        const refreshed = await fetchPreviewGraphLinks(finalMeta).catch(() => null);
        if (refreshed?.success) {
          graphCandidates = (refreshed.data as { candidates?: GraphCandidate[] })?.candidates || [];
          graphWarnings = (refreshed.data as { warnings?: string[] })?.warnings || graphWarnings;
        }
      }
      updateItem(entry.uid, { graphCandidates, graphWarnings, ...contentPatch });
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : '解析失败';
      setItems(prev => prev.map(it => {
        if (it.uid !== entry.uid) return it;
        const hasMeta = Boolean(it.meta?.std_code || it.meta?.std_name);
        if (hasMeta) {
          return { ...it, parseStatus: 'ready', contentError: msg, fullLoading: false };
        }
        return { ...it, parseStatus: 'error', parseError: msg, fullLoading: false };
      }));
    }
  }, [updateItem, resumeMineruPoll, applyMineruResult]);

  const handleFileSelect = (file: File) => {
    const uid = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    const entry: UploadFileItem = {
      uid,
      file,
      name: file.name,
      parseStatus: 'pending',
      uploadStatus: 'local',
      syncChroma: allSyncChroma,
      meta: {
        title: file.name.replace(/\.[^/.]+$/, ''),
        std_code: '', std_name: '', publish_date: '', issuer: '',
        abstract: '', keywords: [], file_type: file.name.split('.').pop() || '',
      },
      tocTree: [],
      selectedSectionKey: '',
      scrollSectionNo: '',
      fullHtml: '',
      fullLoading: false,
      graphCandidates: [],
      equipmentTabs: [],
    };
    setItems(prev => {
      const next = [...prev, entry];
      if (!activeTab) setActiveTab(uid);
      return next;
    });
    void parseItem(entry);
    return false;
  };

  const phase = useMemo(() => {
    if (items.length === 0) return 'pick';
    if (items.some(it => it.parseStatus === 'pending' || it.parseStatus === 'parsing')) return 'parsing';
    if (submitting) return 'submitting';
    if (items.every(it => it.parseStatus === 'ready')) return 'review';
    return 'parsing';
  }, [items, submitting]);

  const handleSubmit = async () => {
    if (!items.length || phase !== 'review') {
      message.warning('请等待解析完成');
      return;
    }
    setSubmitting(true);
    try {
      for (const item of items) {
        if (item.uploadStatus === 'confirmed') continue;
        updateItem(item.uid, { uploadStatus: 'uploading' });

        const { doc_id: docId } = await commitWjglFileUpload({
          file: item.file,
          uid: item.uid,
          meta: item.meta,
          category,
          syncChroma: item.syncChroma,
          fileLabel: category === '试验方案' ? SCHEME_KM_SOURCE_LABELS.LOCAL_UPLOAD : undefined,
          duplicateAction,
          documentToc: item.documentToc?.length
            ? item.documentToc
            : (item.tocTree?.length ? tocTreeNodesToItems(item.tocTree) : undefined),
          documentContent: {
            html: item.fullHtml,
            markdown: item.fullMarkdown,
            mineruKey: item.mineruKey || item.contentDocId,
          },
        });

        const hasLinks = equipmentTabsHasLinks(item.equipmentTabs);
        if (hasLinks) {
          await fetch(`/api/wjgl1/files/${encodeURIComponent(docId)}/graph-link-confirm`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              category,
              equipment_tabs: item.equipmentTabs.map(t => ({
                equipment_id: t.equipment_id,
                equipment_name: t.equipment_name,
                links: tabToConfirmLinks(t),
              })),
            }),
          });
        }

        updateItem(item.uid, { uploadStatus: 'confirmed', doc_id: docId });
      }
      message.success(`已成功入库 ${items.length} 个文件`);
      onComplete?.();
      handleClose();
    } catch (e: unknown) {
      message.error(e instanceof Error ? e.message : '入库失败');
    } finally {
      setSubmitting(false);
    }
  };

  useEffect(() => {
    if (!open) reset();
  }, [open]);

  const activeItem = items.find(it => it.uid === activeTab);

  return (
    <Modal
      title="上传文件"
      open={open}
      onCancel={handleClose}
      width={1280}
      destroyOnClose
      footer={null}
      styles={{ body: { maxHeight: '82vh', overflowY: 'auto' } }}
    >
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 12 }}
        message="选择文件 → 元数据解析 → 推荐关联节点 → 入库"
        description="WJGL_UPLOAD_PREVIEW=mineru 时仅用 MinerU 解析正文；请先运行 start_mineru_api.ps1 并设置 MINERU_API_URL 加速。"
      />
      <Steps
        size="small"
        current={phase === 'pick' ? 0 : phase === 'parsing' ? 1 : phase === 'review' ? 2 : 3}
        style={{ marginBottom: 16 }}
        items={[{ title: '选择' }, { title: '解析' }, { title: '审核' }, { title: '入库' }]}
      />

      {phase === 'pick' ? (
        <Upload.Dragger multiple beforeUpload={handleFileSelect} showUploadList={false}
          accept=".pdf,.doc,.docx,.ppt,.pptx,.xls,.xlsx,.wps,.et,.dps">
          <p><CloudUploadOutlined style={{ fontSize: 28 }} /></p>
          <p>点击或拖拽文件（支持批量）</p>
        </Upload.Dragger>
      ) : (
        <>
          <Row gutter={16}>
            <Col span={6}>
              <List
                size="small"
                dataSource={items}
                renderItem={it => (
                  <List.Item
                    style={{ cursor: 'pointer', background: activeTab === it.uid ? '#e6f4ff' : undefined, padding: 6, borderRadius: 4 }}
                    onClick={() => setActiveTab(it.uid)}
                  >
                    {parseStatusIcon(it.parseStatus)} {it.name}
                  </List.Item>
                )}
              />
              <div style={{ marginTop: 8, fontSize: 12 }}>
                同步向量库 <Switch size="small" checked={allSyncChroma} onChange={v => {
                  setAllSyncChroma(v);
                  setItems(prev => prev.map(it => ({ ...it, syncChroma: v })));
                }} />
                <Select size="small" value={duplicateAction} onChange={setDuplicateAction} style={{ width: 88, marginLeft: 8 }}>
                  <Option value="replace">替换</Option>
                  <Option value="skip">跳过</Option>
                </Select>
              </div>
            </Col>
            <Col span={18}>
              {activeItem && (
                <UploadFileReview
                  item={activeItem}
                  onChange={patch => updateItem(activeItem.uid, patch)}
                  onResumeMineru={() => void parseItem(activeItem, 'resume-mineru')}
                  onForceReparse={() => void parseItem(activeItem, 'full')}
                />
              )}
            </Col>
          </Row>
          <div style={{ marginTop: 16, textAlign: 'right' }}>
            <Space>
              <Button onClick={handleClose}>取消</Button>
              <Button type="primary" icon={<LinkOutlined />} loading={submitting} disabled={phase !== 'review'} onClick={() => void handleSubmit()}>
                确认上传并入库
              </Button>
            </Space>
          </div>
        </>
      )}
    </Modal>
  );
}
