'use client';

import React from 'react';
import { Alert, Spin } from 'antd';
import FileMetadataForm from '@/app/ZSGL/WJGL/components/FileMetadataForm';
import type { UploadFileItem } from '@/app/ZSGL/WJGL/components/UploadModal';

interface MethodFileMetadataReviewProps {
  item: UploadFileItem;
  onChange: (patch: Partial<UploadFileItem>) => void;
  showSyncChroma?: boolean;
  /** 元数据异步提取中（与正文解析分离） */
  metaLoading?: boolean;
}

/** 分类管理-方法提取：元数据审核（不含图谱关联） */
export default function MethodFileMetadataReview({
  item,
  onChange,
  showSyncChroma = true,
  metaLoading = false,
}: MethodFileMetadataReviewProps) {
  const updateMeta = (patch: Partial<UploadFileItem['meta']>) => {
    onChange({ meta: { ...item.meta, ...patch } });
  };

  if (metaLoading) {
    return (
      <div style={{ textAlign: 'center', padding: 32 }}>
        <Spin tip="正在提取元数据…" />
      </div>
    );
  }

  if (item.parseStatus === 'error' && !item.fullHtml?.trim() && !item.fullMarkdown?.trim()) {
    return (
      <Alert type="error" showIcon message="文档解析失败" description={item.parseError || '未知错误'} />
    );
  }

  const isPdf = item.meta.file_type?.toLowerCase() === 'pdf';

  return (
    <div>
      {(item.parseWarnings || []).length > 0 ? (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 12 }}
          message="元数据提示"
          description={(item.parseWarnings || []).join('；')}
        />
      ) : null}
      <FileMetadataForm
        meta={item.meta}
        onChange={updateMeta}
        showSyncChroma={showSyncChroma && isPdf}
        syncChroma={item.syncChroma}
        onSyncChromaChange={(v) => onChange({ syncChroma: v })}
      />
    </div>
  );
}
