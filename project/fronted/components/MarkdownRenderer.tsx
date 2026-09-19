// src/components/MarkdownRenderer.tsx
import React, { type CSSProperties, type ReactNode, useCallback } from 'react';
import ReactMarkdown, { Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeRaw from 'rehype-raw';
import rehypeHighlight from 'rehype-highlight';
import { DownloadOutlined } from '@ant-design/icons';
import { Button } from 'antd';
import 'highlight.js/styles/github.css';
import { repairMarkdownTables } from '@/lib/markdownTableUtils';

interface MarkdownRendererProps {
  content: string;
  className?: string;
  /** 为 true 时，Markdown 四级及以下标题（h4–h6）使用相同字号与字重，便于方案深层小节视觉一致 */
  schemeSubHeadingsUniform?: boolean;
  /**
   * 为 true 时允许 Markdown 内嵌 HTML（如 <u> 下划线），仅用于可信内容（如本页方案编辑区预览）。
   * rehype-raw 在 rehype-highlight 之前执行。
   */
  allowHtml?: boolean;
  /** 与文档中标题出现顺序一一对应的 id，用于目录锚点滚动 */
  docHeadingIds?: string[];
  /** 为 true 时在图片下方显示「下载图片」按钮 */
  enableImageDownload?: boolean;
}

const schemeSubHeadingStyle: CSSProperties = {
  fontSize: 15,
  fontWeight: 600,
  lineHeight: 1.45,
  marginTop: 14,
  marginBottom: 8,
  color: '#1f1f1f',
};

function MarkdownImageBlock({
  src,
  alt,
  enableDownload,
  ...props
}: {
  src?: string;
  alt?: string;
  enableDownload?: boolean;
  [k: string]: unknown;
}) {
  const handleDownload = useCallback(async () => {
    if (!src) return;
    const fallbackName = decodeURIComponent((src.split('/').pop() || 'image').split('?')[0] || 'image');
    try {
      const res = await fetch(src);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const blob = await res.blob();
      const objectUrl = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = objectUrl;
      anchor.download = fallbackName;
      anchor.rel = 'noopener';
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(objectUrl);
    } catch {
      window.open(src, '_blank', 'noopener,noreferrer');
    }
  }, [src]);

  return (
    <figure style={{ margin: '12px 0' }}>
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        {...(props as object)}
        src={src || ''}
        alt={alt || ''}
        style={{
          maxWidth: '100%',
          maxHeight: 360,
          height: 'auto',
          objectFit: 'contain',
          display: 'block',
          borderRadius: 6,
        }}
      />
      {enableDownload && src ? (
        <figcaption style={{ marginTop: 6 }}>
          <Button
            type="link"
            size="small"
            icon={<DownloadOutlined />}
            style={{ padding: 0, height: 'auto', fontSize: 13 }}
            onClick={() => void handleDownload()}
          >
            下载图片
          </Button>
        </figcaption>
      ) : null}
    </figure>
  );
}

const MarkdownRenderer = ({
  content,
  className,
  schemeSubHeadingsUniform,
  allowHtml,
  docHeadingIds,
  enableImageDownload,
}: MarkdownRendererProps) => {
  const contentToRender = repairMarkdownTables(
    typeof content === 'string' ? content : JSON.stringify(content),
  );

  let headingPos = 0;

  const makeHeading = (level: 1 | 2 | 3 | 4 | 5 | 6) => {
    const Tag = `h${level}` as keyof React.JSX.IntrinsicElements;
    const uniform = Boolean(schemeSubHeadingsUniform && level >= 4);
    return ({ children, ...props }: { children?: ReactNode; [k: string]: unknown }) => {
      const id =
        docHeadingIds && headingPos < docHeadingIds.length ? docHeadingIds[headingPos++] : undefined;
      const style: CSSProperties = {
        ...(uniform ? schemeSubHeadingStyle : {}),
        ...((props as { style?: CSSProperties }).style || {}),
      };
      return React.createElement(Tag, { ...(props as object), id, style }, children);
    };
  };

  const rehypePlugins = allowHtml ? [rehypeRaw, rehypeHighlight] : [rehypeHighlight];

  headingPos = 0;

  return (
    <div className={className}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={rehypePlugins}
        components={
          {
            h1: makeHeading(1),
            h2: makeHeading(2),
            h3: makeHeading(3),
            h4: makeHeading(4),
            h5: makeHeading(5),
            h6: makeHeading(6),
            code({ node, className: cn, children, ...props }: {
              node?: unknown;
              className?: string;
              children?: React.ReactNode;
              [key: string]: unknown;
            }) {
              const match = /language-(\w+)/.exec(cn || '');
              const isInline = (props as { inline?: boolean }).inline || false;

              return !isInline ? (
                <pre
                  style={{
                    background: '#f6f8fa',
                    padding: '16px',
                    borderRadius: '6px',
                    overflow: 'auto',
                    margin: '16px 0',
                  }}
                >
                  <code className={cn} {...props}>
                    {children}
                  </code>
                </pre>
              ) : (
                <code className={cn} {...props}>
                  {children}
                </code>
              );
            },

            img({ node, src, alt, title, ...props }: { node?: unknown; src?: string; alt?: string; title?: string; [k: string]: unknown }) {
              return (
                <MarkdownImageBlock
                  {...props}
                  src={src}
                  alt={alt || title || '图片'}
                  enableDownload={enableImageDownload}
                />
              );
            },
            a({ node, href, children, ...props }: { node?: unknown; href?: string; children?: ReactNode; [k: string]: unknown }) {
              const childText = typeof children === 'string' ? children : '';
              const isDownloadLink = childText.includes('下载图片') || childText.includes('下载');
              if (isDownloadLink && href) {
                return (
                  <Button
                    type="link"
                    size="small"
                    icon={<DownloadOutlined />}
                    style={{ padding: 0, height: 'auto', fontSize: 13, marginLeft: 4 }}
                    onClick={async (e) => {
                      e.preventDefault();
                      const fallbackName = decodeURIComponent((href.split('/').pop() || 'image').split('?')[0] || 'image');
                      try {
                        const res = await fetch(href);
                        if (!res.ok) throw new Error(`HTTP ${res.status}`);
                        const blob = await res.blob();
                        const objectUrl = URL.createObjectURL(blob);
                        const anchor = document.createElement('a');
                        anchor.href = objectUrl;
                        anchor.download = fallbackName;
                        anchor.rel = 'noopener';
                        document.body.appendChild(anchor);
                        anchor.click();
                        anchor.remove();
                        URL.revokeObjectURL(objectUrl);
                      } catch {
                        window.open(href, '_blank', 'noopener,noreferrer');
                      }
                    }}
                  >
                    {children}
                  </Button>
                );
              }
              return (
                <a href={href} {...props} target="_blank" rel="noopener noreferrer">
                  {children}
                </a>
              );
            },
            table({ node, children, ...props }) {
              return (
                <div style={{ overflowX: 'auto', margin: '16px 0' }}>
                  <table
                    style={{
                      width: '100%',
                      borderCollapse: 'collapse',
                      border: '1px solid #e0e0e0',
                    }}
                    {...props}
                  >
                    {children}
                  </table>
                </div>
              );
            },
            th({ node, children, ...props }) {
              return (
                <th
                  style={{
                    padding: '10px 12px',
                    border: '1px solid #e0e0e0',
                    textAlign: 'left',
                    fontWeight: 600,
                    backgroundColor: '#f8f8f8',
                  }}
                  {...props}
                >
                  {children}
                </th>
              );
            },
            td({ node, children, ...props }) {
              return (
                <td
                  style={{
                    padding: '10px 12px',
                    border: '1px solid #e0e0e0',
                    verticalAlign: 'top',
                  }}
                  {...props}
                >
                  {children}
                </td>
              );
            },
            u({ children, ...props }: { children?: ReactNode; [k: string]: unknown }) {
              return (
                <u style={{ textDecoration: 'underline' }} {...props}>
                  {children}
                </u>
              );
            },
          } as Components
        }
      >
        {contentToRender}
      </ReactMarkdown>
    </div>
  );
};

export default MarkdownRenderer;
