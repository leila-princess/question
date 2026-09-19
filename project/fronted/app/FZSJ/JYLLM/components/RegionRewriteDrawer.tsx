'use client';

import { useMemo } from 'react';
import { Modal, Input, Button, Space, Typography, Spin, Checkbox, Alert } from 'antd';
import MathAwareMarkdown from '@/components/MathAwareMarkdown';
import type { RegionRewriteSpec } from '../types/flow';
import { regionTypeLabel } from '../lib/regionRewrite';
import { computeRegionDiff, diffSummary } from '../lib/regionDiff';

const { Text } = Typography;
const { TextArea } = Input;

interface RegionRewriteDrawerProps {
  open: boolean;
  spec: RegionRewriteSpec | null;
  regionPreview: string;
  originalRegion: string;
  streamingText: string;
  loading: boolean;
  mergedChapter: string | null;
  comment: string;
  diffConfirmed: boolean;
  validationWarnings?: string[];
  diffStats?: { similarity?: number; lines_added?: number; lines_removed?: number };
  onCommentChange: (v: string) => void;
  onDiffConfirmedChange: (v: boolean) => void;
  onClose: () => void;
  onStart: () => void;
  onStop: () => void;
  onApply: () => void;
}

export default function RegionRewriteDrawer({
  open,
  spec,
  regionPreview,
  originalRegion,
  streamingText,
  loading,
  mergedChapter,
  comment,
  diffConfirmed,
  validationWarnings = [],
  diffStats,
  onCommentChange,
  onDiffConfirmedChange,
  onClose,
  onStart,
  onStop,
  onApply,
}: RegionRewriteDrawerProps) {
  const preview = regionPreview || '';
  const stream = streamingText || '';
  const orig = originalRegion || preview;
  const canShowDiff = Boolean(stream.trim() && !loading);
  const canApply = Boolean(mergedChapter && !loading && canShowDiff && diffConfirmed);

  const diffLines = useMemo(() => {
    if (!canShowDiff) return [];
    return computeRegionDiff(orig, stream);
  }, [orig, stream, canShowDiff]);

  const summary = useMemo(() => diffSummary(diffLines), [diffLines]);

  return (
    <Modal
      title={spec ? `增量修订 · ${regionTypeLabel(spec.region_type)}${spec.label ? ` · ${spec.label}` : ''}` : '增量修订'}
      open={open}
      onCancel={onClose}
      width={820}
      destroyOnHidden
      footer={
        <Space>
          <Button onClick={onClose}>取消</Button>
          {loading ? (
            <Button danger onClick={onStop}>
              停止
            </Button>
          ) : (
            <Button type="primary" onClick={onStart} disabled={!comment.trim()}>
              开始修订
            </Button>
          )}
          <Button type="primary" onClick={onApply} disabled={!canApply}>
            应用修改
          </Button>
        </Space>
      }
    >
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 12 }}
        message="本次为增量修订，仅应改动您描述的部分；若 diff 中出现大段无关变更，请勿应用。"
      />

      {validationWarnings.length > 0 ? (
        <Alert type="warning" showIcon style={{ marginBottom: 12 }} message={validationWarnings.join('；')} />
      ) : null}

      <div style={{ marginBottom: 8 }}>
        <Text strong style={{ fontSize: 13 }}>
          区域原文
        </Text>
      </div>
      <div
        style={{
          maxHeight: 120,
          overflow: 'auto',
          padding: 10,
          background: '#fafafa',
          border: '1px solid #f0f0f0',
          borderRadius: 6,
          marginBottom: 12,
          fontSize: 13,
        }}
      >
        <MathAwareMarkdown content={preview.slice(0, 4000) || '（空）'} allowHtml />
      </div>

      <div style={{ marginBottom: 8 }}>
        <Text strong style={{ fontSize: 13 }}>
          修改意见
        </Text>
      </div>
      <TextArea
        rows={3}
        value={comment}
        onChange={(e) => onCommentChange(e.target.value)}
        placeholder="例如：将表格「持续时间」列改为具体数值（含单位）…"
        disabled={loading}
      />

      <div style={{ marginTop: 16, marginBottom: 8, display: 'flex', alignItems: 'center', gap: 8 }}>
        <Text strong style={{ fontSize: 13 }}>
          修订结果
        </Text>
        {loading ? <Spin size="small" /> : null}
        {diffStats?.similarity != null ? (
          <Text type="secondary" style={{ fontSize: 12 }}>
            相似度 {(diffStats.similarity * 100).toFixed(0)}%
          </Text>
        ) : null}
      </div>
      <div
        style={{
          minHeight: 80,
          maxHeight: 160,
          overflow: 'auto',
          padding: 10,
          background: '#fff',
          border: '1px solid #e8e8e8',
          borderRadius: 6,
          marginBottom: 12,
        }}
      >
        {stream ? (
          <MathAwareMarkdown content={stream} allowHtml />
        ) : (
          <Text type="secondary">点击「开始修订」后在此显示…</Text>
        )}
      </div>

      {canShowDiff ? (
        <>
          <div style={{ marginBottom: 8, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <Text strong style={{ fontSize: 13 }}>
              Diff 对比
            </Text>
            <Text type="secondary" style={{ fontSize: 12 }}>
              +{summary.added} / -{summary.removed} 行
            </Text>
          </div>
          <div
            style={{
              maxHeight: 200,
              overflow: 'auto',
              padding: 8,
              background: '#fafafa',
              border: '1px solid #f0f0f0',
              borderRadius: 6,
              fontFamily: 'Consolas, monospace',
              fontSize: 12,
              lineHeight: 1.5,
              marginBottom: 12,
            }}
          >
            {diffLines.map((d, i) => {
              if (d.type === 'same') {
                return (
                  <div key={i} style={{ color: '#8c8c8c', whiteSpace: 'pre-wrap' }}>
                    {d.oldLine}
                  </div>
                );
              }
              if (d.type === 'remove') {
                return (
                  <div key={i} style={{ background: '#fff1f0', color: '#cf1322', whiteSpace: 'pre-wrap' }}>
                    - {d.oldLine}
                  </div>
                );
              }
              return (
                <div key={i} style={{ background: '#f6ffed', color: '#389e0d', whiteSpace: 'pre-wrap' }}>
                  + {d.newLine}
                </div>
              );
            })}
          </div>
          <Checkbox checked={diffConfirmed} onChange={(e) => onDiffConfirmedChange(e.target.checked)}>
            我已核对 diff，确认仅包含预期变更
          </Checkbox>
        </>
      ) : null}
    </Modal>
  );
}
