import type { ChapterModule, RegionRewriteSpec } from '../types/flow';

const METHOD_SENTINEL_RE = /<!--JYLLM_METHOD:([^>]+)-->/g;
const EXCERPT_LEN = 1500;

export function buildFullSchemeExcerpt(modules: ChapterModule[]): string {
  const list = [...(modules || [])].sort((a, b) => a.index - b.index);
  return list
    .map((m) => {
      const title = m.title || `第 ${m.index} 章`;
      const body = (m.content || '').slice(0, EXCERPT_LEN);
      return `## ${title}\n${body}`;
    })
    .join('\n\n')
    .slice(0, EXCERPT_LEN * 2);
}

export function buildHeadingBreadcrumb(spec: RegionRewriteSpec): string {
  const parts: string[] = [];
  if (spec.chapter_title) parts.push(spec.chapter_title);
  if (spec.heading_text && spec.region_type === 'heading') parts.push(spec.heading_text);
  if (spec.label && spec.region_type !== 'heading') parts.push(spec.label);
  return parts.join(' > ');
}

export function findMethodIdsInChapter(markdown: string): string[] {
  const ids: string[] = [];
  const text = markdown || '';
  let m: RegExpExecArray | null;
  const re = new RegExp(METHOD_SENTINEL_RE.source, 'g');
  while ((m = re.exec(text)) !== null) {
    const id = (m[1] || '').trim();
    if (id && !ids.includes(id)) ids.push(id);
  }
  return ids;
}

export function getSelectionTextFromDocument(): string {
  if (typeof window === 'undefined') return '';
  const sel = window.getSelection();
  if (!sel || sel.isCollapsed) return '';
  return sel.toString();
}

/** 在章 MD 中定位选中文本，返回 offset（失败则仅依赖 selected_text） */
export function findSelectionOffsets(
  chapterMarkdown: string,
  selectedText: string,
): { start_offset?: number; end_offset?: number } {
  const md = chapterMarkdown || '';
  const sn = (selectedText || '').trim();
  if (!sn) return {};
  const idx = md.indexOf(sn);
  if (idx >= 0) return { start_offset: idx, end_offset: idx + sn.length };
  const norm = (s: string) => s.replace(/[ \t]+/g, ' ');
  const idx2 = norm(md).indexOf(norm(sn));
  if (idx2 >= 0) return { start_offset: idx2, end_offset: idx2 + sn.length };
  return {};
}

export function buildRegionRewriteBody(
  spec: RegionRewriteSpec,
  comment: string,
  taskId: string,
  baseConfig: Record<string, unknown>,
  llmModelKey?: string,
  options?: {
    fullSchemeExcerpt?: string;
    originalRegion?: string;
  },
): Record<string, unknown> {
  const offsets =
    spec.region_type === 'selection' && spec.selected_text
      ? findSelectionOffsets(spec.chapter_content, spec.selected_text)
      : {};

  return {
    task_id: taskId,
    chapter_index: spec.chapter_index,
    region_type: spec.region_type,
    comment,
    chapter_content: spec.chapter_content,
    chapter_title: spec.chapter_title,
    heading_text: spec.heading_text || spec.label,
    heading_breadcrumb: buildHeadingBreadcrumb(spec),
    full_scheme_excerpt: options?.fullSchemeExcerpt || '',
    original_region: options?.originalRegion || spec.original_region || '',
    selected_text: spec.selected_text,
    start_offset: spec.start_offset ?? offsets.start_offset,
    end_offset: spec.end_offset ?? offsets.end_offset,
    heading_id: spec.heading_id,
    method_id: spec.method_id,
    base_config: baseConfig,
    llm_model_key: llmModelKey || undefined,
  };
}

export function regionTypeLabel(type: RegionRewriteSpec['region_type']): string {
  switch (type) {
    case 'selection':
      return '选中文字';
    case 'heading':
      return '标题节';
    case 'method':
      return '试验方法';
    case 'chapter':
      return '整章';
    default:
      return '区域';
  }
}
