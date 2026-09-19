'use client';

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Segmented, Spin } from 'antd';
import FullDocumentViewer from './FullDocumentViewer';
import WjglMarkdownViewer from './WjglMarkdownViewer';
import WjglDocumentFormatToolbar from './WjglDocumentFormatToolbar';
import { scrollToSectionAnchor } from './documentTocUtils';
import {
  filterGarbledHtml,
  filterGarbledMarkdown,
  hasMarkdownContent,
  htmlNeedsMarkdownRender,
  markdownToWjglHtml,
} from './wjglMarkdownUtils';
import { formatLatexInHtmlContent } from '@/lib/latexUtils';

interface FullDocumentEditorProps {
  html: string;
  markdown?: string;
  docId?: string;
  category?: string;
  loading?: boolean;
  scrollToSectionNo?: string;
  activeSectionNo?: string;
  height?: number;
  onChange?: (html: string) => void;
  onActiveSectionNo?: (sectionNo: string) => void;
  /** auto=有 MD 时可能用 MD 渲染；html=始终用 MinerU/解析 HTML */
  contentMode?: 'auto' | 'html';
  defaultMode?: 'preview' | 'edit';
  showModeSwitch?: boolean;
}

export function hasDocumentHtmlContent(html: string): boolean {
  if (!html) return false;
  const text = html.replace(/<[^>]+>/g, '').replace(/&nbsp;/gi, ' ').trim();
  if (text.length > 0) return true;
  return /<img[\s>]/i.test(html) || /<table[\s>]/i.test(html);
}

const DOC_STYLES = `
  .wjgl-doc-editable {
    outline: none;
    font-size: 14px;
    line-height: 1.65;
    color: #333;
  }
  .wjgl-doc-editable .wjgl-section-heading {
    scroll-margin-top: 12px;
    font-weight: 600;
    margin: 12px 0 6px;
    padding: 6px 0 4px;
    border-bottom: 1px solid #f0f0f0;
  }
  .wjgl-doc-editable h1, .wjgl-doc-editable h2, .wjgl-doc-editable h3,
  .wjgl-doc-editable h4, .wjgl-doc-editable h5, .wjgl-doc-editable h6 {
    scroll-margin-top: 12px;
    font-weight: 600;
    margin: 12px 0 6px;
  }
  .wjgl-doc-editable .wjgl-section-highlight {
    background: #fffbe6;
    outline: 2px solid #ffe58f;
    border-radius: 4px;
  }
  .wjgl-doc-editable p { margin: 0 0 8px; }
  .wjgl-doc-editable .doc-table { overflow-x: auto; margin: 8px 0; }
  .wjgl-doc-editable table { border-collapse: collapse; width: 100%; margin: 8px 0; }
  .wjgl-doc-editable td, .wjgl-doc-editable th {
    border: 1px solid #ddd;
    padding: 4px 8px;
    vertical-align: top;
  }
  .wjgl-doc-editable figure { margin: 12px 0; text-align: center; }
  .wjgl-doc-editable img { max-width: 100%; height: auto; display: block; margin: 0 auto; }
`;

const DEBOUNCE_MS = 800;

function EditableDocumentBody({
  html,
  height,
  onHtmlChange,
  registerFlush,
}: {
  html: string;
  height: number;
  onHtmlChange: (h: string) => void;
  registerFlush?: (flush: () => void) => () => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const flush = useCallback(() => {
    if (!ref.current) return;
    onHtmlChange(formatLatexInHtmlContent(ref.current.innerHTML));
  }, [onHtmlChange]);

  useEffect(() => {
    if (!registerFlush) return undefined;
    return registerFlush(flush);
  }, [flush, registerFlush]);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (document.activeElement === el) return;
    el.innerHTML = html || '<p><br></p>';
  }, [html]);

  useEffect(
    () => () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    },
    [],
  );

  return (
    <div
      ref={ref}
      className="wjgl-doc-editable wjgl-full-doc"
      contentEditable
      suppressContentEditableWarning
      onBlur={flush}
      onInput={() => {
        if (debounceRef.current) clearTimeout(debounceRef.current);
        debounceRef.current = setTimeout(flush, DEBOUNCE_MS);
      }}
      style={{
        height,
        overflow: 'auto',
        padding: '12px 16px',
        background: '#fff',
        border: '1px solid #f0f0f0',
        borderRadius: 6,
      }}
    />
  );
}

export default function FullDocumentEditor({
  html,
  markdown,
  docId,
  category,
  loading,
  scrollToSectionNo,
  activeSectionNo,
  height = 480,
  onChange,
  onActiveSectionNo,
  contentMode = 'auto',
  defaultMode = 'edit',
  showModeSwitch = true,
}: FullDocumentEditorProps) {
  const hasMarkdown = contentMode === 'auto' && hasMarkdownContent(markdown || '');
  const filteredMarkdown = useMemo(() => filterGarbledMarkdown(markdown || ''), [markdown]);
  const editHtml = useMemo(() => filterGarbledHtml(html || ''), [html]);
  const deriveFromMarkdown = useMemo(
    () => contentMode === 'auto' && hasMarkdown && htmlNeedsMarkdownRender(editHtml, filteredMarkdown),
    [contentMode, hasMarkdown, editHtml, filteredMarkdown],
  );
  const resolvedEditHtml = useMemo(() => {
    if (deriveFromMarkdown) {
      return markdownToWjglHtml(filteredMarkdown, docId, category);
    }
    return editHtml;
  }, [deriveFromMarkdown, filteredMarkdown, editHtml, docId, category]);

  const [mode, setMode] = useState<'preview' | 'edit'>(defaultMode);
  const scrollRef = useRef<HTMLDivElement>(null);
  const flushRef = useRef<(() => void) | null>(null);
  const syncedMdRef = useRef('');

  const registerFlush = useCallback((flush: () => void) => {
    flushRef.current = flush;
    return () => {
      flushRef.current = null;
    };
  }, []);

  const handleHtmlChange = useCallback(
    (next: string) => {
      onChange?.(filterGarbledHtml(formatLatexInHtmlContent(next)));
    },
    [onChange],
  );

  // MinerU 完成后把 MD 渲染结果同步到 fullHtml，便于入库与预览一致
  useEffect(() => {
    if (!onChange || !deriveFromMarkdown || !resolvedEditHtml.trim()) return;
    if (syncedMdRef.current === filteredMarkdown) return;
    syncedMdRef.current = filteredMarkdown;
    onChange(resolvedEditHtml);
  }, [onChange, deriveFromMarkdown, filteredMarkdown, resolvedEditHtml]);

  useEffect(() => {
    if (!scrollToSectionNo || loading) return;
    const timer = window.setTimeout(() => {
      const container = scrollRef.current?.querySelector('.wjgl-full-doc') as HTMLElement | null;
      if (!container) return;
      scrollToSectionAnchor(container, scrollToSectionNo, container);
    }, 120);
    return () => window.clearTimeout(timer);
  }, [scrollToSectionNo, resolvedEditHtml, filteredMarkdown, loading, mode]);

  if (loading) {
    return (
      <div style={{ height, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <Spin tip={loading ? 'MinerU 解析中，请稍候…' : '加载正文预览…'} />
      </div>
    );
  }

  const hasContent =
    contentMode === 'html'
      ? hasDocumentHtmlContent(resolvedEditHtml)
      : hasMarkdown || hasDocumentHtmlContent(resolvedEditHtml);
  if (!hasContent) {
    return (
      <div style={{ height, color: '#999', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        暂无正文
      </div>
    );
  }

  return (
    <div ref={scrollRef} className="wjgl-full-doc-editor">
      <style>{DOC_STYLES}</style>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
        {showModeSwitch ? (
          <Segmented
            size="small"
            value={mode}
            onChange={(v) => {
              const next = v as 'preview' | 'edit';
              if (mode === 'edit' && next === 'preview') {
                try {
                  flushRef.current?.();
                } catch {
                  /* ignore */
                }
              }
              setMode(next);
            }}
            options={[
              { label: '预览', value: 'preview' },
              { label: '编辑', value: 'edit' },
            ]}
          />
        ) : <span />}
      </div>
      {mode === 'preview' ? (
        <FullDocumentViewer
          html={resolvedEditHtml}
          height={height}
          scrollToSectionNo={scrollToSectionNo}
          activeSectionNo={activeSectionNo}
          onActiveSectionNo={onActiveSectionNo}
        />
      ) : (
        <>
          <WjglDocumentFormatToolbar />
          <EditableDocumentBody
            html={resolvedEditHtml}
            height={height}
            onHtmlChange={handleHtmlChange}
            registerFlush={registerFlush}
          />
        </>
      )}
    </div>
  );
}
