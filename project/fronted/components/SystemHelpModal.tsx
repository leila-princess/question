'use client';

import { Modal } from 'antd';
import MarkdownRenderer from '@/components/MarkdownRenderer';
import systemHelpContent from '@/content/system-help.md';

interface SystemHelpModalProps {
  open: boolean;
  onClose: () => void;
}

export default function SystemHelpModal({ open, onClose }: SystemHelpModalProps) {
  return (
    <Modal
      title="帮助与更新说明"
      open={open}
      onCancel={onClose}
      footer={null}
      width={820}
      destroyOnHidden
      styles={{
        body: {
          maxHeight: '70vh',
          overflowY: 'auto',
          paddingTop: 8,
        },
      }}
    >
      <div
        className="system-help-markdown"
        style={{
          fontSize: 14,
          lineHeight: 1.7,
          color: '#333',
        }}
      >
        <MarkdownRenderer content={systemHelpContent} />
      </div>
    </Modal>
  );
}
