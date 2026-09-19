'use client';

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Editor, Toolbar } from '@wangeditor/editor-for-react';
import type { IDomEditor, IEditorConfig, IToolbarConfig } from '@wangeditor/editor';
import '@wangeditor/editor/dist/css/style.css';

export interface WangEditorProps {
  value: string;
  onChange: (html: string) => void;
  /** 返回 { url, alt, href } 供编辑器插入图片 */
  uploadImage?: (file: File) => Promise<{ url: string; alt: string; href: string }>;
  height?: number;
}

const WangEditorInner: React.FC<WangEditorProps> = ({
  value,
  onChange,
  uploadImage,
  height = 320,
}) => {
  const [editor, setEditor] = useState<IDomEditor | null>(null);

  const toolbarConfig = useMemo<Partial<IToolbarConfig>>(() => ({}), []);

  const editorConfig = useMemo<Partial<IEditorConfig>>(() => {
    const base: Partial<IEditorConfig> = {
      placeholder: '请输入内容…',
      readOnly: false,
      autoFocus: false,
    };
    if (!uploadImage) {
      return base;
    }
    return {
      ...base,
      MENU_CONF: {
        uploadImage: {
          maxFileSize: 20 * 1024 * 1024,
          maxNumberOfFiles: 10,
          allowedFileTypes: ['image/*'],
          async customUpload(file: File, insertFn: (url: string, alt: string, href: string) => void) {
            const res = await uploadImage(file);
            insertFn(res.url, res.alt, res.href);
          },
        },
      },
    };
  }, [uploadImage]);

  const handleChange = useCallback(
    (ed: IDomEditor) => {
      onChange(ed.getHtml());
    },
    [onChange],
  );

  // 父级 value 变化时同步到编辑器（如弹窗切换记录）
  useEffect(() => {
    if (!editor) return;
    const next = value ?? '';
    try {
      if (editor.getHtml() !== next) {
        editor.setHtml(next || '<p><br></p>');
      }
    } catch {
      /* 可能已 destroy */
    }
  }, [value, editor]);

  useEffect(() => {
    return () => {
      if (editor == null) return;
      try {
        editor.destroy();
      } catch {
        /* ignore */
      }
    };
  }, [editor]);

  return (
    <div
      style={{
        border: '1px solid #d9d9d9',
        borderRadius: 6,
        overflow: 'hidden',
        background: '#fff',
      }}
    >
      <Toolbar
        editor={editor}
        defaultConfig={toolbarConfig}
        mode="default"
        style={{ borderBottom: '1px solid #f0f0f0' }}
      />
      <Editor
        defaultConfig={editorConfig}
        value={value ?? ''}
        onCreated={setEditor}
        onChange={handleChange}
        mode="default"
        style={{ height, overflowY: 'hidden' }}
      />
    </div>
  );
};

export default WangEditorInner;
