'use client';

import React, { type ReactNode } from 'react';
import { InlineMath, BlockMath } from 'react-katex';
import 'katex/dist/katex.min.css';
import MarkdownRenderer from '@/components/MarkdownRenderer';
import { splitContentWithLatex } from '@/lib/latexUtils';
import { repairMarkdownTables } from '@/lib/markdownTableUtils';

type MarkdownRendererProps = React.ComponentProps<typeof MarkdownRenderer>;

/**
 * 在 Markdown 预览中穿插渲染 $...$ / $$...$$（KaTeX）。
 * 非公式片段仍走 MarkdownRenderer（GFM、HTML、图片等）。
 */
export default function MathAwareMarkdown(props: MarkdownRendererProps) {
  const { content, ...rest } = props;
  const text = repairMarkdownTables(typeof content === 'string' ? content : JSON.stringify(content ?? ''));
  const parts = splitContentWithLatex(text);

  if (parts.length === 1 && parts[0]!.type === 'text') {
    return <MarkdownRenderer content={parts[0]!.content} {...rest} />;
  }

  return (
    <div className={rest.className}>
      {parts.map((part, idx) => {
        if (part.type === 'text') {
          if (!part.content.trim()) return null;
          return <MarkdownRenderer key={idx} content={part.content} {...rest} className={undefined} />;
        }
        const math = part.content;
        if (!math) return null;
        try {
          return part.display ? (
            <div key={idx} style={{ margin: '8px 0', textAlign: 'center', overflowX: 'auto' }}>
              <BlockMath math={math} />
            </div>
          ) : (
            <span key={idx} style={{ display: 'inline' }}>
              <InlineMath math={math} />
            </span>
          );
        } catch {
          return (
            <code key={idx} style={{ color: '#cf1322', fontSize: 12 }}>
              ${math}$
            </code>
          );
        }
      })}
    </div>
  );
}
