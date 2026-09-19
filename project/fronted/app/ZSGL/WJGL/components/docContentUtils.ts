export interface DocSection {
  section_id: string;
  title: string;
  level: number;
  text?: string;
  html?: string;
}

function headingTag(level: number): string {
  return `h${Math.min(6, Math.max(2, (level || 0) + 2))}`;
}

/** 多章节合并为单个富文本 HTML */
export function sectionsToFullHtml(sections: DocSection[]): string {
  if (!sections.length) return '';
  return sections
    .map(s => {
      const tag = headingTag(s.level || 0);
      const body = s.html || (s.text ? `<p>${s.text.replace(/\n/g, '<br/>')}</p>` : '');
      return `<${tag}>${s.title}</${tag}>${body}`;
    })
    .join('');
}

/** 单个富文本保存回 sections.json（整篇一章节） */
export function fullHtmlToSections(html: string, prev?: DocSection[]): DocSection[] {
  const trimmed = (html || '').trim();
  if (!trimmed) return prev?.length ? prev.map(s => ({ ...s, html: '', text: '' })) : [];
  const id = prev?.[0]?.section_id || 'S001';
  const title = prev?.length === 1 ? (prev[0].title || '全文') : '全文';
  return [{ section_id: id, title, level: 0, html: trimmed, text: '' }];
}
