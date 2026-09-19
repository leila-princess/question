'use client';

import React, { useCallback, useMemo } from 'react';
import WangEditor from '@/components/WangEditor';
import MixedContent from '@/components/ThreeColumnLayout/MixedContent';
import { contentHasLatex, formatLatexInHtmlContent } from '@/lib/latexUtils';
import type { WangEditorProps } from '@/components/WangEditor';
import 'katex/dist/katex.min.css';

export type RichTextLatexEditorProps = WangEditorProps;

/**
 * 富文本编辑器 + LaTeX 公式预览；失焦时自动规范化 HTML 中的公式。
 */
export default function RichTextLatexEditor({
  value = '',
  onChange,
  placeholder = '请输入内容…（公式可用 $...$ 或 $$...$$）',
  ...rest
}: RichTextLatexEditorProps) {
  const previewContent = useMemo(() => formatLatexInHtmlContent(value), [value]);
  const showLatexPreview = contentHasLatex(value);

  const handleBlur = useCallback(() => {
    const formatted = formatLatexInHtmlContent(value);
    if (formatted !== value) {
      onChange?.(formatted);
    }
  }, [onChange, value]);

  return (
    <div className="rich-text-latex-editor" onBlur={handleBlur}>
      <WangEditor value={value} onChange={onChange} placeholder={placeholder} {...rest} />
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
    </div>
  );
}
