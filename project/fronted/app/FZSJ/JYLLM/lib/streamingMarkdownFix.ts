/**
 * 流式方案 Markdown：轻量规范化 + 标题大纲（与 MarkdownRenderer docHeadingIds 顺序一致）。
 */

export interface HeadingOutlineItem {
  id: string;
  level: number;
  text: string;
}

export interface HeadingOutlineNode {
  id: string;
  level: number;
  text: string;
  children: HeadingOutlineNode[];
}

function slugFragment(text: string, maxLen: number): string {
  const t = text
    .trim()
    .slice(0, maxLen)
    .replace(/[^\w\u4e00-\u9fff-]/g, '-')
    .replace(/-+/g, '-')
    .replace(/^-|-$/g, '');
  return t || 'h';
}

/**
 * 生成中与预览用的轻量修补：换行统一、去掉流式尾部未闭合的方法占位注释。
 */
export function normalizeStreamingMarkdown(raw: string): string {
  let s = (raw || '').replace(/\r\n/g, '\n');
  const open = '<!--JYLLM_METHOD:';
  const close = '-->';
  const lastOpen = s.lastIndexOf(open);
  if (lastOpen !== -1) {
    const tail = s.slice(lastOpen);
    if (!tail.includes(close)) {
      s = s.slice(0, lastOpen).replace(/\s+$/, '');
    }
  }
  return s;
}

/**
 * 按正文顺序提取 ATX 标题（跳过分 fenced code），id 与 MarkdownRenderer 遍历 h1–h6 顺序对齐。
 */
export function extractMarkdownHeadingOutline(markdown: string, idPrefix: string): HeadingOutlineItem[] {
  const text = (markdown || '').replace(/\r\n/g, '\n');
  const lines = text.split('\n');
  const out: HeadingOutlineItem[] = [];
  let inFence = false;
  let fenceTick = false;
  let idx = 0;

  for (const line of lines) {
    const t = line.trimEnd();
    const fenceStart = /^(```|~~~)/.exec(t.trimStart());
    if (fenceStart) {
      const tick = fenceStart[1]![0] === '`';
      if (!inFence) {
        inFence = true;
        fenceTick = tick;
      } else if (fenceTick === tick && t.trimStart().startsWith(fenceTick ? '```' : '~~~')) {
        inFence = false;
      }
      continue;
    }
    if (inFence) continue;

    const m = /^(#{1,6})\s+(.+?)(?:\s+#*)?$/.exec(t.trim());
    if (!m) continue;
    const level = m[1]!.length;
    const rawText = m[2]!.trim();
    const id = `${idPrefix}-h-${idx}-${slugFragment(rawText, 48)}`;
    idx += 1;
    out.push({ id, level, text: rawText });
  }
  return out;
}

export function headingOutlineToTree(
  entries: { id: string; level: number; text: string }[],
): HeadingOutlineNode[] {
  const root: HeadingOutlineNode[] = [];
  const stack: HeadingOutlineNode[] = [];
  for (const e of entries) {
    const node: HeadingOutlineNode = { id: e.id, level: e.level, text: e.text, children: [] };
    while (stack.length && stack[stack.length - 1]!.level >= e.level) {
      stack.pop();
    }
    if (stack.length === 0) {
      root.push(node);
    } else {
      stack[stack.length - 1]!.children.push(node);
    }
    stack.push(node);
  }
  return root;
}

const _RE_SCHEME_SECTION_NO = /^[12]\.\d/;
const _RE_CH2_SECTION_NO = /^2\.(1|2)\.\d/;
const _RE_METHOD_ORDINAL = /[（(][一二三四五六七八九十]+[）)]/;
const _RE_METHOD_TITLE = /试验方法|方法补全/;

/**
 * 方案侧栏目录：仅展示至「项目与指标」层级，过滤方法内（一）（二）（三）等。
 * chapterIndex: 1 | 2 按章过滤；0 表示整篇文档（JYGZ 单文档）。
 */
export function filterSchemeTocHeadings(
  entries: HeadingOutlineItem[],
  chapterIndex: number,
): HeadingOutlineItem[] {
  return (entries || []).filter((e) => {
    const text = (e.text || '').trim();
    if (!text) return false;

    if (_RE_METHOD_ORDINAL.test(text) || _RE_METHOD_TITLE.test(text)) {
      return false;
    }

    if (chapterIndex === 1) {
      if (e.level <= 2 && /^1[.\s]/.test(text)) return true;
      return _RE_SCHEME_SECTION_NO.test(text) && text.startsWith('1.');
    }

    if (chapterIndex === 2 || chapterIndex === 0) {
      if (/^###\s*2\.1\s/.test(`### ${text}`) || text.startsWith('2.1 试验项目')) return true;
      if (/^###\s*2\.2\s/.test(`### ${text}`) || text.startsWith('2.2 ')) return true;
      if (text.startsWith('第二章节') || text.startsWith('2. 试验内容及方法')) return true;
      if (_RE_CH2_SECTION_NO.test(text)) return true;
      if (chapterIndex === 0 && _RE_SCHEME_SECTION_NO.test(text) && text.startsWith('1.')) return true;
      if (chapterIndex === 0 && (text.startsWith('第一章节') || text.startsWith('1. 试验依据'))) return true;
      return false;
    }

    return true;
  });
}
