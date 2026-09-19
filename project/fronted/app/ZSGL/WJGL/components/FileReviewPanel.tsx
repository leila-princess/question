'use client';

import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  Button, Card, Col, Form, Input, Row, Space, Spin, message, Progress,
} from 'antd';
import { ReloadOutlined, SaveOutlined, LinkOutlined } from '@ant-design/icons';
import DocumentTocTree from './DocumentTocTree';
import FullDocumentEditor from './FullDocumentEditor';
import MathAwareTextArea from '@/components/MathAwareTextArea';
import DeviceLinkTabs from './DeviceLinkTabs';
import type { EquipmentTab, GraphRecommendation } from './semanticTypes';
import { tabToConfirmLinks } from './semanticTypes';
import { equipmentTabsHasLinks } from './DeviceLinkTabs';
import {
  buildTocTreeNodes,
  loadLibraryDocumentContent,
  findTocNode,
  findTocNodeBySectionNo,
  tocNodeTitle,
  tocTreeNodesToItems,
  type TocItem,
  type TocTreeNode,
} from './documentTocUtils';

function formatFileSize(bytes: number): string {
  if (!bytes || bytes <= 0) return '-';
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export interface FileReviewPanelProps {
  docId: string;
  category: string;
  initialScrollSectionNo?: string;
  showProgress?: boolean;
  autoAnalyze?: boolean;
  onConfirmed?: () => void;
  onFileLoaded?: (file: Record<string, unknown>) => void;
}

export default function FileReviewPanel({
  docId,
  category,
  initialScrollSectionNo = '',
  showProgress = true,
  autoAnalyze = true,
  onConfirmed,
  onFileLoaded,
}: FileReviewPanelProps) {
  const [form] = Form.useForm();
  const [loading, setLoading] = useState(true);
  const [analyzing, setAnalyzing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [file, setFile] = useState<Record<string, unknown> | null>(null);
  const [graphLinkStatus, setGraphLinkStatus] = useState('none');
  const [recommendations, setRecommendations] = useState<GraphRecommendation[]>([]);
  const [equipmentTabs, setEquipmentTabs] = useState<EquipmentTab[]>([]);

  const [tocTree, setTocTree] = useState<TocTreeNode[]>([]);
  const [documentToc, setDocumentToc] = useState<TocItem[]>([]);
  const [tocLoading, setTocLoading] = useState(false);
  const [docProgress, setDocProgress] = useState('');
  const [fullHtml, setFullHtml] = useState('');
  const [fullMarkdown, setFullMarkdown] = useState('');
  const [fullLoading, setFullLoading] = useState(false);
  const [selectedSectionKey, setSelectedSectionKey] = useState('');
  const [scrollSectionNo, setScrollSectionNo] = useState('');

  const loadedRef = useRef('');
  const analyzeStartedRef = useRef(false);

  const applyFileToForm = useCallback((f: Record<string, unknown>) => {
    form.setFieldsValue({
      std_name: f.std_name || f.title,
      std_code: f.std_code,
      publish_date: f.publish_date,
      issuer: f.issuer,
      abstract: f.abstract,
      keywords: Array.isArray(f.keywords) ? (f.keywords as string[]).join(', ') : '',
    });
  }, [form]);

  const loadDocument = useCallback(async (objectName: string, savedToc?: TocItem[]) => {
    setTocLoading(true);
    setFullLoading(true);
    setDocProgress('正在加载文档…');
    try {
      const loaded = await loadLibraryDocumentContent({
        docId,
        category,
        objectName,
        onProgress: setDocProgress,
      });
      const toc = savedToc?.length ? savedToc : loaded.toc;
      setDocumentToc(toc);
      setTocTree(buildTocTreeNodes(toc));
      setFullMarkdown(loaded.markdown);
      setFullHtml(loaded.html);
      setDocProgress('');
    } catch (e: unknown) {
      message.warning(e instanceof Error ? e.message : '文档内容加载失败');
      setTocTree([]);
      setFullHtml('');
      setFullMarkdown('');
    } finally {
      setTocLoading(false);
      setFullLoading(false);
      setDocProgress('');
    }
  }, [docId, category]);

  const initData = useCallback(async () => {
    if (!docId) return;
    const loadKey = `${docId}:${category}`;
    if (loadedRef.current === loadKey) return;
    loadedRef.current = loadKey;

    setLoading(true);
    try {
      const reviewRes = await fetch(
        `/api/wjgl1/files/${encodeURIComponent(docId)}/review?category=${encodeURIComponent(category)}`
      );
      const reviewJson = await reviewRes.json();

      if (!reviewJson.success) {
        message.error(reviewJson.error || '加载失败');
        return;
      }

      const f = reviewJson.data.file as Record<string, unknown>;
      setFile(f);
      onFileLoaded?.(f);
      setGraphLinkStatus(reviewJson.data.graph_link_status || 'none');
      setRecommendations(Array.isArray(reviewJson.data.graph_links?.candidates) ? reviewJson.data.graph_links.candidates : []);
      applyFileToForm(f);

      const reviewToc = Array.isArray(reviewJson.data.document_toc)
        ? (reviewJson.data.document_toc as TocItem[])
        : [];
      if (reviewToc.length) {
        setDocumentToc(reviewToc);
        setTocTree(buildTocTreeNodes(reviewToc));
      }

      void loadDocument(String(f.object_name || ''), reviewToc);

      if (autoAnalyze && reviewJson.data.graph_link_status === 'none' && !analyzeStartedRef.current) {
        analyzeStartedRef.current = true;
        void (async () => {
          setAnalyzing(true);
          try {
            const res = await fetch(
              `/api/wjgl1/files/${encodeURIComponent(docId)}/graph-link-analyze`,
              { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ category }) }
            );
            const data = await res.json();
            if (data.success) {
              setGraphLinkStatus('done');
              setRecommendations(data.data.graph_links?.candidates || []);
            }
          } finally {
            setAnalyzing(false);
          }
        })();
      }
    } catch (e: unknown) {
      message.error(e instanceof Error ? e.message : '加载失败');
      loadedRef.current = '';
    } finally {
      setLoading(false);
    }
  }, [docId, category, applyFileToForm, autoAnalyze, onFileLoaded, loadDocument]);

  useEffect(() => {
    loadedRef.current = '';
    analyzeStartedRef.current = false;
    setScrollSectionNo(initialScrollSectionNo || '');
    void initData();
  }, [docId, category, initialScrollSectionNo]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!initialScrollSectionNo || !tocTree.length) return;
    setScrollSectionNo(initialScrollSectionNo);
    const node = findTocNodeBySectionNo(tocTree, initialScrollSectionNo);
    if (node?.key) setSelectedSectionKey(String(node.key));
  }, [initialScrollSectionNo, tocTree]);

  const runAnalyze = async () => {
    if (!docId) return;
    setAnalyzing(true);
    try {
      const res = await fetch(
        `/api/wjgl1/files/${encodeURIComponent(docId)}/graph-link-analyze`,
        { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ category }) }
      );
      const data = await res.json();
      if (!data.success) {
        message.error(data.error || '分析失败');
        return;
      }
      setGraphLinkStatus('done');
      setRecommendations(data.data.graph_links?.candidates || []);
      message.success('关联推荐已更新');
    } catch (e: unknown) {
      message.error(e instanceof Error ? e.message : '分析失败');
    } finally {
      setAnalyzing(false);
    }
  };

  const handleSaveMetadata = async () => {
    const values = await form.validateFields();
    setSaving(true);
    try {
      const res = await fetch(`/api/wjgl1/files/${encodeURIComponent(docId)}/metadata`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          category,
          title: values.std_name,
          std_name: values.std_name,
          std_code: values.std_code,
          publish_date: values.publish_date,
          issuer: values.issuer,
          abstract: values.abstract,
          keywords: values.keywords
            ? String(values.keywords).split(/[,，;；]/).map((s: string) => s.trim()).filter(Boolean)
            : [],
          document_toc: documentToc.length ? documentToc : tocTreeNodesToItems(tocTree),
          full_html: fullHtml,
          full_markdown: fullMarkdown,
        }),
      });
      const data = await res.json();
      if (data.success) message.success('元数据已保存');
      else message.error(data.error || '保存失败');
    } catch (e: unknown) {
      message.error(e instanceof Error ? e.message : '保存失败');
    } finally {
      setSaving(false);
    }
  };

  const handleConfirm = async () => {
    if (!equipmentTabsHasLinks(equipmentTabs)) {
      message.warning('请至少在一个设备 Tab 中新增一条关联');
      return;
    }
    setConfirming(true);
    try {
      await handleSaveMetadata();
      const res = await fetch(`/api/wjgl1/files/${encodeURIComponent(docId)}/graph-link-confirm`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          category,
          equipment_tabs: equipmentTabs.map(t => ({
            equipment_id: t.equipment_id,
            equipment_name: t.equipment_name,
            links: tabToConfirmLinks(t),
          })),
        }),
      });
      const data = await res.json();
      if (data.success) {
        message.success(`已确认 ${data.data.confirmed} 条关联`);
        onConfirmed?.();
      } else {
        message.error(data.error || '确认失败');
      }
    } catch (e: unknown) {
      message.error(e instanceof Error ? e.message : '确认失败');
    } finally {
      setConfirming(false);
    }
  };

  if (loading && !file) {
    return <div style={{ padding: 32, textAlign: 'center' }}><Spin tip="加载审核信息..." /></div>;
  }

  return (
    <div>
      {showProgress && (
        <Card size="small" style={{ marginBottom: 12 }}>
          <Progress
            percent={graphLinkStatus === 'done' ? 100 : analyzing ? 50 : 20}
            size="small"
            format={() => `关联推荐: ${graphLinkStatus}`}
          />
        </Card>
      )}

      <Card title="文档元信息" size="small" style={{ marginBottom: 12 }}>
        <Form form={form} layout="vertical" size="small">
          <Row gutter={12}>
            <Col span={12}><Form.Item label="标题" name="std_name"><Input /></Form.Item></Col>
            <Col span={12}><Form.Item label="文档标识" name="std_code"><Input /></Form.Item></Col>
            <Col span={8}><Form.Item label="颁布时间" name="publish_date"><Input /></Form.Item></Col>
            <Col span={16}><Form.Item label="单位" name="issuer"><Input /></Form.Item></Col>
            <Col span={24}><Form.Item label="摘要" name="abstract"><MathAwareTextArea rows={2} /></Form.Item></Col>
          </Row>
        </Form>
        <div style={{ color: '#888', fontSize: 12 }}>
          {(file?.file_type as string || '').toUpperCase()} · {formatFileSize(file?.file_size as number)}
        </div>
      </Card>

      <Row gutter={12} style={{ marginBottom: 12 }}>
        <Col span={8}>
          <Card
            size="small"
            title="文档结构"
            extra={
              <Button
                size="small"
                icon={<ReloadOutlined />}
                loading={tocLoading || fullLoading}
                onClick={() => void loadDocument(String(file?.object_name || ''), documentToc)}
              >
                刷新
              </Button>
            }
          >
            {docProgress ? (
              <p style={{ fontSize: 12, color: '#666', marginBottom: 8 }}>{docProgress}</p>
            ) : null}
            <DocumentTocTree
              treeData={tocTree}
              loading={tocLoading}
              selectedKey={selectedSectionKey}
              onSelect={(key, node) => {
                setSelectedSectionKey(key);
                setScrollSectionNo(node?.sectionNo || '');
              }}
            />
          </Card>
        </Col>
        <Col span={16}>
          <Card size="small" title={tocNodeTitle(findTocNode(tocTree, selectedSectionKey)) || '全文内容'}>
            <FullDocumentEditor
              html={fullHtml}
              markdown={fullMarkdown}
              docId={docId}
              category={category}
              loading={fullLoading}
              scrollToSectionNo={scrollSectionNo}
              height={480}
            />
          </Card>
        </Col>
      </Row>

      <Card
        size="small"
        title="推荐关联节点"
        style={{ marginBottom: 12 }}
        extra={
          <Button size="small" icon={<ReloadOutlined />} loading={analyzing} onClick={() => void runAnalyze()}>
            刷新推荐
          </Button>
        }
      >
        <DeviceLinkTabs
          equipmentTabs={equipmentTabs}
          recommendations={recommendations}
          onChange={setEquipmentTabs}
        />
      </Card>

      <div style={{ textAlign: 'right' }}>
        <Space>
          <Button icon={<SaveOutlined />} loading={saving} onClick={() => void handleSaveMetadata()}>保存元数据</Button>
          <Button type="primary" icon={<LinkOutlined />} loading={confirming} onClick={() => void handleConfirm()}>
            确认关联并入库
          </Button>
        </Space>
      </div>
    </div>
  );
}
