'use client';

import React from 'react';
import { Row, Col, Input, Select, Switch } from 'antd';
import MathAwareTextArea from '@/components/MathAwareTextArea';
import type { UploadFileItem } from './UploadModal';

interface FileMetadataFormProps {
  meta: UploadFileItem['meta'];
  onChange: (patch: Partial<UploadFileItem['meta']>) => void;
  /** 是否展示 PDF 同步向量库开关 */
  showSyncChroma?: boolean;
  syncChroma?: boolean;
  onSyncChromaChange?: (v: boolean) => void;
}

export default function FileMetadataForm({
  meta,
  onChange,
  showSyncChroma = false,
  syncChroma = false,
  onSyncChromaChange,
}: FileMetadataFormProps) {
  return (
    <>
      <Row gutter={12}>
        <Col span={12}>
          <div style={{ marginBottom: 4, fontSize: 12, color: '#666' }}>标题</div>
          <Input
            value={meta.std_name}
            onChange={(e) => onChange({ std_name: e.target.value, title: e.target.value })}
          />
        </Col>
        <Col span={12}>
          <div style={{ marginBottom: 4, fontSize: 12, color: '#666' }}>文档标识</div>
          <Input value={meta.std_code} onChange={(e) => onChange({ std_code: e.target.value })} />
        </Col>
        <Col span={8} style={{ marginTop: 8 }}>
          <div style={{ marginBottom: 4, fontSize: 12, color: '#666' }}>颁布时间</div>
          <Input value={meta.publish_date} onChange={(e) => onChange({ publish_date: e.target.value })} />
        </Col>
        <Col span={16} style={{ marginTop: 8 }}>
          <div style={{ marginBottom: 4, fontSize: 12, color: '#666' }}>单位</div>
          <Input value={meta.issuer} onChange={(e) => onChange({ issuer: e.target.value })} />
        </Col>
        <Col span={24} style={{ marginTop: 8 }}>
          <div style={{ marginBottom: 4, fontSize: 12, color: '#666' }}>摘要</div>
          <MathAwareTextArea
            rows={2}
            value={meta.abstract}
            onChange={(e) => onChange({ abstract: e.target.value })}
          />
        </Col>
        <Col span={24} style={{ marginTop: 8 }}>
          <Select
            mode="tags"
            style={{ width: '100%' }}
            value={meta.keywords}
            onChange={(v) => onChange({ keywords: v })}
            placeholder="关键词"
          />
        </Col>
        {showSyncChroma ? (
          <Col span={24} style={{ marginTop: 12 }}>
            <span style={{ marginRight: 8, fontSize: 13 }}>同步向量库（PDF）</span>
            <Switch checked={syncChroma} onChange={onSyncChromaChange} size="small" />
          </Col>
        ) : null}
      </Row>
    </>
  );
}
