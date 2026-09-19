/**
 * 方案正文：contentEditable HTML ↔ Markdown（与 SchemeFormatToolbar / MarkdownRenderer 配合）。
 */
import { marked } from 'marked';
import TurndownService from 'turndown';
import { gfm } from 'turndown-plugin-gfm';

const turndown = new TurndownService({
  headingStyle: 'atx',
  codeBlockStyle: 'fenced',
  bulletListMarker: '-',
});
turndown.use(gfm);

marked.setOptions({ gfm: true, breaks: false });

export function schemeEditableHtmlToMarkdown(html: string): string {
  const h = (html || '').trim();
  if (!h) return '';
  let md = turndown.turndown(h).replace(/\n{3,}/g, '\n\n').trim();
  return md;
}

export function schemeMarkdownToEditableHtml(markdown: string): string {
  const md = markdown || '';
  if (!md.trim()) return '<p><br></p>';
  const out = marked.parse(md, { async: false }) as string;
  return typeof out === 'string' ? out : '<p><br></p>';
}
