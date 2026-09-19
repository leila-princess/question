"use client";

import React from "react";
import { Typography } from "antd";
import { InlineMath, BlockMath } from "react-katex";
import { normalizeLatexSegment } from "@/lib/latexUtils";
import { enrichHtmlImagesWithDownloadLinks } from "./environmentMedia";

const { Text } = Typography;

import { unescapeEnvironmentNewlines } from "./environmentMedia";

/** 将换行转为 <br/>；含 HTML 时仅处理标签外的文本片段 */
function normalizeNewlinesToHtml(raw: string): string {
  const text = unescapeEnvironmentNewlines(raw);
  if (!text) return "";
  if (!/<[a-z][\s\S]*>/i.test(text)) {
    return text
      .split("\n")
      .map((line) => line.trim())
      .filter(Boolean)
      .join("<br/>");
  }
  return text
    .split(/(<[^>]+>)/g)
    .map((part) => {
      if (part.startsWith("<")) return part;
      return part
        .split("\n")
        .map((line) => line.trim())
        .filter(Boolean)
        .join("<br/>");
    })
    .join("");
}

function addImageErrorHandlers(html: string): string {
  if (!html || !/<img/i.test(html)) return html;
  return html.replace(
    /<img\s+([^>]*?)src="([^"]*)"([^>]*)\/?>/gi,
    (match, before, src, after) => {
      if (match.includes('onerror=')) return match;
      return `<img ${before}src="${src}"${after} onerror="this.onerror=null;this.alt='[图片加载失败]';this.style.opacity='0.4';this.style.maxWidth='200px';">`;
    }
  );
}

export default function MixedContent({
  content,
  enableImageDownload = false,
}: {
  content: string;
  enableImageDownload?: boolean;
}) {
  if (!content) return <Text type="secondary">无数据</Text>;

  const finalizeHtml = (html: string) => {
    let out = addImageErrorHandlers(normalizeNewlinesToHtml(html));
    if (enableImageDownload) out = enrichHtmlImagesWithDownloadLinks(out);
    return out;
  };

  const segments: Array<{ start: number; end: number; type: 'latex' }> = [];
  let regexMatch: RegExpExecArray | null;

  const blockLatexRe = /\$\$([\s\S]*?)\$\$/g;
  while ((regexMatch = blockLatexRe.exec(content)) !== null) {
    segments.push({ start: regexMatch.index, end: regexMatch.index + regexMatch[0].length, type: 'latex' });
  }

  const inlineLatexRe = /\$([^$\n]+?)\$/g;
  while ((regexMatch = inlineLatexRe.exec(content)) !== null) {
    if (!segments.some(s => regexMatch!.index >= s.start && regexMatch!.index < s.end)) {
      segments.push({ start: regexMatch.index, end: regexMatch.index + regexMatch[0].length, type: 'latex' });
    }
  }
  segments.sort((a, b) => a.start - b.start);

  if (segments.length === 0) {
    const normalizedHtml = finalizeHtml(content);
    return <div className="mixed-content-html" dangerouslySetInnerHTML={{ __html: normalizedHtml }} />;
  }

  const parts: Array<{ type: 'html' | 'latex'; content: string }> = [];
  let pos = 0;
  for (const seg of segments) {
    if (seg.start > pos) {
      const text = content.slice(pos, seg.start).trim();
      if (text) parts.push({ type: 'html', content: text });
    }
    parts.push({ type: 'latex', content: content.slice(seg.start, seg.end) });
    pos = seg.end;
  }
  const tail = content.slice(pos).trim();
  if (tail) parts.push({ type: 'html', content: tail });

  return (
    <div style={{ fontSize: 14, lineHeight: 1.6 }}>
      {parts.map((part, idx) => {
        if (part.type === 'html') {
          const normalized = finalizeHtml(part.content);
          const isBlock = /<(div|p|table|h[1-6]|ul|ol|li|br|figure)/i.test(normalized);
          const isTable = /<table/i.test(normalized);
          // @ts-ignore
          const Tag = isBlock ? 'div' : 'span';
          return <Tag key={idx} className={isTable ? 'html-table-wrapper' : 'mixed-content-html'} dangerouslySetInnerHTML={{ __html: normalized }} style={isBlock ? { margin: '4px 0' } : {}} />;
        }
        if (part.type === 'latex') {
          const isBlock = part.content.startsWith('$$');
          const mathStr = normalizeLatexSegment(
            isBlock ? part.content.slice(2, -2).trim() : part.content.slice(1, -1).trim(),
          );
          try {
            return isBlock 
              ? <div key={idx} style={{ margin: '8px 0', textAlign: 'center' }}><BlockMath math={mathStr} /></div>
              : <InlineMath key={idx} math={mathStr} />;
          } catch {
            return <span key={idx} style={{ color: '#ff4d4f', fontSize: 12 }}>{part.content}</span>;
          }
        }
      })}
    </div>
  );
}
