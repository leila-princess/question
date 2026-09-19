'use client';

import { Button, Input, Space, message, Typography, Collapse, Slider, Tag, Tabs, Empty } from 'antd';
import { SaveOutlined, ReloadOutlined, CloudUploadOutlined, UndoOutlined } from '@ant-design/icons';
import type { ChapterModule } from '../types/flow';
import type { RegionRewriteSpec } from '../types/flow';
import SchemeDocumentPanel from './SchemeDocumentPanel';
import SchemeExportDropdown from '../../components/SchemeExportDropdown';
import type { SchemeExportFormat } from '../../lib/schemeKmMeta';

const { TextArea } = Input;
const { Text } = Typography;

interface ContentDrawerProps {
  open: boolean;
  onClose: () => void;
  modules: ChapterModule[];
  isGenerating: boolean;
  onFeedbackSave?: (score: number, comment: string) => void | Promise<void>;
  onFeedbackRewrite?: (comment: string) => void | Promise<void>;
  onConfirm: () => void;
  onExport: (format: SchemeExportFormat) => void | Promise<void>;
  onSyncToKm?: () => void | Promise<void>;
  onResave?: () => void;
  progress?: number;
  mode: 'generating' | 'reviewing' | 'complete';
  width?: number;
  panelWidthPx?: number;
  currentChapter?: string;
  currentGeneratingChapter?: number | null;
  onChapterContentChange?: (chapterIndex: number, markdown: string) => void;
  onRequestRegionRewrite?: (spec: RegionRewriteSpec, regionPreview: string) => void;
  taskId?: string | null;
  feedbackRewriteLoading?: boolean;
  exportLoading?: boolean;
  syncLoading?: boolean;
  overallScore?: number;
  overallComment?: string;
  onOverallScoreChange?: (score: number) => void;
  onOverallCommentChange?: (comment: string) => void;
  /** 撤销上次区域修订 */
  onUndoRevision?: () => void;
  canUndoRevision?: boolean;
  /** 历史查看模式：右侧显示方案原文/查看评价双 Tab */
  historyViewMode?: boolean;
  historyFeedback?: { score?: number | null; comment?: string | null };
}

export default function ContentDrawer({
  open,
  onClose,
  modules,
  isGenerating,
  onFeedbackSave,
  onFeedbackRewrite,
  onConfirm,
  onExport,
  onSyncToKm,
  onResave,
  progress,
  mode,
  width = 40,
  panelWidthPx,
  currentGeneratingChapter = null,
  taskId = null,
  onChapterContentChange,
  onRequestRegionRewrite,
  feedbackRewriteLoading = false,
  exportLoading = false,
  syncLoading = false,
  overallScore = 90,
  overallComment = '',
  onOverallScoreChange,
  onOverallCommentChange,
  onUndoRevision,
  canUndoRevision = false,
  historyViewMode = false,
  historyFeedback,
}: ContentDrawerProps) {
  const submitSave = async () => {
    if (onFeedbackSave) {
      await onFeedbackSave(overallScore, overallComment);
      message.success('评价已保存');
    }
  };

  const submitRewrite = async () => {
    if (!overallComment.trim()) {
      message.warning('请先填写修改意见');
      return;
    }
    if (onFeedbackRewrite) {
      await onFeedbackRewrite(overallComment);
    }
  };

  const usePx = panelWidthPx != null && panelWidthPx > 0;
  const outerWidth = open ? (usePx ? panelWidthPx : `${width}%`) : 0;
  const outerMinW = open ? (usePx ? panelWidthPx! : 400) : 0;

  const chapterChange = onChapterContentChange ?? (() => {});

  const exportControls = (
    <Space>
      <SchemeExportDropdown
        size="small"
        onExport={onExport}
        loading={exportLoading}
        disabled={isGenerating || modules.length === 0}
      />
      {onSyncToKm ? (
        <Button
          size="small"
          icon={<CloudUploadOutlined />}
          loading={syncLoading}
          onClick={() => void onSyncToKm()}
          disabled={isGenerating || modules.length === 0 || exportLoading}
        >
          同步到数据库
        </Button>
      ) : null}
    </Space>
  );

  const feedbackPanel = (
    <div style={{ padding: 16 }}>
      {historyFeedback?.score != null ? (
        <div style={{ marginBottom: 16 }}>
          <Text type="secondary" style={{ fontSize: 13 }}>评分：</Text>
          <Tag
            color={(historyFeedback.score ?? 0) >= 80 ? 'success' : 'warning'}
            style={{ fontSize: 14, padding: '2px 12px', marginLeft: 8 }}
          >
            {historyFeedback.score} 分
          </Tag>
        </div>
      ) : (
        <Empty description="暂无评分" image={Empty.PRESENTED_IMAGE_SIMPLE} style={{ marginBottom: 16 }} />
      )}
      <div style={{ marginBottom: 8 }}>
        <Text type="secondary" style={{ fontSize: 13 }}>评价内容</Text>
      </div>
      <TextArea
        rows={6}
        value={historyFeedback?.comment || ''}
        readOnly
        placeholder="暂无评价内容"
      />
    </div>
  );

  const documentPanel = modules.length > 0 ? (
    <SchemeDocumentPanel
      modules={modules}
      isGenerating={isGenerating}
      currentGeneratingChapter={currentGeneratingChapter}
      taskId={taskId}
      onChapterContentChange={chapterChange}
      onRequestRegionRewrite={onRequestRegionRewrite}
    />
  ) : (
    <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#8c8c8c' }}>
      {isGenerating ? '正在生成中...' : '暂无内容'}
    </div>
  );

  const mainContent = historyViewMode ? (
    <Tabs
      style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column' }}
      className="jyllm-history-tabs"
      items={[
        {
          key: 'content',
          label: '方案原文',
          children: (
            <div style={{ flex: 1, minHeight: 0, height: '100%', overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
              {documentPanel}
            </div>
          ),
        },
        {
          key: 'feedback',
          label: '查看评价',
          children: feedbackPanel,
        },
      ]}
    />
  ) : documentPanel;

  return (
    <div
      style={{
        width: outerWidth,
        minWidth: outerMinW,
        flexShrink: 0,
        height: '100%',
        background: '#fff',
        borderLeft: open ? '1px solid #e5e7eb' : 'none',
        transition: open && !usePx ? 'width 0.25s ease' : 'none',
        overflow: 'hidden',
        display: 'flex',
        flexDirection: 'column',
        boxShadow: open ? '-4px 0 16px rgba(0,0,0,0.05)' : 'none',
      }}
    >
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', minHeight: 0 }}>
        {mainContent}

        {mode === 'reviewing' && !historyViewMode && (
          <div style={{ padding: '16px 24px', borderTop: '1px solid #e5e7eb', background: '#fafafa' }}>
            <Collapse
              size="small"
              defaultActiveKey={['feedback']}
              style={{ marginBottom: 12 }}
              items={[
                {
                  key: 'feedback',
                  label: <span style={{ fontSize: 13 }}>整体方案评价</span>,
                  children: (
                    <div>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 12 }}>
                        <span style={{ fontSize: 13, whiteSpace: 'nowrap' }}>评分:</span>
                        <Slider
                          min={0}
                          max={100}
                          value={overallScore}
                          onChange={onOverallScoreChange}
                          style={{ flex: 1 }}
                          marks={{ 0: '0', 60: '60', 80: '80', 100: '100' }}
                        />
                        <Tag color={overallScore >= 80 ? 'success' : 'warning'} style={{ fontSize: 14, padding: '2px 12px' }}>
                          {overallScore}分
                        </Tag>
                      </div>
                      <TextArea
                        rows={3}
                        placeholder="输入整体意见或修改建议…（精确修改请用正文/目录「增量修订」）"
                        value={overallComment}
                        onChange={(e) => onOverallCommentChange?.(e.target.value)}
                      />
                    </div>
                  ),
                },
              ]}
            />
            <Space style={{ width: '100%', justifyContent: 'space-between' }}>
              <Space>
                <Button type="primary" icon={<SaveOutlined />} onClick={submitSave}>
                  保存评价
                </Button>
                <Button
                  icon={<ReloadOutlined />}
                  loading={feedbackRewriteLoading}
                  onClick={submitRewrite}
                >
                  反馈重写
                </Button>
                {canUndoRevision && onUndoRevision ? (
                  <Button icon={<UndoOutlined />} onClick={onUndoRevision}>
                    撤销修订
                  </Button>
                ) : null}
              </Space>
              <Space>
                {exportControls}
                <Button type="primary" onClick={onConfirm}>
                  确认方案
                </Button>
              </Space>
            </Space>
          </div>
        )}

        {mode === 'complete' && (
          <div style={{ padding: '16px 24px', borderTop: '1px solid #e5e7eb', background: '#fafafa' }}>
            <Space style={{ width: '100%', justifyContent: 'center' }}>
              {onResave && !historyViewMode ? (
                <Button type="primary" icon={<SaveOutlined />} onClick={onResave}>
                  重新保存
                </Button>
              ) : null}
              {exportControls}
            </Space>
          </div>
        )}
      </div>
      <style dangerouslySetInnerHTML={{ __html: `
        .jyllm-history-tabs .ant-tabs-content-holder {
          flex: 1;
          min-height: 0;
          overflow: hidden;
        }
        .jyllm-history-tabs .ant-tabs-content {
          height: 100%;
        }
        .jyllm-history-tabs .ant-tabs-tabpane {
          height: 100%;
        }
      `}} />
    </div>
  );
}
