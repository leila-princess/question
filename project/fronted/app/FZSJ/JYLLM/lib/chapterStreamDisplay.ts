/**
 * 与 backend/blueprints/FZSJ/JYLLM/routes.py 中 _strip_duplicate_leading_chapter_heading 语义对齐的章节流式展示修补；
 * 全文导出用 buildJyllmFullSchemeMarkdown 与后端 _full_scheme_markdown_from_chapters 对齐。
 */
import type { ChapterModule } from '../types/flow';
const METHOD_SENTINEL_PREFIX = '<!--JYLLM_METHOD:';
const METHOD_SENTINEL_SUFFIX = '-->';

/** 去掉完整或未闭合的 JYLLM 方法占位 HTML 注释（流式阶段可能截断）。 */
export function stripJyllmMethodSentinels(md: string): string {
  let s = md || '';
  s = s.replace(/<!--JYLLM_METHOD:[\s\S]*?-->/g, '');
  const last = s.lastIndexOf(METHOD_SENTINEL_PREFIX);
  if (last !== -1) {
    const tail = s.slice(last);
    if (!tail.includes(METHOD_SENTINEL_SUFFIX)) {
      s = s.slice(0, last).replace(/\s+$/, '');
    }
  }
  return s;
}

/**
 * 若正文首行即为与章名等价的 Markdown 标题，去掉最多两行重复（与后端一致）。
 */
export function stripDuplicateLeadingChapterHeading(chapterTitle: string, md: string): string {
  let rest = (md || '').replace(/^\uFEFF/, '');
  const core = (chapterTitle || '')
    .trim()
    .replace(/^\d+\.?\s*/, '')
    .trim();
  if (!core) return md || '';
  for (let i = 0; i < 2; i++) {
    const lines = rest.split('\n');
    if (!lines.length) break;
    const first = lines[0]!.trim();
    const hm = /^(#{1,6})\s+(.+)$/.exec(first);
    if (!hm) break;
    const hText = hm[2]!.trim();
    const headingCore = hText.replace(/^\d+\.?\s*/, '').trim();
    if (headingCore === core || hText === (chapterTitle || '').trim()) {
      rest = lines.slice(1).join('\n').replace(/^\n+/, '');
    } else {
      break;
    }
  }
  return rest;
}

/** 第一章流式：去掉尾部不完整的 ATX 标题行，避免目录/预览闪烁。 */
export function clampStreamingChapter1Subsections(md: string): string {
  const s = md || '';
  const lines = s.split('\n');
  if (!lines.length) return s;
  const last = lines[lines.length - 1] ?? '';
  const tr = last.trim();
  if (/^#{1,6}\s*$/.test(tr) || /^#{7,}/.test(tr)) {
    return lines.slice(0, -1).join('\n');
  }
  return s;
}

/** 各章拼接为单一 Markdown（章前 `## 标题`，正文已去重首行同题标题）。 */
export function buildJyllmFullSchemeMarkdown(modules: ChapterModule[]): string {
  const list = [...(modules || [])].sort((a, b) => a.index - b.index);
  const parts = list.map((m) => {
    const t = (m.title || `章节 ${m.index}`).trim();
    const body = stripDuplicateLeadingChapterHeading(t, m.content || '');
    return `## ${t}\n\n${body}`;
  });
  return parts.join('\n\n');
}
