'use client';

import React, { useCallback } from 'react';
import { Row, Col, Button, Spin, Card, Alert, Space } from 'antd';
import { ReloadOutlined } from '@ant-design/icons';
import DocumentTocTree from './DocumentTocTree';
import FullDocumentEditor from './FullDocumentEditor';
import FileMetadataForm from './FileMetadataForm';
import DeviceLinkTabs from './DeviceLinkTabs';
import { canResumeMineruPoll, fetchPreviewGraphLinks, findTocNode, mineruStatusAlertType, tocNodeTitle } from './documentTocUtils';
import type { UploadFileItem } from './UploadModal';

interface UploadFileReviewProps {
  item: UploadFileItem;
  onChange: (patch: Partial<UploadFileItem>) => void;
  onResumeMineru: () => void;
  onForceReparse: () => void;
}

export default function UploadFileReview({ item, onChange, onResumeMineru, onForceReparse }: UploadFileReviewProps) {
  const updateMeta = (patch: Partial<UploadFileItem['meta']>) => {
    onChange({ meta: { ...item.meta, ...patch } });
  };

  const refreshGraphLinks = useCallback(async () => {
    try {
      const json = await fetchPreviewGraphLinks(item.meta);
      if (!json.success) throw new Error(String(json.error || '刷新推荐失败'));
      onChange({
        graphCandidates: (json.data as { candidates?: UploadFileItem['graphCandidates'] })?.candidates || [],
        graphWarnings: (json.data as { warnings?: string[] })?.warnings || [],
      });
    } catch (e: unknown) {
      onChange({
        graphWarnings: [e instanceof Error ? e.message : '刷新推荐失败'],
      });
    }
  }, [item.meta, onChange]);

  const showResumeMineru = canResumeMineruPoll(item);

  if (item.parseStatus === 'parsing' || item.parseStatus === 'pending') {
    return <div style={{ textAlign: 'center', padding: 48 }}><Spin tip="解析元数据与关联推荐…" /></div>;
  }

  if (item.parseStatus === 'error') {
    return (
      <div>
        <p style={{ color: 'red' }}>{item.parseError}</p>
        <Space>
          {showResumeMineru && (
            <Button type="primary" onClick={onResumeMineru}>继续等待 MinerU</Button>
          )}
          <Button onClick={onForceReparse}>重新解析</Button>
        </Space>
      </div>
    );
  }

  return (
    <div>
      {(item.contentError) && (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 12 }}
          message="正文预览未完成"
          description={(
            <div>
              <div>{item.contentError}</div>
              {showResumeMineru && (
                <Button type="link" size="small" style={{ paddingLeft: 0 }} onClick={onResumeMineru}>
                  继续等待 MinerU（不重新开始）
                </Button>
              )}
            </div>
          )}
        />
      )}
      {(item.mineruStatus || item.mineruLoading) && (
        <Alert
          type={mineruStatusAlertType(item.mineruLoading, item.mineruStatus)}
          showIcon
          style={{ marginBottom: 12 }}
          message={item.mineruStatus || 'MinerU 解析中…'}
          action={showResumeMineru && !item.mineruLoading ? (
            <Button size="small" type="primary" onClick={onResumeMineru}>继续等待</Button>
          ) : undefined}
        />
      )}
      {(item.parseWarnings || []).length > 0 && (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 12 }}
          message="元数据提示"
          description={(item.parseWarnings || []).join('；')}
        />
      )}
      {(item.graphWarnings || []).length > 0 && (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 12 }}
          message="关联推荐提示"
          description={(item.graphWarnings || []).join('；')}
        />
      )}
      <div style={{ marginBottom: 12 }}>
        <FileMetadataForm meta={item.meta} onChange={updateMeta} />
      </div>

      <Row gutter={12} style={{ marginBottom: 12 }}>
        <Col span={8}>
          <Card size="small" title="文档结构">
            <DocumentTocTree
              treeData={item.tocTree}
              loading={item.fullLoading}
              selectedKey={item.selectedSectionKey}
              onSelect={(key, node) => {
                onChange({
                  selectedSectionKey: key,
                  scrollSectionNo: node?.sectionNo || '',
                });
              }}
            />
          </Card>
        </Col>
        <Col span={16}>
          <Card size="small" title={tocNodeTitle(findTocNode(item.tocTree, item.selectedSectionKey)) || '全文内容'}>
            <FullDocumentEditor
              html={item.fullHtml}
              markdown={item.fullMarkdown}
              docId={item.contentDocId}
              category={item.contentCategory}
              loading={item.fullLoading && !item.fullHtml && !item.fullMarkdown}
              scrollToSectionNo={item.scrollSectionNo}
              height={360}
              onChange={(nextHtml) => onChange({ fullHtml: nextHtml })}
            />
          </Card>
        </Col>
      </Row>

      <Card
        size="small"
        title="推荐关联节点"
        extra={(
          <Space size={4}>
            <Button size="small" icon={<ReloadOutlined />} onClick={() => void refreshGraphLinks()}>
              刷新推荐
            </Button>
            {showResumeMineru && (
              <Button size="small" type="primary" onClick={onResumeMineru}>继续等待 MinerU</Button>
            )}
            <Button size="small" onClick={onForceReparse}>强制重新解析</Button>
          </Space>
        )}
      >
        {!item.graphCandidates.length && !(item.graphWarnings || []).length ? (
          <Alert
            type="info"
            showIcon
            style={{ marginBottom: 12 }}
            message="暂无自动推荐"
            description="请确认 Neo4j 已启动且图库有数据；可修改关键词/摘要后点「刷新推荐」，或手动添加设备关联。"
          />
        ) : null}
        <DeviceLinkTabs
          equipmentTabs={item.equipmentTabs}
          recommendations={item.graphCandidates}
          onChange={tabs => onChange({ equipmentTabs: tabs })}
        />
      </Card>
    </div>
  );
}
