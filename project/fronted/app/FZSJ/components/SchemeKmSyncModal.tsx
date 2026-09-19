'use client';

import { useEffect, useState } from 'react';
import { Modal, message } from 'antd';
import FileMetadataForm from '@/app/ZSGL/WJGL/components/FileMetadataForm';
import type { UploadFileMeta } from '@/app/ZSGL/WJGL/utils/wjglUploadPipeline';
import { syncSchemeToKm } from '../lib/schemeKmSync';

interface SchemeKmSyncModalProps {
  open: boolean;
  initialMeta: UploadFileMeta;
  file: File | null;
  onClose: () => void;
  onSuccess?: (docId: string) => void;
}

export default function SchemeKmSyncModal({
  open,
  initialMeta,
  file,
  onClose,
  onSuccess,
}: SchemeKmSyncModalProps) {
  const [meta, setMeta] = useState<UploadFileMeta>(initialMeta);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (open) {
      setMeta(initialMeta);
    }
  }, [open, initialMeta]);

  const handleConfirm = async () => {
    if (!file) {
      message.error('未找到待同步文件');
      return;
    }
    if (!meta.std_name?.trim()) {
      message.warning('请填写标题');
      return;
    }
    setLoading(true);
    try {
      const result = await syncSchemeToKm({ file, meta });
      message.success(`已同步到知识管理（${result.doc_id}）`);
      onSuccess?.(result.doc_id);
      onClose();
    } catch (err: unknown) {
      message.error(err instanceof Error ? err.message : '同步失败');
    } finally {
      setLoading(false);
    }
  };

  return (
    <Modal
      title="同步到知识管理 - 试验方案"
      open={open}
      onCancel={onClose}
      onOk={() => void handleConfirm()}
      confirmLoading={loading}
      okText="确认同步"
      cancelText="取消"
      width={640}
      destroyOnHidden
    >
      <div style={{ marginBottom: 12, fontSize: 13, color: '#666' }}>
        请确认元数据后将文件入库至知识管理「试验方案」。摘要与标题已根据页面内容预填，可按需修改。
      </div>
      {file ? (
        <div style={{ marginBottom: 12, fontSize: 12, color: '#888' }}>
          待同步文件：{file.name}
        </div>
      ) : null}
      <FileMetadataForm
        meta={meta}
        onChange={(patch) => setMeta((prev) => ({ ...prev, ...patch }))}
        showSyncChroma
        syncChroma
      />
    </Modal>
  );
}
