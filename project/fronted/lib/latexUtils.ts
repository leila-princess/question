/**
 * LaTeX 公式：OCR/Word 导出常带多余空格与 \mathrm{~}，展示前规范化以便 KaTeX 渲染。
 */

/** 清理单段 LaTeX 数学字符串（不含外层 $） */
export function normalizeLatexSegment(raw: string): string {
  let s = (raw || '').trim();
  if (!s) return s;
  // 去掉 OCR 常见的空占位与波浪占位
  s = s.replace(/\\mathrm\s*\{\s*~\s*\}/gi, '');
  s = s.replace(/\\text\s*\{\s*~\s*\}/gi, '');
  s = s.replace(/\{\s*~\s*\}/g, '');
  // 数字之间被拆开：3 5 → 35（仅相邻单数字）
  s = s.replace(/(?<=\d)\s+(?=\d)/g, '');
  // 度符号常见 OCR：^ { \circ } → ^\circ
  s = s.replace(/\^\s*\{\s*\\circ\s*\}/g, '^\\circ');
  s = s.replace(/\\circ/g, '\\circ');
  // 合并多余空白（保留必要的一个空格）
  s = s.replace(/\s{2,}/g, ' ').trim();
  return s;
}

export type LatexSplitPart =
  | { type: 'text'; content: string }
  | { type: 'latex'; content: string; display: boolean };

/** 将混排文本按 $...$ / $$...$$ 切分（不处理转义 \$） */
export function splitContentWithLatex(text: string): LatexSplitPart[] {
  const src = text || '';
  if (!src.includes('$')) return [{ type: 'text', content: src }];

  const segments: Array<{ start: number; end: number; display: boolean }> = [];
  const blockRe = /\$\$([\s\S]*?)\$\$/g;
  let m: RegExpExecArray | null;
  while ((m = blockRe.exec(src)) !== null) {
    segments.push({ start: m.index, end: m.index + m[0].length, display: true });
  }
  const inlineRe = /\$([^$\n]+?)\$/g;
  while ((m = inlineRe.exec(src)) !== null) {
    if (!segments.some((s) => m!.index >= s.start && m!.index < s.end)) {
      segments.push({ start: m.index, end: m.index + m[0].length, display: false });
    }
  }
  segments.sort((a, b) => a.start - b.start);

  if (segments.length === 0) return [{ type: 'text', content: src }];

  const parts: LatexSplitPart[] = [];
  let pos = 0;
  for (const seg of segments) {
    if (seg.start > pos) {
      parts.push({ type: 'text', content: src.slice(pos, seg.start) });
    }
    const slice = src.slice(seg.start, seg.end);
    const inner = seg.display ? slice.slice(2, -2) : slice.slice(1, -1);
    parts.push({ type: 'latex', content: normalizeLatexSegment(inner), display: seg.display });
    pos = seg.end;
  }
  if (pos < src.length) {
    parts.push({ type: 'text', content: src.slice(pos) });
  }
  return parts;
}

export function contentHasLatex(text: string): boolean {
  const src = text || '';
  return (
    /\$[^$]+\$/.test(src)
    || /```(?:latex|tex|math)\b/i.test(src)
    || /language-(?:latex|tex|math)/i.test(src)
  );
}

function stripHtmlTags(raw: string): string {
  return (raw || '')
    .replace(/<br\s*\/?>/gi, '\n')
    .replace(/<[^>]+>/g, '')
    .replace(/&nbsp;/gi, ' ')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&amp;/g, '&')
    .trim();
}

/** 将 ```latex 围栏转为 $$...$$，并规范化已有 $ 段 */
export function formatLatexInPlainText(text: string): string {
  let s = text || '';
  s = s.replace(/```(?:latex|tex|math)\s*\n([\s\S]*?)```/gi, (_, inner) => {
    const norm = normalizeLatexSegment(String(inner ?? '').trim());
    return norm ? `\n$$${norm}$$\n` : '';
  });
  if (!s.includes('$')) return s;
  const parts = splitContentWithLatex(s);
  return parts
    .map((p) => {
      if (p.type === 'text') return p.content;
      return p.display ? `$$${p.content}$$` : `$${p.content}$`;
    })
    .join('');
}

/** 在 HTML 中将 latex 代码块转为 $$...$$，并规范化文本节点中的 $ 段 */
export function formatLatexInHtmlContent(html: string): string {
  let s = html || '';
  s = s.replace(
    /<pre[^>]*>\s*<code[^>]*class=["'][^"']*language-(?:latex|tex|math)["'][^>]*>([\s\S]*?)<\/code>\s*<\/pre>/gi,
    (_, inner) => {
      const norm = normalizeLatexSegment(stripHtmlTags(inner));
      return norm ? `<p>$$${norm}$$</p>` : '';
    },
  );
  s = s.replace(
    /<code[^>]*class=["'][^"']*language-(?:latex|tex|math)["'][^>]*>([\s\S]*?)<\/code>/gi,
    (_, inner) => {
      const norm = normalizeLatexSegment(stripHtmlTags(inner));
      return norm ? `$$${norm}$$` : '';
    },
  );
  const re = /(<[^>]+>)/g;
  return s
    .split(re)
    .map((part) => (part.startsWith('<') ? part : formatLatexInPlainText(part)))
    .join('');
}
