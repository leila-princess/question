import type { ReactNode } from 'react';
import { marked } from 'marked';

marked.setOptions({
  gfm: true,
  breaks: true,
});

const SECTION_HEADING_TEXT = /^(\d+(?:[\.．]\d+)*)[\s\.、．]+(.+)$/;

export function normalizeWjglSectionNo(sec: string): string {
  return (sec || '').replace(/．/g, '.').replace(/\s/g, '').trim();
}

export function wjglSectionAnchorId(sectionNo: string): string {
  return `wjgl-sec-${normalizeWjglSectionNo(sectionNo).replace(/\./g, '-')}`;
}

export function parseWjglSectionHeading(text: string): { sectionNo: string; title: string } | null {
  const norm = (text || '').replace(/\u00a0/g, ' ').trim();
  const m = SECTION_HEADING_TEXT.exec(norm);
  if (!m) return null;
  return {
    sectionNo: normalizeWjglSectionNo(m[1]),
    title: m[2].trim(),
  };
}

export function flattenReactText(children: ReactNode): string {
  if (children == null || typeof children === 'boolean') return '';
  if (typeof children === 'string' || typeof children === 'number') return String(children);
  if (Array.isArray(children)) return children.map(flattenReactText).join('');
  if (typeof children === 'object' && 'props' in children) {
    return flattenReactText((children as { props?: { children?: ReactNode } }).props?.children);
  }
  return '';
}

/** 将 MinerU 相对图片路径改写为 WJGL local-asset URL。 */
export function rewriteWjglMarkdownAssets(
  md: string,
  docId: string,
  category: string,
): string {
  if (!md || !docId || !category) return md || '';
  const prefix = `/api/wjgl1/files/${encodeURIComponent(docId)}/local-asset?category=${encodeURIComponent(category)}&path=`;
  return md.replace(
    /!\[([^\]]*)\]\((?!https?:\/\/|\/api\/)([^)]+)\)/g,
    (_full, alt: string, path: string) => {
      const rel = path.trim().replace(/^\.\//, '').replace(/\\/g, '/');
      return `![${alt}](${prefix}${encodeURIComponent(rel)})`;
    },
  );
}

// 符号字体乱码：康熙部首区 + CJK 扩展区 + Latin-1 Symbol 误映射
const SYMBOL_FONT_INLINE_RE = /[\s\u3000]+[\u7680-\u76ff]{2,}(?:[a-zA-Z\u7680-\u76ff\s]*)/g;
const SYMBOL_FONT_LATIN_RE = /[\s\u3000]+[\u7280-\u72ff]{2,}(?:[a-zA-Z\u7280-\u72ff\s]*)/gi;
const CID_RADICAL_RE = /[\u7280-\u72ff]{2,}/g;
const LATIN1_GARBAGE_RE =
  /[\u00C0-\u00D6\u00D8-\u00F6\u00F8-\u00FFÊËÌÍÎÏÐÑÒÓÔÖ×ØÙÚÛÜÝÞßàáâãäåæçèéêëìíîïðñòóôõö÷øùúûüýþÿ]/g;
const PAREN_CAP_RUN_RE = /[A-Z]\([A-Z]{2,}[A-Z()\u00C0-\u00FF]*/g;
const DIGIT_SYMBOL_JUNK_RE =
  /(?:[\$#%&]{2,}|\d{2,}[\\*]|012\*|0178|01;<|sra\]|ra\]|e[0-9]{1,3}e?üý|üý[0-9]|34-5401|[`´]{2,})/gi;
const CAP_GARBAGE_RUN_RE = /(?<![A-Z/])(?<![a-z0-9])[A-Z]{5,}(?![a-z/])/g;

function lineQualityScore(line: string): number {
  const s = (line || '').trim();
  if (!s) return 1;
  if (/^#{1,6}\s+\d/.test(s)) return 0.85;
  if (s.includes('|') && (s.match(/\|/g) || []).length >= 2) return 0.8;
  const total = s.length;
  const cjk = (s.match(/[\u4e00-\u9fff]/g) || []).length;
  if (cjk >= 6 && cjk / Math.max(total, 1) >= 0.12) return 0.75;
  let good = cjk * 2;
  for (const ch of s) {
    if (/[，。、《》？！：；,.!?;:()\[\]{}_\-/\\'"％+℃°≤≥±·—]/.test(ch)) good += 1;
    else if (/\d/.test(ch) || /\s/.test(ch)) good += 0.8;
  }
  good -= (s.match(LATIN1_GARBAGE_RE) || []).length * 2.5;
  good -= (s.match(CAP_GARBAGE_RUN_RE) || []).length * 4;
  good -= (s.match(PAREN_CAP_RUN_RE) || []).length * 4;
  return Math.max(0, Math.min(1, good / Math.max(total * 1.6, 1)));
}

function stripGarbledFromLine(line: string): string {
  const keepGb = /GB\/?T\s*\d/i.test(line);
  let s = line
    .replace(PAREN_CAP_RUN_RE, '')
    .replace(LATIN1_GARBAGE_RE, '')
    .replace(SYMBOL_FONT_INLINE_RE, '')
    .replace(SYMBOL_FONT_LATIN_RE, '')
    .replace(CID_RADICAL_RE, '');
  if (!keepGb) {
    s = s.replace(DIGIT_SYMBOL_JUNK_RE, '').replace(CAP_GARBAGE_RUN_RE, '');
  }
  return s.replace(/(?<=[\u4e00-\u9fff])\s+[A-Za-z]{1,4}\s+(?=[\u4e00-\u9fff])/g, ' ').replace(/\s{2,}/g, ' ').trim();
}

export function filterGarbledMarkdown(md: string): string {
  if (!md) return '';
  return md
    .split('\n')
    .map(stripGarbledFromLine)
    .filter((line) => {
      if (!line) return false;
      if (lineQualityScore(line) >= 0.2) return true;
      return (line.match(/[\u4e00-\u9fff]/g) || []).length >= 4;
    })
    .join('\n');
}

export function filterGarbledHtml(html: string): string {
  if (!html) return '';
  return html
    .replace(PAREN_CAP_RUN_RE, '')
    .replace(LATIN1_GARBAGE_RE, '')
    .replace(DIGIT_SYMBOL_JUNK_RE, '')
    .replace(CAP_GARBAGE_RUN_RE, '')
    .replace(SYMBOL_FONT_INLINE_RE, '')
    .replace(SYMBOL_FONT_LATIN_RE, '')
    .replace(CID_RADICAL_RE, '');
}

export function hasMarkdownContent(md: string): boolean {
  return Boolean((md || '').trim());
}

/** 后端 HTML 缺失或仍是 Markdown 纯文本时，需从 MD 重新渲染。 */
export function htmlNeedsMarkdownRender(html: string, markdown: string): boolean {
  if (!markdown.trim()) return false;
  if (!html.trim()) return true;
  const plain = html.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim();
  if (/^#{1,6}\s/m.test(plain) || plain.includes(' ## ')) return true;
  if (/<p>\s*#{1,6}\s/.test(html)) return true;
  const mdLen = markdown.trim().length;
  const htmlLen = plain.length;
  if (mdLen > 800 && htmlLen < mdLen * 0.45) return true;
  return false;
}

function enhanceWjglSectionHeadings(html: string): string {
  return html.replace(/<h([1-6])([^>]*)>([\s\S]*?)<\/h\1>/gi, (full, level, attrs, inner) => {
    const text = inner.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim();
    const parsed = parseWjglSectionHeading(text);
    if (!parsed) return full;
    const id = wjglSectionAnchorId(parsed.sectionNo);
    const cleanAttrs = String(attrs || '').replace(/\s*(id|class|data-section-no)=("[^"]*"|'[^']*')/gi, '');
    return `<h${level} id="${id}" class="wjgl-section-heading" data-section-no="${parsed.sectionNo}"${cleanAttrs}>${inner}</h${level}>`;
  });
}

function wrapDocTables(html: string): string {
  return html.replace(/<table(\s|>)/gi, '<div class="doc-table"><table$1').replace(/<\/table>/gi, '</table></div>');
}

/** MinerU Markdown → 可编辑富文本 HTML（与预览样式一致）。 */
export function markdownToWjglHtml(
  md: string,
  docId?: string,
  category?: string,
): string {
  const filtered = filterGarbledMarkdown(md || '');
  if (!filtered.trim()) return '';
  const src = docId && category ? rewriteWjglMarkdownAssets(filtered, docId, category) : filtered;
  const raw = marked.parse(src, { async: false });
  const html = typeof raw === 'string' ? raw : '';
  return filterGarbledHtml(wrapDocTables(enhanceWjglSectionHeadings(html)));
}
