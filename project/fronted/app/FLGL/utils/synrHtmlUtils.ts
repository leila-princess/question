/** 方法 SYNR 是否含可渲染的 HTML 表格 */
import { formatLatexInHtmlContent } from '@/lib/latexUtils';

export function synrContainsTable(html: string): boolean {
  return /<\s*table\b/i.test(html || '');
}

type SlateLeaf = { text?: string; bold?: boolean; italic?: boolean; underline?: boolean; code?: boolean };
type SlateElement = { type?: string; children?: SlateNode[] };
type SlateNode = SlateLeaf | SlateElement;

function isSlateLeaf(node: SlateNode): node is SlateLeaf {
  return node != null && typeof node === 'object' && 'text' in node;
}

function escapeSynrHtml(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function slateLeafToHtml(leaf: SlateLeaf): string {
  let t = escapeSynrHtml(String(leaf.text ?? ''));
  if (!t) return '';
  if (leaf.code) t = `<code>${t}</code>`;
  if (leaf.bold) t = `<strong>${t}</strong>`;
  if (leaf.italic) t = `<em>${t}</em>`;
  if (leaf.underline) t = `<u>${t}</u>`;
  return t;
}

function slateElementToHtml(el: SlateElement): string {
  const children = (el.children || []).map(slateNodeToHtml).join('');
  const type = el.type || 'paragraph';
  switch (type) {
    case 'paragraph':
      return children ? `<p>${children}</p>` : '';
    case 'header1':
    case 'header2':
    case 'header3':
    case 'header4':
    case 'header5': {
      const level = type.replace('header', '');
      return `<h${level}>${children}</h${level}>`;
    }
    case 'bulleted-list':
      return `<ul>${children}</ul>`;
    case 'numbered-list':
      return `<ol>${children}</ol>`;
    case 'list-item':
      return `<li>${children}</li>`;
    case 'table':
      return `<table border="1">${children}</table>`;
    case 'table-row':
      return `<tr>${children}</tr>`;
    case 'table-cell':
      return `<td>${children}</td>`;
    default:
      return children ? `<p>${children}</p>` : '';
  }
}

function slateNodeToHtml(node: SlateNode): string {
  if (isSlateLeaf(node)) return slateLeafToHtml(node);
  return slateElementToHtml(node as SlateElement);
}

/** 将误存的 Slate/JSON 结构转为 HTML，避免 WangEditor 报 Text node 错误 */
export function slateOrJsonContentToHtml(raw: unknown): string | null {
  if (raw == null) return '';
  if (typeof raw === 'string') {
    const t = raw.trim();
    if (!t.startsWith('[') && !t.startsWith('{')) return null;
    try {
      return slateOrJsonContentToHtml(JSON.parse(t));
    } catch {
      return null;
    }
  }
  if (Array.isArray(raw)) {
    if (raw.length && raw.every((n) => isSlateLeaf(n as SlateNode))) {
      return `<p>${raw.map((n) => slateLeafToHtml(n as SlateLeaf)).join('')}</p>`;
    }
    return raw.map((n) => slateNodeToHtml(n as SlateNode)).join('');
  }
  if (typeof raw === 'object' && isSlateLeaf(raw as SlateNode)) {
    return `<p>${slateLeafToHtml(raw as SlateLeaf)}</p>`;
  }
  if (typeof raw === 'object' && raw !== null && 'children' in raw) {
    return slateElementToHtml(raw as SlateElement);
  }
  return null;
}

const SYNR_BLOCK_TAG_RE = /<\s*(p|div|table|figure|ul|ol|li|h[1-6]|strong)\b/i;
const SEE_REF_INLINE_OPEN_RE = /^<div[^>]*class=["'][^"']*see-ref-inline[^"']*["']/i;

function collectMultilineHtmlBlock(lines: string[], start: number, closeTag: string): { html: string; next: number } {
  const block: string[] = [lines[start]];
  let i = start + 1;
  const closeRe = new RegExp(`<\\/${closeTag}>`, 'i');
  while (i < lines.length && !closeRe.test(block[block.length - 1] || '')) {
    block.push(lines[i]);
    i += 1;
  }
  return { html: block.join('\n'), next: i };
}

/** 仅含 <br> 而无块级标签的伪 HTML，应按纯文本/Markdown 再解析 */
export function isBrOnlyPseudoHtml(raw: string): boolean {
  const s = (raw || '').trim();
  if (!s || !/<\s*br\s*\/?>/i.test(s)) return false;
  return !SYNR_BLOCK_TAG_RE.test(s) && !SEE_REF_INLINE_OPEN_RE.test(s);
}

/** 将裸 <br> 转为换行，并合并「标题行 + /评定」续行 */
function preprocessSynrRaw(raw: string): string {
  let s = String(raw || '').trim();
  s = s.replace(/<br\s*\/?>/gi, '\n');
  s = s.replace(/(\d+(?:\.\d+)*\s+[^\n/]+)\n\/评定\s*/g, '$1/评定\n');
  return s;
}

const KNOWN_TABLE_COLS = [
  '温度/高度条件', '参数名称', '功能说明', '数据记录', '岗位角色', '工作内容',
  '设备名称', '文件标识', '文件名称', '样机编号', '机身编号', '持续时间',
  '序号', '步骤', '姓名', '参数', '参数值', '来源', '操作', '配置',
  '试验类型', '机型', '颜色', '温度（℃）', '气压（kPa）', '对应高度',
  '喷淋角度', '水压', '喷水量', '水温控制',
];

function splitGluedTableHeader(line: string): string[] {
  const s = line.trim();
  if (s.includes('|')) {
    return s.split('|').map((c) => c.trim()).filter((c, i, arr) => c || arr.length > 1);
  }
  const cols: string[] = [];
  let pos = 0;
  const ordered = [...KNOWN_TABLE_COLS].sort((a, b) => b.length - a.length);
  while (pos < s.length) {
    const tok = ordered.find((k) => s.startsWith(k, pos));
    if (!tok) break;
    cols.push(tok);
    pos += tok.length;
  }
  return cols.length >= 2 ? cols : [s];
}

function splitGluedTableRow(line: string, colCount: number): string[] {
  let s = line.trim();
  if (s.startsWith('|')) s = s.slice(1);
  if (s.includes('|')) return s.split('|').map((c) => c.trim());
  const kv = s.match(/^([\u4e00-\u9fffA-Za-z（）()、，\-\s]+?)([\d≥≤±].+)$/);
  if (kv) {
    if (colCount >= 3) return ['', kv[1].trim(), kv[2].trim()];
    return [kv[1].trim(), kv[2].trim()];
  }
  return [s];
}

function isTableBlockStopLine(line: string): boolean {
  const s = line.trim();
  if (!s) return true;
  if (/^【表格】\s*$/.test(s)) return true;
  if (/^#{1,6}\s+/.test(s)) return true;
  if (/^附录\s*[A-ZＡ-Ｚ]?/i.test(s)) return true;
  if (/^\d+\s+[\u4e00-\u9fff]{2,12}$/.test(s)) return true;
  if (/^#{1,6}\s+\d/m.test(s)) return true;
  if (/^\d+(?:\.\d+)*\s*[\u4e00-\u9fff]/.test(s)) return true;
  if (/^[（(]\d+[）)]/.test(s)) return true;
  return false;
}

/** 已是章节标题 + HTML 表格的富文本，不宜再当 Markdown 重解析 */
export function isStructuredSynrHtml(raw: string): boolean {
  const s = String(raw || '').trim();
  if (!s) return false;
  if (/8\.\d+\.\d+/.test(s) || /data-section-no=["']8\./i.test(s)) return false;
  const hasHeading = /wjgl-section-heading/i.test(s) || /data-section-no=/i.test(s);
  const hasBlocks = SYNR_BLOCK_TAG_RE.test(s) || SEE_REF_INLINE_OPEN_RE.test(s);
  if (hasHeading && hasBlocks) return true;
  if (!/<\s*table\b/i.test(s) && !/<\s*figure\b/i.test(s) && !SEE_REF_INLINE_OPEN_RE.test(s)) return false;
  return hasBlocks;
}

/** 是否仍含未转换的 Markdown 痕迹（可与 HTML 混排） */
export function hasMarkdownArtifacts(raw: string): boolean {
  const s = preprocessSynrRaw(raw || '');
  if (!s) return false;
  if (isStructuredSynrHtml(raw)) {
    return /^#{1,6}\s/m.test(s)
      || /^```(?:latex|tex|math)/im.test(s)
      || /^\|.+\|$/m.test(s)
      || /【表格】/.test(s);
  }
  return /^#{1,6}\s/m.test(s)
    || /^(?:附)?表\s*[\dA-Za-z]+/m.test(s)
    || /^```(?:latex|tex|math)/im.test(s)
    || /^\|.+\|$/m.test(s)
    || /【表格】/.test(s);
}

function stripHtmlFragmentToPlain(fragment: string): string {
  let s = preprocessSynrRaw(fragment);
  s = s.replace(/<\/p>\s*/gi, '\n\n');
  s = s.replace(/<br\s*\/?>/gi, '\n');
  s = s.replace(/<\/h[1-6]>\s*/gi, '\n\n');
  s = s.replace(/<h[1-6][^>]*>/gi, '\n');
  s = s.replace(/<[^>]+>/g, '');
  return s
    .replace(/&nbsp;/gi, ' ')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&amp;/g, '&')
    .trim();
}

function htmlToPlainForMarkdown(raw: string): string {
  const h = preprocessSynrRaw(raw);
  if (!/<\s*table\b/i.test(h)) {
    return stripHtmlFragmentToPlain(h);
  }
  const parts: string[] = [];
  let pos = 0;
  const re = /<table[\s\S]*?<\/table>/gi;
  let m: RegExpExecArray | null;
  while ((m = re.exec(h)) !== null) {
    const before = stripHtmlFragmentToPlain(h.slice(pos, m.index));
    if (before) parts.push(before);
    parts.push(m[0].trim());
    pos = m.index + m[0].length;
  }
  const tail = stripHtmlFragmentToPlain(h.slice(pos));
  if (tail) parts.push(tail);
  return parts.join('\n\n');
}

function stripResidualMarkdownInHtml(html: string): string {
  return (html || '')
    .replace(/^#{1,6}\s+/gm, '')
    .replace(/(<p[^>]*>)\s*#{1,6}\s+/gi, '$1')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
}

function renderPipeTable(rows: string[][], inlineMd: (t: string) => string): string {
  if (!rows.length) return '';
  const [head, ...body] = rows;
  const out: string[] = ['<div class="doc-table"><table border="1" cellpadding="4" cellspacing="0">'];
  out.push('<thead><tr>');
  head.forEach((c) => { out.push(`<th>${inlineMd(c)}</th>`); });
  out.push('</tr></thead><tbody>');
  body.forEach((row) => {
    out.push('<tr>');
    row.forEach((c) => { out.push(`<td>${inlineMd(c)}</td>`); });
    out.push('</tr>');
  });
  out.push('</tbody></table></div>');
  return out.join('');
}

/** 内容是否像 GFM Markdown（按章提取真源） */
export function looksLikeSynrMarkdown(raw: string): boolean {
  const s = preprocessSynrRaw(raw || '').trim();
  if (!s) return false;
  if (hasMarkdownArtifacts(s)) return true;
  if (/<\s*(p|div|table|ul|ol|li|h[1-6]|strong)\b/i.test(s)) return false;
  return /^\d+\.\d+(?:\.\d+)*\s+[\u4e00-\u9fff]/m.test(s)
    || /^\d+\s+[\u4e00-\u9fff]{2,12}/m.test(s)
    || /^[（(][一二三四五六七八九十百零\d]+[）)]/m.test(s);
}

function parseCaptionTableBlock(lines: string[], start: number, inlineMd: (t: string) => string): { html: string; next: number } {
  const capLine = lines[start].trim();
  const capM = capLine.match(/^(?:附)?表\s*(\S+)\s*(.*)$/);
  if (!capM) return { html: '', next: start };
  const caption = `表 ${capM[1]}${capM[2] ? ` ${capM[2]}` : ''}`.trim();
  let i = start + 1;
  while (i < lines.length && !lines[i].trim()) i += 1;
  if (i < lines.length && /^<table[\s>]/i.test(lines[i].trim())) {
    return { html: `<p><strong>${inlineMd(caption)}</strong></p>`, next: i };
  }
  const rows: string[][] = [];
  if (i < lines.length && lines[i].trim() && !isTableBlockStopLine(lines[i].trim())) {
    const hdr = lines[i].trim();
    if (!hdr.includes('|')) {
      rows.push(splitGluedTableHeader(hdr));
      i += 1;
    }
  }
  while (i < lines.length) {
    const rowLine = lines[i].trim();
    if (!rowLine) {
      i += 1;
      if (rows.length > 1) break;
      continue;
    }
    if (isTableBlockStopLine(rowLine)) break;
    if (rowLine.includes('|')) {
      let row = rowLine;
      if (row.startsWith('|')) row = row.slice(1);
      if (row.endsWith('|')) row = row.slice(0, -1);
      rows.push(row.split('|').map((c) => c.trim()));
    } else {
      rows.push(splitGluedTableRow(rowLine, rows[0]?.length || 3));
    }
    i += 1;
  }
  const parts: string[] = [`<p><strong>${inlineMd(caption)}</strong></p>`];
  if (rows.length) parts.push(renderPipeTable(rows, inlineMd));
  return { html: parts.join(''), next: i };
}

/** 将仍含 Markdown 痕迹或纯文本的 SYNR 转为 HTML（兼容历史提取结果） */
export function synrMarkdownToHtml(raw: string): string {
  let s = preprocessSynrRaw(raw || '');
  if (!s) return '';
  if (SYNR_BLOCK_TAG_RE.test(s) && hasMarkdownArtifacts(raw)) {
    s = htmlToPlainForMarkdown(raw);
  } else if (!isBrOnlyPseudoHtml(raw) && SYNR_BLOCK_TAG_RE.test(s) && !hasMarkdownArtifacts(raw)) {
    return stripResidualMarkdownInHtml(s.replace(/<h([1-6])([^>]*)>([\s\S]*?)<\/h\1>/gi, (_, _lvl, _attrs, inner) => {
      const text = String(inner || '').replace(/<[^>]+>/g, '').trim();
      const m = text.match(/^(\d+(?:\.\d+)*)\s*(.+)$/);
      const sno = m?.[1] || '';
      const label = m ? `${m[1]} ${m[2]}`.trim() : text;
      const dataNo = sno ? ` data-section-no="${sno}"` : '';
      return `<p class="wjgl-section-heading"${dataNo} style="font-weight:600;margin:10px 0 6px"><strong>${label}</strong></p>`;
    }));
  }

  const lines = s.replace(/\r\n/g, '\n').split('\n');
  const out: string[] = [];
  let i = 0;
  const numListRe = /^(\d+)[\.、\)）]\s+/;
  const cnNumListRe = /^(\d+)[）)]\s*(.*)$/;
  const letterListRe = /^([a-zA-Z])[\.、\)）]\s+/;
  const tableMarkerRe = /^【表格】\s*$/;
  const mdHeadingRe = /^#{1,6}\s+(\d+(?:\.\d+)*)\s+(.+)$/;
  const mdHashParenRe = /^#{1,6}\s*[（(](\d+)[）)]\s*(.+)$/;
  const mdHashPlainRe = /^#{1,6}\s+(.+)$/;
  const topSectionRe = /^(\d+)\s+(试验条件|试验方法|试验过程|试验数据处理|结果评定|试验目的|试验组织[\u4e00-\u9fff]*)$/;
  const parenSubheadRe = /^[（(](\d+)[）)]\s*(.+)$/;
  const cnParenHeadingRe = /^[（(]([一二三四五六七八九十百零\d]+)[）)]\s*(.+)$/;
  const gluedKeywordRe = /^(?:(\d+(?:\.\d+)*)\s*)?(试验环境和条件要求|试验环境及条件要求|试验环境条件|数据处理方法|试验数据处理|试验结果评定|试验结果评判|试验结果判定|试验目的|试验条件|试验方法|试验过程|试验步骤|试验要求|试验设备|试验仪器|结果评定|结果评判|结果判定|试验环境)(?:[：:\s、\-—]+(.+)|(.+))?$/;
  const subMethodRe = /^\*\*[（(]?(\d+)[）)]?\s*(.+?)\*\*\s*$/;
  const tableCaptionRe = /^(?:附)?表\s*(\S+)/;
  const pipeRowRe = (line: string) => (line.trim().match(/\|/g) || []).length >= 2;

  const inlineMd = (t: string) =>
    t.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');

  while (i < lines.length) {
    const stripped = lines[i].trim();
    if (!stripped) {
      i += 1;
      continue;
    }
    if (/^<table[\s>]/i.test(stripped)) {
      const collected = collectMultilineHtmlBlock(lines, i, 'table');
      out.push(`<div class="doc-table">${collected.html}</div>`);
      i = collected.next;
      continue;
    }
    if (/^<figure[\s>]/i.test(stripped)) {
      const collected = collectMultilineHtmlBlock(lines, i, 'figure');
      out.push(collected.html);
      i = collected.next;
      continue;
    }
    if (SEE_REF_INLINE_OPEN_RE.test(stripped)) {
      const collected = collectMultilineHtmlBlock(lines, i, 'div');
      out.push(collected.html);
      i = collected.next;
      continue;
    }
    if (tableCaptionRe.test(stripped)) {
      const parsed = parseCaptionTableBlock(lines, i, inlineMd);
      if (parsed.html) {
        out.push(parsed.html);
        i = parsed.next;
        continue;
      }
    }
    const ts = stripped.match(topSectionRe);
    if (ts) {
      out.push(`<p class="wjgl-section-heading" data-section-no="${ts[1]}" style="font-weight:600;margin:10px 0 6px"><strong>${inlineMd(`${ts[1]} ${ts[2]}`)}</strong></p>`);
      i += 1;
      continue;
    }
    const mhp = stripped.match(mdHashParenRe);
    if (mhp) {
      out.push(`<p><strong>（${mhp[1]}）${inlineMd(mhp[2])}</strong></p>`);
      i += 1;
      continue;
    }
    const mhp2 = stripped.match(mdHashPlainRe);
    if (mhp2 && !mdHeadingRe.test(stripped)) {
      const body = mhp2[1].trim();
      const innerParen = body.match(/^[（(](\d+)[）)]\s*(.+)$/);
      if (innerParen) {
        out.push(`<p><strong>（${innerParen[1]}）${inlineMd(innerParen[2])}</strong></p>`);
      } else if (/^\d+\s+[\u4e00-\u9fff]/.test(body)) {
        const sm = body.match(/^(\d+)\s+(.+)$/);
        if (sm) {
          out.push(`<p class="wjgl-section-heading" data-section-no="${sm[1]}" style="font-weight:600;margin:10px 0 6px"><strong>${inlineMd(body)}</strong></p>`);
        } else {
          out.push(`<p><strong>${inlineMd(body)}</strong></p>`);
        }
      } else {
        out.push(`<p><strong>${inlineMd(body)}</strong></p>`);
      }
      i += 1;
      continue;
    }
    const hm = stripped.match(mdHeadingRe);
    if (hm) {
      out.push(`<p class="wjgl-section-heading" data-section-no="${hm[1]}" style="font-weight:600;margin:10px 0 6px"><strong>${inlineMd(`${hm[1]} ${hm[2]}`)}</strong></p>`);
      i += 1;
      continue;
    }
    const cnh = stripped.match(cnParenHeadingRe);
    if (cnh) {
      out.push(`<p><strong>（${cnh[1]}）${inlineMd(cnh[2])}</strong></p>`);
      i += 1;
      continue;
    }
    const gkw = stripped.match(gluedKeywordRe);
    if (gkw) {
      const keyword = gkw[2];
      const body = (gkw[3] || gkw[4] || '').replace(/^[：:\s、\-—]+/, '').trim();
      out.push(`<p><strong>${inlineMd(keyword)}</strong></p>`);
      if (body) out.push(`<p>${inlineMd(body)}</p>`);
      i += 1;
      continue;
    }
    const psm = stripped.match(parenSubheadRe);
    if (psm) {
      out.push(`<p><strong>（${psm[1]}）${inlineMd(psm[2])}</strong></p>`);
      i += 1;
      continue;
    }
    if (tableMarkerRe.test(stripped) || (stripped.startsWith('【表格】') && stripped.length > 4)) {
      const rows: string[][] = [];
      if (!tableMarkerRe.test(stripped)) {
        const rest = stripped.replace(/^【表格】\s*/, '').trim();
        if (rest) rows.push(splitGluedTableHeader(rest));
      }
      i += 1;
      while (i < lines.length) {
        const rowLine = lines[i].trim();
        if (!rowLine || isTableBlockStopLine(rowLine)) break;
        if (rowLine.includes('|') || rowLine.startsWith('|')) {
          let row = rowLine;
          if (row.startsWith('|')) row = row.slice(1);
          rows.push(row.split('|').map((c) => c.trim()));
        } else if (!rows.length) {
          rows.push(splitGluedTableHeader(rowLine));
        } else {
          rows.push(splitGluedTableRow(rowLine, rows[0]?.length || 2));
        }
        i += 1;
      }
      if (rows.length) out.push(renderPipeTable(rows, inlineMd));
      continue;
    }
    if (pipeRowRe(stripped)) {
      const rows: string[][] = [];
      while (i < lines.length && pipeRowRe(lines[i].trim())) {
        let row = lines[i].trim();
        if (row.startsWith('|')) row = row.slice(1);
        if (row.endsWith('|')) row = row.slice(0, -1);
        rows.push(row.split('|').map((c) => c.trim()));
        i += 1;
      }
      if (rows.length) {
        const [head, ...body] = rows;
        out.push('<div class="doc-table"><table border="1" cellpadding="4" cellspacing="0">');
        out.push('<thead><tr>');
        head.forEach((c) => { out.push(`<th>${inlineMd(c)}</th>`); });
        out.push('</tr></thead><tbody>');
        body.forEach((row) => {
          out.push('<tr>');
          row.forEach((c) => { out.push(`<td>${inlineMd(c)}</td>`); });
          out.push('</tr>');
        });
        out.push('</tbody></table></div>');
      }
      continue;
    }
    if (numListRe.test(stripped)) {
      out.push('<ol>');
      while (i < lines.length && numListRe.test(lines[i].trim())) {
        const body = lines[i].trim().replace(numListRe, '');
        out.push(`<li>${inlineMd(body)}</li>`);
        i += 1;
      }
      out.push('</ol>');
      continue;
    }
    const cnm = stripped.match(cnNumListRe);
    if (cnm && cnm[2]) {
      out.push('<ol>');
      while (i < lines.length) {
        const m = lines[i].trim().match(cnNumListRe);
        if (!m || !m[2]) break;
        out.push(`<li>${inlineMd(m[2])}</li>`);
        i += 1;
      }
      out.push('</ol>');
      continue;
    }
    if (letterListRe.test(stripped)) {
      out.push('<ol type="a">');
      while (i < lines.length && letterListRe.test(lines[i].trim())) {
        const body = lines[i].trim().replace(letterListRe, '');
        out.push(`<li>${inlineMd(body)}</li>`);
        i += 1;
      }
      out.push('</ol>');
      continue;
    }
    const sm = stripped.match(subMethodRe);
    if (sm) {
      out.push(`<p><strong>（${sm[1]}）${inlineMd(sm[2])}</strong></p>`);
      i += 1;
      continue;
    }
    if (/^\d+(?:\.\d+)*\s*[\u4e00-\u9fff]/.test(stripped)) {
      const sm = stripped.match(/^(\d+(?:\.\d+)*)\s*([\u4e00-\u9fff（）()、，\sA-Za-z0-9.\-]{2,80})\s*$/);
      if (sm) {
        out.push(`<p class="wjgl-section-heading" data-section-no="${sm[1]}" style="font-weight:600;margin:10px 0 6px"><strong>${inlineMd(`${sm[1]} ${sm[2]}`)}</strong></p>`);
        i += 1;
        continue;
      }
      const pm = stripped.match(/^(\d+(?:\.\d+)*)\s*([\u4e00-\u9fff（）()、，\sA-Za-z0-9.\-]{2,48})(.+)$/);
      if (pm && pm[3].trim()) {
        out.push(`<p class="wjgl-section-heading" data-section-no="${pm[1]}" style="font-weight:600;margin:10px 0 6px"><strong>${inlineMd(`${pm[1]} ${pm[2]}`)}</strong></p>`);
        out.push(`<p>${inlineMd(pm[3].trim())}</p>`);
        i += 1;
        continue;
      }
    }
    const para: string[] = [stripped];
    i += 1;
    while (i < lines.length && lines[i].trim() && !pipeRowRe(lines[i]) && !numListRe.test(lines[i].trim()) && !cnNumListRe.test(lines[i].trim()) && !letterListRe.test(lines[i].trim()) && !subMethodRe.test(lines[i].trim()) && !mdHeadingRe.test(lines[i].trim()) && !mdHashParenRe.test(lines[i].trim()) && !mdHashPlainRe.test(lines[i].trim()) && !topSectionRe.test(lines[i].trim()) && !tableCaptionRe.test(lines[i].trim()) && !parenSubheadRe.test(lines[i].trim()) && !tableMarkerRe.test(lines[i].trim()) && !/^<table[\s>]/i.test(lines[i].trim()) && !/^<figure[\s>]/i.test(lines[i].trim()) && !SEE_REF_INLINE_OPEN_RE.test(lines[i].trim()) && !/^\d+(?:\.\d+)*\s*[\u4e00-\u9fff]/.test(lines[i].trim())) {
      para.push(lines[i].trim());
      i += 1;
    }
    out.push(`<p>${inlineMd(para.join('<br>'))}</p>`);
  }
  return out.join('');
}

function lightNormalizeStructuredSynrHtml(raw: string): string {
  let s = String(raw || '').trim();
  if (!s) return '';
  s = s.replace(/<h([1-6])([^>]*)>([\s\S]*?)<\/h\1>/gi, (_, _lvl, _attrs, inner) => {
    const text = String(inner || '').replace(/<[^>]+>/g, '').trim();
    const m = text.match(/^(\d+(?:\.\d+)*)\s*(.+)$/);
    const sno = m?.[1] || '';
    const label = m ? `${m[1]} ${m[2]}`.trim() : text;
    const dataNo = sno ? ` data-section-no="${sno}"` : '';
    return `<p class="wjgl-section-heading"${dataNo} style="font-weight:600;margin:10px 0 6px"><strong>${label}</strong></p>`;
  });
  s = stripResidualMarkdownInHtml(s);
  s = s.replace(
    /<div[^>]*class=["'][^"']*doc-table[^"']*["'][^>]*>\s*(<table[\s\S]*?<\/table>)\s*<\/div>/gi,
    '$1',
  );
  s = s.replace(/<table(?![^>]*\bborder\b)/gi, '<table border="1" cellpadding="4" cellspacing="0"');
  return formatLatexInHtmlContent(s);
}

/** 载入 WangEditor 前：展开 doc-table 包裹，补全 table 边框；Markdown 兜底；LaTeX 规范化 */
export function normalizeSynrForWangEditor(html: unknown): string {
  const fromJson = slateOrJsonContentToHtml(html);
  const raw = String(html ?? '').trim();
  if (isStructuredSynrHtml(raw)) {
    const base = fromJson != null ? fromJson : raw;
    return lightNormalizeStructuredSynrHtml(base);
  }
  const needsMarkdownParse = looksLikeSynrMarkdown(raw)
    || hasMarkdownArtifacts(raw)
    || isBrOnlyPseudoHtml(raw)
    || !SYNR_BLOCK_TAG_RE.test(raw)
    || (/<\s*br\s*\/?>/i.test(raw) && (raw.match(/<\s*br\s*\/?>/gi) || []).length >= 3);
  let s = fromJson != null ? fromJson : (needsMarkdownParse ? synrMarkdownToHtml(raw) : raw);
  if (!s) return '';
  // 已是 HTML 时不应再次整段 Markdown 解析，否则正文会重复渲染
  if (hasMarkdownArtifacts(s) && !/<\s*p\b/i.test(s)) {
    s = synrMarkdownToHtml(s);
  } else if (hasMarkdownArtifacts(raw) && /<\s*p\b/i.test(s)) {
    s = stripResidualMarkdownInHtml(s);
  }
  s = stripResidualMarkdownInHtml(s);
  s = s.replace(
    /<div[^>]*class=["'][^"']*doc-table[^"']*["'][^>]*>\s*(<table[\s\S]*?<\/table>)\s*<\/div>/gi,
    '$1',
  );
  s = s.replace(/<table(?![^>]*\bborder\b)/gi, '<table border="1" cellpadding="4" cellspacing="0"');
  s = formatLatexInHtmlContent(s);
  return s;
}

/** 保存时：裸 table 包回 doc-table，便于预览/方案生成统一样式 */
export function normalizeSynrFromWangEditor(html: string): string {
  const s = (html || '').trim();
  if (!s) return '';
  return s.replace(/<table[\s\S]*?<\/table>/gi, (tbl, offset) => {
    const before = s.slice(Math.max(0, offset - 80), offset);
    if (/doc-table/i.test(before)) return tbl;
    return `<div class="doc-table">${tbl}</div>`;
  });
}

export const SYNR_PREVIEW_STYLES = `
.flgl-method-synr-preview .doc-table { overflow-x: auto; margin: 10px 0; }
.flgl-method-synr-preview table,
.flgl-method-synr-preview .doc-table table {
  border-collapse: collapse;
  min-width: 240px;
  max-width: 100%;
  font-size: 13px;
}
.flgl-method-synr-preview th,
.flgl-method-synr-preview td {
  border: 1px solid #d9d9d9;
  padding: 6px 10px;
  vertical-align: middle;
  word-break: break-word;
}
.flgl-method-synr-preview th {
  background: #fafafa;
  font-weight: 600;
  text-align: center;
}
.flgl-method-synr-preview h4 {
  margin: 12px 0 8px;
  font-size: 15px;
}
.flgl-method-synr-preview .see-ref-inline {
  margin: 8px 0;
  padding: 8px 10px;
  background: #f6ffed;
  border-left: 3px solid #52c41a;
}
.flgl-method-synr-preview figure {
  margin: 12px 0;
  text-align: center;
}
.flgl-method-synr-preview .see-ref-inline figure {
  margin: 8px 0 0;
}
.flgl-method-synr-preview img {
  max-width: 100%;
  height: auto;
  cursor: zoom-in;
}
`;

/** WangEditor 编辑区内表格/图片样式（与环境 tab 抽屉一致，并适配方法 SYNR） */
export const WANGEDITOR_SYNR_STYLES = `
.flgl-method-synr-editor .w-e-text-container {
  font-size: 14px;
  line-height: 1.7;
}
.flgl-method-synr-editor .w-e-text-container h4 {
  margin: 14px 0 8px;
  font-size: 15px;
  font-weight: 600;
}
.flgl-method-synr-editor .w-e-text-container .wjgl-section-heading {
  font-weight: 600;
  margin: 10px 0 6px;
}
.flgl-method-synr-editor .w-e-text-container .see-ref-inline {
  margin: 8px 0;
  padding: 8px 10px;
  background: #f6ffed;
  border-left: 3px solid #52c41a;
}
.flgl-method-synr-editor .w-e-text-container .doc-table {
  overflow-x: auto;
  margin: 10px 0;
  max-width: 100%;
}
.flgl-method-synr-editor .w-e-text-container table {
  border-collapse: collapse;
  width: max-content;
  min-width: 60%;
  max-width: 100%;
  font-size: 13px;
}
.flgl-method-synr-editor .w-e-text-container th,
.flgl-method-synr-editor .w-e-text-container td {
  border: 1px solid #d9d9d9 !important;
  padding: 6px 10px;
  vertical-align: middle;
  word-break: break-word;
  min-width: 48px;
}
.flgl-method-synr-editor .w-e-text-container th {
  background: #fafafa;
  font-weight: 600;
  text-align: center;
}
.flgl-method-synr-editor .w-e-text-container img {
  max-width: 100%;
  height: auto;
  cursor: zoom-in;
}
`;
