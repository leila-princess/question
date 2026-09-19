'use client';

import React, { useCallback, useMemo } from 'react';
import { Input } from 'antd';
import type { TextAreaProps } from 'antd/es/input';
import MixedContent from '@/components/ThreeColumnLayout/MixedContent';
import { contentHasLatex, formatLatexInPlainText } from '@/lib/latexUtils';
import 'katex/dist/katex.min.css';

export type MathAwareTextAreaProps = TextAreaProps & {
  /** 检测到公式时显示预览区，默认 true */
  showPreview?: boolean;
};

/**
 * 支持 LaTeX 公式预览与失焦自动规范化的 TextArea。
 * 公式写法：行内 `$...$`，块级 `$$...$$` 或 ```latex 围栏。
 */
export default function MathAwareTextArea({
  value,
  onChange,
  onBlur,
  showPreview = true,
  ...rest
}: MathAwareTextAreaProps) {
  const str = typeof value === 'string' ? value : '';
  const previewText = useMemo(() => formatLatexInPlainText(str), [str]);
  const hasLatex = contentHasLatex(str);

  const handleBlur = useCallback(
    (e: React.FocusEvent<HTMLTextAreaElement>) => {
      const formatted = formatLatexInPlainText(e.target.value);
      if (formatted !== e.target.value) {
        onChange?.({
          ...e,
          target: { ...e.target, value: formatted },
          currentTarget: { ...e.currentTarget, value: formatted },
        });
      }
      onBlur?.(e);
    },
    [onChange, onBlur],
  );

  return (
    <div className="math-aware-textarea">
      <Input.TextArea value={value} onChange={onChange} onBlur={handleBlur} {...rest} />
      {showPreview && hasLatex && previewText.trim() ? (
        <div
          style={{
            marginTop: 8,
            padding: '8px 12px',
            background: '#fafafa',
            border: '1px solid #f0f0f0',
            borderRadius: 6,
            fontSize: 14,
            lineHeight: 1.6,
          }}
        >
          <div style={{ fontSize: 12, color: '#888', marginBottom: 4 }}>公式预览</div>
          <MixedContent content={previewText} />
        </div>
      ) : null}
    </div>
  );
}
