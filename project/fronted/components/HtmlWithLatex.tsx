'use client';

import React, { forwardRef, useMemo, type CSSProperties } from 'react';
import 'katex/dist/katex.min.css';
import { renderLatexInHtml } from '@/lib/latexHtmlRender';

export interface HtmlWithLatexProps {
  html: string;
  className?: string;
  style?: CSSProperties;
}

/** 渲染含 LaTeX 的 HTML（$...$ / $$...$$ 及 latex 代码块） */
const HtmlWithLatex = forwardRef<HTMLDivElement, HtmlWithLatexProps>(function HtmlWithLatex(
  { html, className, style },
  ref,
) {
  const rendered = useMemo(() => renderLatexInHtml(html), [html]);

  return (
    <>
      <style>{`
        .html-with-latex .katex-display { margin: 8px 0; overflow-x: auto; text-align: center; }
        .html-with-latex .katex { font-size: 1.05em; }
      `}</style>
      <div
        ref={ref}
        className={className ? `html-with-latex ${className}` : 'html-with-latex'}
        style={style}
        dangerouslySetInnerHTML={{ __html: rendered }}
      />
    </>
  );
});

export default HtmlWithLatex;
