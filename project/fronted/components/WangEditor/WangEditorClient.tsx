'use client';

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Editor, Toolbar } from '@wangeditor/editor-for-react';
import type { IDomEditor, IEditorConfig, IToolbarConfig } from '@wangeditor/editor';
import '@wangeditor/editor/dist/css/style.css';

export interface WangEditorProps {
  value?: string;
  onChange?: (html: string) => void;
  placeholder?: string;
  height?: number;
  disabled?: boolean;
  /** 为 false 时不挂载编辑器（用于 Drawer/Tab 未展示时，避免 host window 报错） */
  active?: boolean;
  uploadImage?: (file: File) => Promise<{ url: string; alt?: string; href?: string }>;
}

export default function WangEditorClient({
  value = '',
  onChange,
  placeholder = '请输入内容...',
  height = 280,
  disabled = false,
  active = true,
  uploadImage,
}: WangEditorProps) {
  const [editor, setEditor] = useState<IDomEditor | null>(null);
  const [editorReady, setEditorReady] = useState(false);
  /** 父组件同步 value 时跳过 onChange，避免 setHtml 触发回写（尤其会破坏 table 等标签） */
  const syncingFromParentRef = useRef(false);

  const toolbarConfig: Partial<IToolbarConfig> = {
    excludeKeys: [
      'group-video',
      'insertVideo',
      'uploadVideo',
      'fullScreen',
    ],
  };

  const editorConfig: Partial<IEditorConfig> = useMemo(() => {
    const base: Partial<IEditorConfig> = {
      placeholder,
      readOnly: false,
      autoFocus: false,
      scroll: true,
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
          async customUpload(file: File, insertFn: (url: string, alt?: string, href?: string) => void) {
            const result = await uploadImage(file);
            insertFn(result.url, result.alt || file.name, result.href || result.url);
          },
        },
      },
    };
  }, [placeholder, uploadImage]);

  const handleChange = useCallback(
    (ed: IDomEditor) => {
      if (syncingFromParentRef.current) return;
      onChange?.(ed.getHtml());
    },
    [onChange],
  );

  const handleCreated = useCallback((ed: IDomEditor) => {
    setEditor(ed);
  }, []);

  // Drawer/Tab 展示后再挂载，避免「Unable to find a host window element」
  useEffect(() => {
    if (!active) {
      setEditorReady(false);
      return;
    }
    const frame = requestAnimationFrame(() => setEditorReady(true));
    return () => cancelAnimationFrame(frame);
  }, [active]);

  useEffect(() => {
    if (active) return;
    if (editor == null) return;
    try {
      editor.destroy();
    } catch {
      /* ignore */
    }
    setEditor(null);
  }, [active, editor]);

  // 父级 value 变化时同步到编辑器（大段 OCR HTML 初次加载 / 切换章节）
  useEffect(() => {
    if (!editor || !editorReady) return;
    const next = value ?? '';
    try {
      if (editor.getHtml() !== next) {
        syncingFromParentRef.current = true;
        editor.setHtml(next || '<p><br></p>');
        queueMicrotask(() => {
          syncingFromParentRef.current = false;
        });
      }
    } catch {
      /* 可能已 destroy */
    }
  }, [value, editor, editorReady]);

  useEffect(() => {
    if (!editor) return;
    if (disabled) {
      editor.disable();
    } else {
      editor.enable();
    }
  }, [editor, disabled]);

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
    <div style={{ border: '1px solid #d9d9d9', borderRadius: 6, overflow: 'hidden', background: '#fff' }}>
      <Toolbar
        editor={editor}
        defaultConfig={toolbarConfig}
        mode="default"
        style={{ borderBottom: '1px solid #f0f0f0' }}
      />
      {editorReady ? (
        <Editor
          defaultConfig={editorConfig}
          defaultHtml={value || '<p><br></p>'}
          onCreated={handleCreated}
          onChange={handleChange}
          mode="default"
          style={{ height, overflowY: 'hidden' }}
        />
      ) : (
        <div style={{ height, background: '#fafafa' }} />
      )}
    </div>
  );
}
