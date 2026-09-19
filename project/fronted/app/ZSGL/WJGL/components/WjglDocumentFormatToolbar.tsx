'use client';

import React, { useRef } from 'react';
import { Button, Space, Tooltip, message } from 'antd';
import {
  BoldOutlined,
  ItalicOutlined,
  UnderlineOutlined,
  OrderedListOutlined,
  UnorderedListOutlined,
  TableOutlined,
  PictureOutlined,
} from '@ant-design/icons';

function exec(cmd: string, value?: string) {
  try {
    return document.execCommand(cmd, false, value);
  } catch {
    return false;
  }
}

function Btn({ title, icon, onCommand }: { title: string; icon: React.ReactNode; onCommand: () => void }) {
  return (
    <Tooltip title={title}>
      <Button
        type="text"
        size="small"
        icon={icon}
        onMouseDown={(e) => e.preventDefault()}
        onClick={onCommand}
        style={{ padding: '0 6px', height: 28 }}
      />
    </Tooltip>
  );
}

export default function WjglDocumentFormatToolbar() {
  const fileRef = useRef<HTMLInputElement>(null);

  const insertTable = () => {
    const html =
      '<table border="1" cellpadding="4" cellspacing="0" style="border-collapse:collapse;width:100%;margin:8px 0">' +
      '<tr><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td></tr>' +
      '<tr><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td></tr></table><p><br></p>';
    exec('insertHTML', html);
  };

  const onPickImage = (file: File) => {
    if (!file.type.startsWith('image/')) {
      message.warning('请选择图片文件');
      return;
    }
    const reader = new FileReader();
    reader.onload = () => {
      const src = String(reader.result || '');
      exec(
        'insertHTML',
        `<figure style="margin:12px 0;text-align:center"><img src="${src}" style="max-width:100%;height:auto" alt="插图"/></figure><p><br></p>`,
      );
    };
    reader.readAsDataURL(file);
  };

  return (
    <div style={{ padding: '6px 8px', borderBottom: '1px solid #f0f0f0', background: '#fafafa' }}>
      <Space size={2} wrap>
        <Btn title="加粗" icon={<BoldOutlined />} onCommand={() => exec('bold')} />
        <Btn title="斜体" icon={<ItalicOutlined />} onCommand={() => exec('italic')} />
        <Btn title="下划线" icon={<UnderlineOutlined />} onCommand={() => exec('underline')} />
        <Btn title="有序列表" icon={<OrderedListOutlined />} onCommand={() => exec('insertOrderedList')} />
        <Btn title="无序列表" icon={<UnorderedListOutlined />} onCommand={() => exec('insertUnorderedList')} />
        <Btn title="插入表格" icon={<TableOutlined />} onCommand={insertTable} />
        <Btn title="插入图片" icon={<PictureOutlined />} onCommand={() => fileRef.current?.click()} />
        <input
          ref={fileRef}
          type="file"
          accept="image/*"
          style={{ display: 'none' }}
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) onPickImage(f);
            e.target.value = '';
          }}
        />
      </Space>
    </div>
  );
}
