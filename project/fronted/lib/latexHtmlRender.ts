import katex from 'katex';
import {
  formatLatexInHtmlContent,
  splitContentWithLatex,
} from './latexUtils';

/** 将纯文本片段中的 $...$ / $$...$$ 替换为 KaTeX HTML */
export function replaceLatexDelimitersWithKatex(text: string): string {
  if (!text || !text.includes('$')) return text;
  const parts = splitContentWithLatex(text);
  return parts
    .map((p) => {
      if (p.type === 'text') return p.content;
      try {
        return katex.renderToString(p.content, {
          displayMode: p.display,
          throwOnError: false,
        });
      } catch {
        return p.display ? `$$${p.content}$$` : `$${p.content}$`;
      }
    })
    .join('');
}

/** 在 HTML 字符串中渲染 LaTeX（仅处理标签间文本，不破坏标签结构） */
export function renderLatexInHtml(html: string): string {
  const formatted = formatLatexInHtmlContent(html || '');
  const re = /(<[^>]+>)/g;
  return formatted
    .split(re)
    .map((part) => (part.startsWith('<') ? part : replaceLatexDelimitersWithKatex(part)))
    .join('');
}
