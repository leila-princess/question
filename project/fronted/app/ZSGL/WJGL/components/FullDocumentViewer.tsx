'use client';

import React, { useEffect, useRef } from 'react';
import { Spin } from 'antd';
import HtmlWithLatex from '@/components/HtmlWithLatex';
import { scrollToSectionAnchor, setupDocumentScrollSpy } from './documentTocUtils';

interface FullDocumentViewerProps {
  html: string;
  loading?: boolean;
  scrollToSectionNo?: string;
  activeSectionNo?: string;
  height?: number;
  onActiveSectionNo?: (sectionNo: string) => void;
}

export default function FullDocumentViewer({
  html,
  loading,
  scrollToSectionNo,
  activeSectionNo,
  height = 480,
  onActiveSectionNo,
}: FullDocumentViewerProps) {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!scrollToSectionNo || !html) return;
    const t = window.setTimeout(() => {
      const el = containerRef.current;
      if (!el) return;
      scrollToSectionAnchor(el, scrollToSectionNo, el);
    }, 120);
    return () => window.clearTimeout(t);
  }, [scrollToSectionNo, html]);

  useEffect(() => {
    if (!onActiveSectionNo) return undefined;
    let cleanup = () => {};
    const timer = window.setTimeout(() => {
      const el = containerRef.current;
      if (!el) return;
      cleanup = setupDocumentScrollSpy(el, onActiveSectionNo);
    }, 120);
    return () => {
      window.clearTimeout(timer);
      cleanup();
    };
  }, [html, onActiveSectionNo]);

  useEffect(() => {
    const root = containerRef.current;
    if (!root || !activeSectionNo) return;
    root.querySelectorAll('.wjgl-section-heading.wjgl-toc-active').forEach((node) => {
      node.classList.remove('wjgl-toc-active');
    });
    const variants = [activeSectionNo, activeSectionNo.replace(/^\./, '')];
    for (const sn of variants) {
      const el = root.querySelector(
        `.wjgl-section-heading[data-section-no="${CSS.escape(sn)}"]`,
      ) as HTMLElement | null;
      if (el) {
        el.classList.add('wjgl-toc-active');
        break;
      }
    }
  }, [activeSectionNo, html]);

  if (loading) {
    return (
      <div style={{ height, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <Spin tip="加载全文…" />
      </div>
    );
  }

  if (!html) {
    return (
      <div style={{ height, color: '#999', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        暂无正文
      </div>
    );
  }

  return (
    <>
      <style>{`
        .wjgl-full-doc .wjgl-section-heading {
          scroll-margin-top: 12px;
          padding: 6px 0 4px;
          margin: 12px 0 6px;
          font-size: 15px;
          font-weight: 600;
          border-bottom: 1px solid #f0f0f0;
        }
        .wjgl-full-doc .wjgl-section-highlight {
          background: #fffbe6;
          outline: 2px solid #ffe58f;
          border-radius: 4px;
        }
        .wjgl-full-doc .wjgl-section-heading.wjgl-toc-active {
          background: #fffbe6;
          outline: 2px solid #ffe58f;
          border-radius: 4px;
        }
        .wjgl-full-doc p { margin: 0 0 8px; line-height: 1.65; }
        .wjgl-full-doc .doc-table { overflow-x: auto; margin: 8px 0; }
        .wjgl-full-doc table { border-collapse: collapse; width: 100%; margin: 8px 0; }
        .wjgl-full-doc td, .wjgl-full-doc th { border: 1px solid #ddd; padding: 4px 8px; vertical-align: top; }
        .wjgl-full-doc figure { margin: 12px 0; text-align: center; }
        .wjgl-full-doc img { max-width: 100%; height: auto; display: block; margin: 0 auto; }
      `}</style>
      <HtmlWithLatex
        ref={containerRef}
        html={html}
        className="wjgl-full-doc"
        style={{
          height,
          overflow: 'auto',
          padding: '12px 16px',
          background: '#fff',
          border: '1px solid #f0f0f0',
          borderRadius: 6,
          fontSize: 14,
        }}
      />
    </>
  );
}
