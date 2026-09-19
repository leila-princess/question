'use client';

import React from 'react';
import { Row, Col, Card, Button, Spin, Alert, Space } from 'antd';
import { ReloadOutlined } from '@ant-design/icons';
import DocumentTocTree from '@/app/ZSGL/WJGL/components/DocumentTocTree';
import FullDocumentEditor from '@/app/ZSGL/WJGL/components/FullDocumentEditor';
import { canResumeMineruPoll, findTocNode, mineruStatusAlertType, tocNodeTitle } from '@/app/ZSGL/WJGL/components/documentTocUtils';
import type { UploadFileItem } from '@/app/ZSGL/WJGL/components/UploadModal';

interface MethodFileContentReviewProps {
  item: UploadFileItem;
  onChange: (patch: Partial<UploadFileItem>) => void;
  onResumeMineru: () => void;
  onForceReparse: () => void;
  /** auto: md/txt 走 Markdown 渲染；html: 强制 HTML（MinerU/PDF/Word） */
  contentMode?: 'auto' | 'html';
  /** default: 目录+阅读器；reader-only: 仅阅读器（向导三栏布局用） */
  layout?: 'default' | 'reader-only';
  editorHeight?: number;
  defaultMode?: 'preview' | 'edit';
  showModeSwitch?: boolean;
  activeSectionNo?: string;
  onActiveSectionNo?: (sectionNo: string) => void;
}

/** 分类管理-方法提取：仅全文预览与校对，不含元数据/关联节点 */
export default function MethodFileContentReview({
  item,
  onChange,
  onResumeMineru,
  onForceReparse,
  contentMode: contentModeProp,
  layout = 'default',
  editorHeight = 420,
  defaultMode = 'edit',
  showModeSwitch = true,
  activeSectionNo,
  onActiveSectionNo,
}: MethodFileContentReviewProps) {
  const showResumeMineru = canResumeMineruPoll(item);
  const ext = (item.name.split('.').pop() || '').toLowerCase();
  const contentMode = contentModeProp ?? (['md', 'txt'].includes(ext) ? 'auto' : 'html');

  if (item.parseStatus === 'parsing' || item.parseStatus === 'pending') {
    return (
      <div style={{ textAlign: 'center', padding: 48 }}>
        <Spin description="正在解析文档正文，请稍候…" />
      </div>
    );
  }

  if (item.parseStatus === 'error') {
    return (
      <div>
        <Alert type="error" showIcon message="文档解析失败" description={item.parseError || '未知错误'} style={{ marginBottom: 12 }} />
        <Space>
          {showResumeMineru && (
            <Button type="primary" onClick={onResumeMineru}>继续等待 MinerU</Button>
          )}
          <Button icon={<ReloadOutlined />} onClick={onForceReparse}>重新解析</Button>
        </Space>
      </div>
    );
  }

  return (
    <div>
      {item.contentError ? (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 12 }}
          message="正文加载未完成"
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
      ) : null}
      {(item.mineruStatus || item.mineruLoading) ? (
        <Alert
          type={mineruStatusAlertType(item.mineruLoading, item.mineruStatus)}
          showIcon
          style={{ marginBottom: 12 }}
          message={item.mineruStatus || 'MinerU 解析中…'}
          action={showResumeMineru && !item.mineruLoading ? (
            <Button size="small" type="primary" onClick={onResumeMineru}>继续等待</Button>
          ) : undefined}
        />
      ) : null}
      {item.parseWarnings?.length ? (
        <Alert
          type={item.tocLocked ? 'success' : 'info'}
          showIcon
          style={{ marginBottom: 12 }}
          message="目录解析"
          description={(
            <ul style={{ margin: 0, paddingLeft: 20 }}>
              {item.parseWarnings.map((w) => (
                <li key={w}>{w}</li>
              ))}
            </ul>
          )}
        />
      ) : null}

      {layout === 'reader-only' ? (
        <Card
          size="small"
          title={tocNodeTitle(findTocNode(item.tocTree, item.selectedSectionKey)) || '文档阅读'}
          extra={(
            <Space size={4}>
              {showResumeMineru && (
                <Button size="small" type="primary" onClick={onResumeMineru}>继续等待 MinerU</Button>
              )}
              <Button size="small" icon={<ReloadOutlined />} onClick={onForceReparse}>
                重新解析
              </Button>
            </Space>
          )}
        >
          <FullDocumentEditor
            html={item.fullHtml}
            markdown={item.fullMarkdown}
            docId={item.contentDocId}
            category={item.contentCategory}
            loading={item.fullLoading && !item.fullHtml && !item.fullMarkdown}
            scrollToSectionNo={item.scrollSectionNo}
            activeSectionNo={activeSectionNo}
            height={editorHeight}
            contentMode={contentMode}
            defaultMode={defaultMode}
            showModeSwitch={showModeSwitch}
            onActiveSectionNo={onActiveSectionNo}
            onChange={(nextHtml) => onChange({ fullHtml: nextHtml })}
          />
        </Card>
      ) : (
      <Row gutter={12}>
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
          <Card
            size="small"
            title={tocNodeTitle(findTocNode(item.tocTree, item.selectedSectionKey)) || '全文内容（可编辑校对）'}
            extra={(
              <Space size={4}>
                {showResumeMineru && (
                  <Button size="small" type="primary" onClick={onResumeMineru}>继续等待 MinerU</Button>
                )}
                <Button size="small" icon={<ReloadOutlined />} onClick={onForceReparse}>
                  强制重新解析
                </Button>
              </Space>
            )}
          >
            <FullDocumentEditor
              html={item.fullHtml}
              markdown={item.fullMarkdown}
              docId={item.contentDocId}
              category={item.contentCategory}
              loading={item.fullLoading && !item.fullHtml && !item.fullMarkdown}
              scrollToSectionNo={item.scrollSectionNo}
              activeSectionNo={activeSectionNo}
              height={editorHeight}
              contentMode={contentMode}
              defaultMode={defaultMode}
              showModeSwitch={showModeSwitch}
              onActiveSectionNo={onActiveSectionNo}
              onChange={(nextHtml) => onChange({ fullHtml: nextHtml })}
            />
          </Card>
        </Col>
      </Row>
      )}
    </div>
  );
}
