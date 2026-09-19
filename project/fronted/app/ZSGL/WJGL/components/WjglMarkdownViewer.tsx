'use client';

import React, { useEffect, useMemo, useRef, type CSSProperties, type ReactNode } from 'react';
import ReactMarkdown, { type Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeRaw from 'rehype-raw';
import { InlineMath, BlockMath } from 'react-katex';
import 'katex/dist/katex.min.css';
import { splitContentWithLatex } from '@/lib/latexUtils';
import { scrollToSectionAnchor } from './documentTocUtils';
import {
  flattenReactText,
  parseWjglSectionHeading,
  rewriteWjglMarkdownAssets,
  wjglSectionAnchorId,
} from './wjglMarkdownUtils';

interface WjglMarkdownViewerProps {
  markdown: string;
  docId?: string;
  category?: string;
  height?: number;
  scrollToSectionNo?: string;
}

const HEADING_STYLE: CSSProperties = {
  scrollMarginTop: 12,
  padding: '6px 0 4px',
  margin: '12px 0 6px',
  fontWeight: 600,
  borderBottom: '1px solid #f0f0f0',
};

function makeHeading(level: 1 | 2 | 3 | 4 | 5 | 6) {
  const Tag = `h${level}` as keyof React.JSX.IntrinsicElements;
  const fontSize = level <= 2 ? 15 : level === 3 ? 14 : 13;
  return ({ children, ...props }: { children?: ReactNode; [k: string]: unknown }) => {
    const text = flattenReactText(children);
    const parsed = parseWjglSectionHeading(text);
    if (parsed) {
      const id = wjglSectionAnchorId(parsed.sectionNo);
      return (
        <Tag
          {...(props as object)}
          id={id}
          className="wjgl-section-heading"
          data-section-no={parsed.sectionNo}
          style={{ ...HEADING_STYLE, fontSize }}
        >
          {children}
        </Tag>
      );
    }
    return (
      <Tag {...(props as object)} style={{ ...HEADING_STYLE, fontSize }}>
        {children}
      </Tag>
    );
  };
}

function WjglMarkdownBlock({ content, components }: { content: string; components: Components }) {
  if (!content.trim()) return null;
  return (
    <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeRaw]} components={components}>
      {content}
    </ReactMarkdown>
  );
}

export default function WjglMarkdownViewer({
  markdown,
  docId,
  category,
  height = 480,
  scrollToSectionNo,
}: WjglMarkdownViewerProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const content = useMemo(
    () => (docId && category ? rewriteWjglMarkdownAssets(markdown, docId, category) : markdown),
    [markdown, docId, category],
  );

  const parts = useMemo(() => splitContentWithLatex(content), [content]);

  const components = useMemo(
    () =>
      ({
        h1: makeHeading(1),
        h2: makeHeading(2),
        h3: makeHeading(3),
        h4: makeHeading(4),
        h5: makeHeading(5),
        h6: makeHeading(6),
        p({ children, ...props }: { children?: ReactNode; [k: string]: unknown }) {
          return (
            <p {...(props as object)} style={{ margin: '0 0 8px', lineHeight: 1.65 }}>
              {children}
            </p>
          );
        },
        table({ children, ...props }: { children?: ReactNode; [k: string]: unknown }) {
          return (
            <div className="doc-table" style={{ overflowX: 'auto', margin: '8px 0' }}>
              <table
                {...(props as object)}
                style={{ borderCollapse: 'collapse', width: '100%', margin: '8px 0' }}
              >
                {children}
              </table>
            </div>
          );
        },
        th({ children, ...props }: { children?: ReactNode; [k: string]: unknown }) {
          return (
            <th
              {...(props as object)}
              style={{
                border: '1px solid #ddd',
                padding: '4px 8px',
                verticalAlign: 'top',
                background: '#fafafa',
                fontWeight: 600,
              }}
            >
              {children}
            </th>
          );
        },
        td({ children, ...props }: { children?: ReactNode; [k: string]: unknown }) {
          return (
            <td
              {...(props as object)}
              style={{ border: '1px solid #ddd', padding: '4px 8px', verticalAlign: 'top' }}
            >
              {children}
            </td>
          );
        },
        img({ src, alt, ...props }: { src?: string; alt?: string; [k: string]: unknown }) {
          return (
            <figure style={{ margin: '12px 0', textAlign: 'center' }}>
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                {...(props as object)}
                src={src || ''}
                alt={alt || ''}
                style={{ maxWidth: '100%', height: 'auto', display: 'block', margin: '0 auto' }}
              />
            </figure>
          );
        },
      }) as Components,
    [],
  );

  useEffect(() => {
    if (!scrollToSectionNo || !content) return;
    const t = window.setTimeout(() => {
      const el = containerRef.current;
      if (!el) return;
      scrollToSectionAnchor(el, scrollToSectionNo, el);
    }, 120);
    return () => window.clearTimeout(t);
  }, [scrollToSectionNo, content]);

  return (
    <>
      <style>{`
        .wjgl-markdown-doc .wjgl-section-highlight {
          background: #fffbe6;
          outline: 2px solid #ffe58f;
          border-radius: 4px;
        }
      `}</style>
      <div
        ref={containerRef}
        className="wjgl-markdown-doc wjgl-full-doc"
        style={{
          height,
          overflow: 'auto',
          padding: '12px 16px',
          background: '#fff',
          border: '1px solid #f0f0f0',
          borderRadius: 6,
          fontSize: 14,
          color: '#333',
        }}
      >
        {parts.length === 1 && parts[0]!.type === 'text' ? (
          <WjglMarkdownBlock content={parts[0]!.content} components={components} />
        ) : (
          parts.map((part, idx) => {
            if (part.type === 'text') {
              return <WjglMarkdownBlock key={idx} content={part.content} components={components} />;
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
          })
        )}
      </div>
    </>
  );
}
