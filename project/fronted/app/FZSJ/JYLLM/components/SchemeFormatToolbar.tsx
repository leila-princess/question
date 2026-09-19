'use client';

/**
 * 方案正文格式工具栏：Ant Design Button + document.execCommand，
 * 父级在失焦/定时将 HTML 转回 Markdown（schemeHtmlMarkdown）。
 * 支持插图（有 taskId 时上传 MinIO 后插入 URL；否则 data URL）、插入简单表格。
 */
import React, { useRef } from 'react';
import { Button, Space, Tooltip, message } from 'antd';
import {
  BoldOutlined,
  ItalicOutlined,
  UnderlineOutlined,
  StrikethroughOutlined,
  OrderedListOutlined,
  UnorderedListOutlined,
  DoubleRightOutlined,
  MinusOutlined,
  ClearOutlined,
  PictureOutlined,
  TableOutlined,
} from '@ant-design/icons';
import { uploadSchemeAsset } from '../api';

type BlockTag = 'H1' | 'H2' | 'H3' | 'H4' | 'H5' | 'H6';

function exec(cmd: string, value?: string) {
  try {
    if (value !== undefined) {
      return document.execCommand(cmd, false, value);
    }
    return document.execCommand(cmd, false);
  } catch {
    return false;
  }
}

function Btn({
  title,
  icon,
  onCommand,
}: {
  title: string;
  icon: React.ReactNode;
  onCommand: () => void;
}) {
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

export default function SchemeFormatToolbar({ taskId }: { taskId?: string | null }) {
  const fileInputRef = useRef<HTMLInputElement>(null);

  const heading = (tag: BlockTag) => () => {
    exec('formatBlock', tag);
  };

  const insertTable = () => {
    const html = `<table style="border-collapse:collapse;width:100%;border:1px solid #ddd;margin:10px 0"><thead><tr><th style="border:1px solid #ddd;padding:8px">列1</th><th style="border:1px solid #ddd;padding:8px">列2</th><th style="border:1px solid #ddd;padding:8px">列3</th></tr></thead><tbody><tr><td style="border:1px solid #ddd;padding:8px">&nbsp;</td><td style="border:1px solid #ddd;padding:8px">&nbsp;</td><td style="border:1px solid #ddd;padding:8px">&nbsp;</td></tr></tbody></table><p><br></p>`;
    exec('insertHTML', html);
  };

  const onPickImage = () => {
    fileInputRef.current?.click();
  };

  const onImageFile = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    if (!f || !f.type.startsWith('image/')) {
      e.target.value = '';
      return;
    }
    const reader = new FileReader();
    reader.onload = () => {
      void (async () => {
        let url = String(reader.result || '');
        if (!url) return;
        if (taskId?.trim()) {
          try {
            const persisted = await uploadSchemeAsset(taskId.trim(), url);
            if (persisted) {
              url = persisted;
            } else {
              message.warning('图片上传失败，已使用本地预览（保存后可能较大）');
            }
          } catch {
            message.warning('图片上传异常，已使用本地预览');
          }
        }
        exec(
          'insertHTML',
          `<p><img src="${url}" alt="插图" style="max-width:100%;height:auto;display:block;margin:8px 0" /></p>`,
        );
      })();
    };
    reader.readAsDataURL(f);
    e.target.value = '';
  };

  return (
    <div
      style={{
        display: 'flex',
        flexWrap: 'wrap',
        alignItems: 'center',
        gap: 4,
        padding: '6px 8px',
        borderBottom: '1px solid #e8e8e8',
        background: '#fafafa',
      }}
    >
      <input
        ref={fileInputRef}
        type="file"
        accept="image/*"
        style={{ display: 'none' }}
        onChange={onImageFile}
      />
      <Space size={2} wrap>
        <Btn title="加粗" icon={<BoldOutlined />} onCommand={() => exec('bold')} />
        <Btn title="斜体" icon={<ItalicOutlined />} onCommand={() => exec('italic')} />
        <Btn title="下划线" icon={<UnderlineOutlined />} onCommand={() => exec('underline')} />
        <Btn title="删除线" icon={<StrikethroughOutlined />} onCommand={() => exec('strikeThrough')} />
        <Btn title="标题1" icon={<span style={{ fontSize: 11, fontWeight: 700 }}>H1</span>} onCommand={heading('H1')} />
        <Btn title="标题2" icon={<span style={{ fontSize: 11, fontWeight: 700 }}>H2</span>} onCommand={heading('H2')} />
        <Btn title="标题3" icon={<span style={{ fontSize: 11, fontWeight: 700 }}>H3</span>} onCommand={heading('H3')} />
        <Btn title="标题4" icon={<span style={{ fontSize: 11, fontWeight: 700 }}>H4</span>} onCommand={heading('H4')} />
        <Btn title="标题5" icon={<span style={{ fontSize: 11, fontWeight: 700 }}>H5</span>} onCommand={heading('H5')} />
        <Btn title="标题6" icon={<span style={{ fontSize: 11, fontWeight: 700 }}>H6</span>} onCommand={heading('H6')} />
        <Btn title="有序列表" icon={<OrderedListOutlined />} onCommand={() => exec('insertOrderedList')} />
        <Btn title="无序列表" icon={<UnorderedListOutlined />} onCommand={() => exec('insertUnorderedList')} />
        <Btn title="引用" icon={<DoubleRightOutlined />} onCommand={() => exec('formatBlock', 'blockquote')} />
        <Btn title="分割线" icon={<MinusOutlined />} onCommand={() => exec('insertHorizontalRule')} />
        <Btn title="插图" icon={<PictureOutlined />} onCommand={onPickImage} />
        <Btn title="插入表格（3列）" icon={<TableOutlined />} onCommand={insertTable} />
        <Btn
          title="清除格式"
          icon={<ClearOutlined />}
          onCommand={() => {
            exec('removeFormat');
            exec('formatBlock', 'P');
          }}
        />
      </Space>
    </div>
  );
}
