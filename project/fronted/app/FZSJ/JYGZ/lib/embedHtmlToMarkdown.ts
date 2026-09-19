/**
 * 方案正文中嵌入的 HTML 片段（如方法 SYNR）→ Markdown，保留标题/段落/列表/表格/换行。
 */
import TurndownService from 'turndown';
import { gfm } from 'turndown-plugin-gfm';

const turndown = new TurndownService({
  headingStyle: 'atx',
  codeBlockStyle: 'fenced',
  bulletListMarker: '-',
  emDelimiter: '*',
});
turndown.use(gfm);

turndown.addRule('lineBreak', {
  filter: 'br',
  replacement: () => '\n',
});

const HTML_LINE_RE = /<[a-zA-Z][^>]*\/?>|<\/[a-zA-Z]+>/;

function looksLikeHtml(s: string): boolean {
  return HTML_LINE_RE.test(s);
}

function htmlChunkToMarkdown(html: string): string {
  const h = (html || '').trim();
  if (!h) return '';
  if (!looksLikeHtml(h)) return h;
  try {
    const wrapped = h.startsWith('<') ? h : `<motionless>${h}</motionless>`;
    let md = turndown.turndown(wrapped).replace(/\n{3,}/g, '\n\n').trim();
    // 方法小节 h4 在方案中已是 ####，将 HTML 转出的 h4 降为 ##### 避免层级冲突
    md = md.replace(/^#### /gm, '##### ');
    return md;
  } catch {
    return h
      .replace(/<br\s*\/?>/gi, '\n')
      .replace(/<\/p>/gi, '\n\n')
      .replace(/<p[^>]*>/gi, '')
      .replace(/<[^>]+>/g, '')
      .replace(/\n{3,}/g, '\n\n')
      .trim();
  }
}

/**
 * 将混合 Markdown 文档里的 HTML 区块转为 Markdown（不破坏 # 标题行）。
 */
export function convertEmbeddedHtmlToMarkdown(src: string): string {
  const text = (src || '').replace(/\r\n/g, '\n').replace(/\r/g, '\n');
  if (!text.trim()) return '';

  const lines = text.split('\n');
  const out: string[] = [];
  let buf: string[] = [];

  const flushBuf = () => {
    if (!buf.length) return;
    const chunk = buf.join('\n').trim();
    buf = [];
    if (!chunk) return;
    if (looksLikeHtml(chunk)) {
      out.push(htmlChunkToMarkdown(chunk));
      out.push('');
    } else {
      out.push(chunk);
    }
  };

  for (const line of lines) {
    const trimmed = line.trim();
    const isMdHeading = /^#{1,6}\s/.test(trimmed);
    const isHtmlLine = looksLikeHtml(line);

    if (buf.length > 0) {
      if (isMdHeading) {
        flushBuf();
        out.push(line);
      } else {
        buf.push(line);
      }
    } else if (isHtmlLine) {
      buf.push(line);
    } else {
      out.push(line);
    }
  }
  flushBuf();

  return out.join('\n').replace(/\n{3,}/g, '\n\n').trim();
}
