'use client';

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Image, Alert } from 'antd';
import WangEditor from '@/components/WangEditor';
import MixedContent from '@/components/ThreeColumnLayout/MixedContent';
import { contentHasLatex, formatLatexInHtmlContent } from '@/lib/latexUtils';
import 'katex/dist/katex.min.css';
import {
  normalizeSynrForWangEditor,
  normalizeSynrFromWangEditor,
  WANGEDITOR_SYNR_STYLES,
} from '../utils/synrHtmlUtils';

export interface MethodSynrEditorProps {
  value?: string;
  onChange?: (html: string) => void;
  height?: number;
  /** 为 false 时延迟挂载 WangEditor（Drawer/Tab 未激活时） */
  active?: boolean;
  uploadImage?: (file: File) => Promise<{ url: string; alt?: string; href?: string }>;
}

/**
 * 方法内容富文本：WangEditor（与环境 tab 抽屉一致），支持表格编辑、图片上传 MinIO、点击预览图片。
 */
export default function MethodSynrEditor({
  value = '',
  onChange,
  height = 360,
  active = true,
  uploadImage,
}: MethodSynrEditorProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [previewSrc, setPreviewSrc] = useState('');

  const editorValue = useMemo(() => normalizeSynrForWangEditor(value), [value]);
  const previewContent = useMemo(() => formatLatexInHtmlContent(editorValue), [editorValue]);
  const showLatexPreview = contentHasLatex(editorValue);

  const handleChange = useCallback(
    (html: string) => {
      onChange?.(normalizeSynrFromWangEditor(html));
    },
    [onChange],
  );

  const handleBlur = useCallback(() => {
    const formatted = formatLatexInHtmlContent(editorValue);
    const normalized = normalizeSynrFromWangEditor(formatted);
    const current = normalizeSynrFromWangEditor(editorValue);
    if (normalized !== current) {
      onChange?.(normalized);
    }
  }, [editorValue, onChange]);

  useEffect(() => {
    const root = containerRef.current;
    if (!root) return;
    const onClick = (e: MouseEvent) => {
      const target = e.target as HTMLElement;
      if (target?.tagName !== 'IMG') return;
      const src = (target as HTMLImageElement).currentSrc || (target as HTMLImageElement).src;
      if (src) setPreviewSrc(src);
    };
    root.addEventListener('click', onClick);
    return () => root.removeEventListener('click', onClick);
  }, []);

  return (
    <div ref={containerRef} className="flgl-method-synr-editor" style={{ maxWidth: '100%' }} onBlur={handleBlur}>
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 8 }}
        message="图片请手动插入；保存前请检查方法内容、格式与公式显示是否正常。"
      />
      <style>{WANGEDITOR_SYNR_STYLES}</style>
      <WangEditor
        value={editorValue}
        onChange={handleChange}
        uploadImage={uploadImage}
        height={height}
        active={active}
        placeholder="请输入方法内容…（公式可用 $...$ 或 $$...$$）"
      />
      {showLatexPreview ? (
        <div
          style={{
            marginTop: 8,
            padding: '8px 12px',
            background: '#fafafa',
            border: '1px solid #f0f0f0',
            borderRadius: 6,
          }}
        >
          <div style={{ fontSize: 12, color: '#888', marginBottom: 4 }}>公式预览</div>
          <MixedContent content={previewContent} />
        </div>
      ) : null}
      {previewSrc ? (
        <Image
          wrapperStyle={{ display: 'none' }}
          src={previewSrc}
          preview={{
            visible: true,
            src: previewSrc,
            onVisibleChange: (visible) => {
              if (!visible) setPreviewSrc('');
            },
          }}
        />
      ) : null}
    </div>
  );
}
