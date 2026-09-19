import type { DataNode } from 'antd/es/tree';
import { wjglApiUrl, wjglUploadUrl } from '../utils/wjglApi';

/** 安全解析 API 响应，避免 500 HTML 导致 JSON.parse 报错。 */
export function extractApiErrorMessage(raw: string, status?: number): string {
  const trimmed = (raw || '').trim();
  if (!trimmed) return status ? `HTTP ${status}` : '请求失败';
  if (trimmed.startsWith('{') || trimmed.startsWith('[')) {
    try {
      const data = JSON.parse(trimmed) as Record<string, unknown>;
      const err = data.error ?? data.message ?? data.detail;
      if (typeof err === 'string' && err.trim()) return err.trim();
    } catch {
      /* fall through */
    }
  }
  const plain = trimmed
    .replace(/<[^>]+>/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
  if (/internal server error/i.test(plain)) {
    return status
      ? `服务返回 ${status}（Internal Server Error）。文件上传已改为直连 Flask（${typeof window !== 'undefined' ? '请确认 NEXT_PUBLIC_BACKEND_URL 指向 :5000 且 Flask 已启动' : '请检查后端'}）`
      : 'Internal Server Error，请确认 Flask 在 5000 端口运行';
  }
  return plain.slice(0, 300) || (status ? `HTTP ${status}` : '请求失败');
}

export async function parseJsonResponse(res: Response): Promise<Record<string, unknown>> {
  const text = await res.text();
  const trimmed = text.trim();
  if (trimmed.startsWith('{') || trimmed.startsWith('[')) {
    try {
      return JSON.parse(trimmed) as Record<string, unknown>;
    } catch {
      throw new Error(extractApiErrorMessage(trimmed, res.status));
    }
  }
  throw new Error(extractApiErrorMessage(trimmed, res.status));
}

/** 与 /api/wjgl1/document-toc 返回结构一致 */
export interface TocItem {
  sectionNo?: string;
  title: string;
  level?: number;
  section_id?: string;
  children?: TocItem[];
}

export type TocTreeNode = DataNode & {
  sectionNo?: string;
  rawTitle?: string;
  level?: number;
  section_id?: string;
};

/** 避免「3 3 术语」：标题已含章节号时不再重复拼接。 */
export function formatTocDisplayTitle(sectionNo?: string, title?: string): string {
  const t = (title || '').trim();
  const sn = (sectionNo || '').trim();
  if (!sn) return t;
  const esc = sn.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  let body = t.replace(new RegExp(`^${esc}[\\s．.、]+`), '').trim();
  if (body === t && t.startsWith(sn) && t.length > sn.length) {
    body = t.slice(sn.length).replace(/^[．.、\s]+/, '').trim();
  }
  if (!body) body = t;
  return body ? `${sn} ${body}` : sn;
}

export function sectionNoSortKey(sectionNo?: string): string[] {
  const parts = (sectionNo || '').trim().replace(/^\.+/, '').split('.');
  return parts.map((p) => {
    if (!p) return '';
    if (/^\d+$/.test(p)) return p.padStart(8, '0');
    return `z${p.toLowerCase()}`;
  });
}

export function compareSectionNo(a?: string, b?: string): number {
  const ka = sectionNoSortKey(a);
  const kb = sectionNoSortKey(b);
  const len = Math.max(ka.length, kb.length);
  for (let i = 0; i < len; i += 1) {
    const va = ka[i] ?? '';
    const vb = kb[i] ?? '';
    if (va < vb) return -1;
    if (va > vb) return 1;
  }
  return 0;
}

function sortTocItems(items: TocItem[]): TocItem[] {
  return [...items]
    .sort((a, b) => compareSectionNo(a.sectionNo, b.sectionNo))
    .map((item) => ({
      ...item,
      children: item.children?.length ? sortTocItems(item.children) : item.children,
    }));
}

export function buildTocTreeNodes(
  items: TocItem[],
  prefix = '',
  level = 1,
  options?: { preserveOrder?: boolean },
): TocTreeNode[] {
  const ordered = options?.preserveOrder ? items : sortTocItems(items);
  return ordered.map((item, index) => {
    // Tree key 必须全局唯一；sectionNo 在部分文档中会重复（如多个「4」），故用路径索引
    const key = `${prefix}${index}`;
    const displayTitle = formatTocDisplayTitle(item.sectionNo, item.title);
    const children = item.children?.length
      ? buildTocTreeNodes(item.children, `${key}-`, level + 1, options)
      : undefined;
    return {
      title: displayTitle,
      key,
      children,
      sectionNo: item.sectionNo,
      rawTitle: item.title,
      level: item.level ?? level,
      section_id: (item as TocItem & { section_id?: string }).section_id,
    };
  });
}

/** 将前端树节点还原为 API 目录结构，便于写入元数据持久化 */
export function tocTreeNodesToItems(nodes: TocTreeNode[]): TocItem[] {
  return (nodes || []).map((node) => ({
    sectionNo: node.sectionNo,
    title: (node.rawTitle || String(node.title || '')).trim(),
    level: node.level,
    section_id: node.section_id,
    children: node.children?.length
      ? tocTreeNodesToItems(node.children as TocTreeNode[])
      : undefined,
  }));
}

/** 单次获取已入库文件的目录（优先 saved_toc，不轮询等待后台解析） */
export async function fetchSavedDocumentToc(options: {
  docId?: string;
  objectName?: string;
  category?: string;
}): Promise<TocItem[]> {
  const params = new URLSearchParams();
  if (options.objectName) params.set('object_name', options.objectName);
  else if (options.docId) params.set('doc_id', options.docId);
  if (options.category) params.set('category', options.category);
  const res = await fetch(`/api/wjgl1/document-toc?${params.toString()}`);
  const data = await fetchJsonSafe(res);
  if (!data.success) {
    throw new Error(String(data.error || '获取目录失败'));
  }
  const toc: TocItem[] = (data.data as { toc?: TocItem[] })?.toc || [];
  const source = String((data.data as { source?: string })?.source || '');
  if (toc.length > 0 && source === 'saved_toc') {
    return toc;
  }
  if (toc.length > 0) {
    return toc;
  }
  return [];
}

export interface LibraryDocumentContent {
  html: string;
  markdown: string;
  toc: TocItem[];
  contentSource?: string;
}

/** 加载已入库文件的目录 + 全文：先读 preview 落盘，再 fallback 轮询 OCR */
export async function loadLibraryDocumentContent(options: {
  docId: string;
  category: string;
  objectName?: string;
  onProgress?: (msg: string) => void;
}): Promise<LibraryDocumentContent> {
  const { docId, category, objectName, onProgress } = options;

  let toc = await fetchSavedDocumentToc({ docId, objectName, category });
  if (!toc.length) {
    onProgress?.('正在获取文档目录…');
    toc = await pollDocumentToc({
      docId,
      objectName,
      category,
      onProgress,
    });
  }

  let html = '';
  let markdown = '';
  let contentSource = '';

  try {
    html = await fetchFullDocumentHtml(docId, category);
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : String(e);
    if (msg !== 'PARSE_PENDING' && !msg.includes('409')) {
      /* 继续尝试 markdown / 轮询 */
    }
  }
  try {
    markdown = await fetchFullDocumentMarkdown(docId, category);
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : String(e);
    if (msg !== 'PARSE_PENDING' && !msg.includes('409')) {
      /* 继续轮询 */
    }
  }

  if (html.trim() || markdown.trim()) {
    contentSource = 'preview';
  } else {
    onProgress?.('正在加载正文…');
    const polled = await pollFullDocumentContent(docId, category, onProgress);
    html = polled.html;
    markdown = polled.markdown;
    contentSource = 'parse_full';
  }

  return { html, markdown, toc, contentSource };
}

export function flattenTocTree(nodes: TocTreeNode[]): TocTreeNode[] {
  const out: TocTreeNode[] = [];
  for (const node of nodes) {
    out.push(node);
    if (node.children?.length) {
      out.push(...flattenTocTree(node.children as TocTreeNode[]));
    }
  }
  return out;
}

export function findTocNode(nodes: TocTreeNode[], key: string): TocTreeNode | null {
  for (const node of nodes) {
    if (String(node.key) === key) return node;
    if (node.children?.length) {
      const hit = findTocNode(node.children as TocTreeNode[], key);
      if (hit) return hit;
    }
  }
  return null;
}

export function firstTocNodeKey(nodes: TocTreeNode[]): string {
  const flat = flattenTocTree(nodes);
  return flat.length ? String(flat[0].key) : '';
}

export interface SectionExtractPayload {
  title?: string;
  sectionNo?: string;
  content?: string;
  tables?: string[];
  images?: { base64?: string }[];
  html?: string;
}

/** 将 method-extract / document-section-content 返回转为编辑器 HTML */
export function sectionExtractToHtml(data: SectionExtractPayload): string {
  if (data.html) return data.html;
  const parts: string[] = [];
  const content = (data.content || '').trim();
  if (content) {
    for (const line of content.split('\n')) {
      const s = line.trim();
      if (s) parts.push(`<p>${escapeHtml(s)}</p>`);
    }
  }
  for (const t of data.tables || []) {
    if (t) parts.push(`<div class="doc-table">${t}</div>`);
  }
  for (const img of data.images || []) {
    if (img.base64) {
      parts.push(`<figure><img src="${img.base64}" style="max-width:100%"/></figure>`);
    }
  }
  return parts.join('');
}

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

/** 安全解析 fetch 响应，避免 HTML 错误页导致 JSON.parse 崩溃 */
export async function fetchJsonSafe(res: Response): Promise<any> {
  const text = await res.text();
  const trimmed = text.trim();
  if (trimmed.startsWith('{') || trimmed.startsWith('[')) {
    try {
      return JSON.parse(trimmed);
    } catch {
      throw new Error(trimmed.slice(0, 160) || `HTTP ${res.status}`);
    }
  }
  if (!res.ok) {
    throw new Error(trimmed.slice(0, 160) || `HTTP ${res.status}`);
  }
  throw new Error(trimmed.slice(0, 160) || '服务器返回非 JSON 响应');
}

export interface DocumentTocPollOptions {
  docId?: string;
  objectName?: string;
  category?: string;
  onProgress?: (message: string) => void;
  maxWaitMs?: number;
  intervalMs?: number;
}

/** 轮询 document-toc，直到目录就绪或超时（后台 OCR 未完成时自动等待） */
export async function pollDocumentToc(options: DocumentTocPollOptions): Promise<TocItem[]> {
  const {
    docId,
    objectName,
    category,
    onProgress,
    maxWaitMs = 15 * 60 * 1000,
    intervalMs = 3000,
  } = options;
  const started = Date.now();
  let attempt = 0;
  let lastMsg = '';

  while (Date.now() - started < maxWaitMs) {
    attempt += 1;
    const params = new URLSearchParams();
    if (objectName) params.set('object_name', objectName);
    else if (docId) params.set('doc_id', docId);
    if (category) params.set('category', category);

    const res = await fetch(`/api/wjgl1/document-toc?${params.toString()}`);
    const data = await fetchJsonSafe(res);

    if (!data.success) {
      throw new Error(data.error || '获取目录失败');
    }

    const toc: TocItem[] = data.data?.toc || [];
    if (toc.length > 0) {
      return toc;
    }

    const pending = !!data.data?.pending;
    const msg = pending || data.data?.message
      ? (data.data?.message || '文档后台解析中，正在等待目录…')
      : '正在读取文档目录…';

    if (msg !== lastMsg) {
      lastMsg = msg;
      onProgress?.(msg);
    }

    if (!pending && attempt > 1) {
      break;
    }

    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }

  throw new Error('获取目录超时：文档仍在后台解析，请稍后再试');
}

export async function fetchDocumentToc(docId: string, objectName: string, category?: string): Promise<TocItem[]> {
  return pollDocumentToc({ docId, objectName, category });
}

export function tocNodeTitle(node: TocTreeNode | null | undefined): string {
  if (!node) return '章节内容';
  return formatTocDisplayTitle(node.sectionNo, String(node.rawTitle || node.title || ''));
}

export function normalizeSectionNo(sectionNo: string): string {
  return (sectionNo || '').trim().replace(/^\.+/, '');
}

export function sectionNoVariants(sectionNo: string): string[] {
  const raw = (sectionNo || '').trim();
  const sn = normalizeSectionNo(raw);
  const out = new Set<string>();
  if (raw) out.add(raw);
  if (sn) {
    out.add(sn);
    out.add(`.${sn}`);
  }
  return [...out];
}

export function sectionAnchorId(sectionNo: string): string {
  const n = normalizeSectionNo(sectionNo);
  return n ? `wjgl-sec-${n.replace(/\./g, '-')}` : '';
}

/** 在全文容器内滚动到章节锚点并短暂高亮 */
export function scrollToSectionAnchor(
  container: HTMLElement | null,
  sectionNo: string,
  scrollParent?: HTMLElement | null,
): boolean {
  if (!container || !sectionNo) return false;

  const pick = (): HTMLElement | null => {
    for (const sn of sectionNoVariants(sectionNo)) {
      const id = sectionAnchorId(sn);
      let el = container.querySelector(`#${CSS.escape(id)}`) as HTMLElement | null;
      if (el) return el;
      const legacyId = `sec-${sn.replace(/\./g, '-')}`;
      el = container.querySelector(`#${CSS.escape(legacyId)}`) as HTMLElement | null;
      if (el) return el;
      el = container.querySelector(`#${CSS.escape(`sec-${sn}`)}`) as HTMLElement | null;
      if (el) return el;
      el = container.querySelector(`[data-section-no="${CSS.escape(sn)}"]`) as HTMLElement | null;
      if (el) return el;
    }
    const headings = container.querySelectorAll('.wjgl-section-heading[data-section-no]');
    for (const node of headings) {
      const no = normalizeSectionNo((node as HTMLElement).getAttribute('data-section-no') || '');
      for (const sn of sectionNoVariants(sectionNo)) {
        if (no === normalizeSectionNo(sn)) return node as HTMLElement;
      }
    }
    return null;
  };

  const el = pick();
  if (!el) return false;
  el.classList.add('wjgl-section-highlight');
  window.setTimeout(() => el.classList.remove('wjgl-section-highlight'), 2500);

  const scroller = scrollParent || container;
  if (scroller && scroller !== document.documentElement && scroller !== document.body) {
    const top = el.getBoundingClientRect().top - scroller.getBoundingClientRect().top + scroller.scrollTop - 12;
    scroller.scrollTo({ top: Math.max(0, top), behavior: 'smooth' });
  } else {
    el.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
  return true;
}

/** 根据滚动位置解析当前可见章节号 */
export function resolveActiveSectionNo(container: HTMLElement): string {
  const headings = Array.from(
    container.querySelectorAll<HTMLElement>('.wjgl-section-heading[data-section-no]'),
  );
  if (!headings.length) return '';

  const rootRect = container.getBoundingClientRect();
  const threshold = rootRect.top + 56;
  let active = '';

  for (const heading of headings) {
    const rect = heading.getBoundingClientRect();
    const sectionNo = normalizeSectionNo(heading.getAttribute('data-section-no') || '');
    if (!sectionNo) continue;
    if (rect.top <= threshold) {
      active = sectionNo;
      continue;
    }
    if (!active) active = sectionNo;
    break;
  }
  return active || normalizeSectionNo(headings[0].getAttribute('data-section-no') || '');
}

export function findTocNodeBySectionNo(nodes: TocTreeNode[], sectionNo: string): TocTreeNode | null {
  const target = normalizeSectionNo(sectionNo);
  if (!target) return null;
  let best: TocTreeNode | null = null;
  for (const node of flattenTocTree(nodes)) {
    if (normalizeSectionNo(node.sectionNo || '') === target) {
      best = node;
    }
  }
  return best;
}

/** 阅读器滚动时同步目录高亮 */
export function setupDocumentScrollSpy(
  container: HTMLElement | null,
  onActiveSectionNo: (sectionNo: string) => void,
): () => void {
  if (!container) return () => undefined;

  let raf = 0;
  let last = '';

  const emit = () => {
    const next = resolveActiveSectionNo(container);
    if (next && next !== last) {
      last = next;
      onActiveSectionNo(next);
    }
  };

  const onScroll = () => {
    cancelAnimationFrame(raf);
    raf = requestAnimationFrame(emit);
  };

  container.addEventListener('scroll', onScroll, { passive: true });
  window.setTimeout(emit, 150);

  return () => {
    container.removeEventListener('scroll', onScroll);
    cancelAnimationFrame(raf);
  };
}

export async function pollFullDocumentHtml(
  docId: string,
  category: string,
  onProgress?: (msg: string) => void,
  maxWaitMs = 20 * 60 * 1000,
): Promise<string> {
  const started = Date.now();
  let attempt = 0;
  while (Date.now() - started < maxWaitMs) {
    attempt += 1;
    try {
      return await fetchFullDocumentHtml(docId, category);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      if (msg !== 'PARSE_PENDING' && !msg.includes('409')) {
        throw e;
      }
      onProgress?.('文档 MinerU 解析中，请稍候…');
      await new Promise((r) => setTimeout(r, 4000));
      if (attempt > 3 && attempt % 5 === 0) {
        onProgress?.('仍在解析，大型 PDF 可能需要数分钟…');
      }
    }
  }
  throw new Error('文档解析超时，请稍后刷新页面');
}

export async function fetchFullDocumentHtml(docId: string, category: string): Promise<string> {
  const res = await fetch(
    `/api/wjgl1/files/${encodeURIComponent(docId)}/full-document?category=${encodeURIComponent(category)}&format=html`,
  );
  const ct = (res.headers.get('content-type') || '').toLowerCase();
  if (ct.includes('text/html')) {
    if (res.status === 409) throw new Error('PARSE_PENDING');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const html = await res.text();
    if (!html.trim()) throw new Error('正文为空');
    return html;
  }
  const data = await parseJsonResponse(res);
  if (!data.success) {
    if (data.code === 'parse_pending') {
      throw new Error('PARSE_PENDING');
    }
    throw new Error(String(data.error || '加载全文失败'));
  }
  return String((data.data as { html?: string })?.html || '');
}

export async function fetchPreviewGraphLinks(meta: {
  std_name?: string;
  std_code?: string;
  abstract?: string;
  keywords?: string[];
  title?: string;
}) {
  const res = await fetch('/api/wjgl1/preview-graph-links', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      std_name: meta.std_name,
      std_code: meta.std_code,
      abstract: meta.abstract,
      keywords: meta.keywords,
      title: meta.title || meta.std_name,
    }),
  });
  const data = await parseJsonResponse(res);
  if (!res.ok && !data.success) {
    throw new Error(String(data.error || `HTTP ${res.status}`));
  }
  return data;
}

export async function enrichMetadataFromText(
  text: string,
  base?: {
    std_name?: string;
    std_code?: string;
    abstract?: string;
    keywords?: string[];
    publish_date?: string;
    issuer?: string;
  },
) {
  const res = await fetch('/api/wjgl1/auto-parse-text', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text, markdown: text, ...base }),
  });
  const data = await parseJsonResponse(res);
  if (!data.success) throw new Error(String(data.error || 'LLM 元数据补全失败'));
  return data.data as {
    abstract?: string;
    keywords?: string[];
    std_code?: string;
    std_name?: string;
    publish_date?: string;
    issuer?: string;
    llm_used?: boolean;
    warnings?: string[];
  };
}

export interface MineruPreviewResult {
  html: string;
  markdown?: string;
  mdPath?: string;
  localDir?: string;
  toc?: TocItem[];
  mineruCached?: boolean;
  parseMode?: string;
}

export async function fetchFullDocumentMarkdown(docId: string, category: string): Promise<string> {
  const res = await fetch(
    `/api/wjgl1/files/${encodeURIComponent(docId)}/full-document?category=${encodeURIComponent(category)}&format=md`,
  );
  const ct = (res.headers.get('content-type') || '').toLowerCase();
  if (ct.includes('text/markdown') || ct.includes('text/plain')) {
    if (res.status === 409) throw new Error('PARSE_PENDING');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const md = await res.text();
    if (!md.trim()) throw new Error('正文为空');
    return md;
  }
  const data = await parseJsonResponse(res);
  if (!data.success) {
    if (data.code === 'parse_pending') {
      throw new Error('PARSE_PENDING');
    }
    throw new Error(String(data.error || '加载 Markdown 失败'));
  }
  return String((data.data as { markdown?: string })?.markdown || '');
}

export async function pollFullDocumentContent(
  docId: string,
  category: string,
  onProgress?: (msg: string) => void,
  maxWaitMs = 20 * 60 * 1000,
): Promise<{ html: string; markdown: string }> {
  const started = Date.now();
  let attempt = 0;
  while (Date.now() - started < maxWaitMs) {
    attempt += 1;
    try {
      let markdown = '';
      let html = '';
      try {
        markdown = await fetchFullDocumentMarkdown(docId, category);
      } catch (e: unknown) {
        const msg = e instanceof Error ? e.message : String(e);
        if (msg === 'PARSE_PENDING' || msg.includes('409')) throw e;
      }
      try {
        html = await fetchFullDocumentHtml(docId, category);
      } catch (e: unknown) {
        const msg = e instanceof Error ? e.message : String(e);
        if (msg === 'PARSE_PENDING' || msg.includes('409')) throw e;
      }
      if (markdown.trim() || html.trim()) {
        return { markdown, html };
      }
      throw new Error('PARSE_PENDING');
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      if (msg !== 'PARSE_PENDING' && !msg.includes('409')) {
        throw e;
      }
      onProgress?.('文档 MinerU 解析中，请稍候…');
      await new Promise((r) => setTimeout(r, 4000));
      if (attempt > 3 && attempt % 5 === 0) {
        onProgress?.('仍在解析，大型 PDF 可能需要数分钟…');
      }
    }
  }
  throw new Error('文档解析超时，请稍后刷新页面');
}

export async function fetchFullDocumentHtmlUpload(file: File): Promise<{
  html: string;
  mineruKey?: string;
  mineruOnly?: boolean;
}> {
  const fd = new FormData();
  fd.append('file', file);
  const res = await fetch(wjglUploadUrl('/document-full-content-upload?format=html'), {
    method: 'POST',
    body: fd,
  });
  const mineruKey = (res.headers.get('X-WJGL-MinerU-Key') || '').trim() || undefined;
  const previewMode = (res.headers.get('X-WJGL-Preview-Mode') || '').trim().toLowerCase();
  const mineruOnly = previewMode === 'mineru';
  const ct = (res.headers.get('content-type') || '').toLowerCase();
  if (!res.ok && !ct.includes('text/html')) {
    const errText = (await res.text().catch(() => '')).trim();
    throw new Error(extractApiErrorMessage(errText, res.status));
  }
  if (ct.includes('text/html')) {
    if (!res.ok) {
      const errText = await res.text().catch(() => '');
      throw new Error(extractApiErrorMessage(errText, res.status));
    }
    const html = await res.text();
    if (!html.trim() && !mineruKey && !mineruOnly) throw new Error('正文为空');
    return { html, mineruKey, mineruOnly };
  }
  const data = await parseJsonResponse(res);
  if (!data.success) throw new Error(String(data.error || '加载全文失败'));
  const html = String((data.data as { html?: string })?.html || '');
  if (!html.trim() && !mineruKey && !mineruOnly) throw new Error('正文为空');
  return { html, mineruKey: (data.data as { mineru_key?: string })?.mineru_key || mineruKey, mineruOnly };
}

export async function fetchMineruPreviewContent(
  cacheKey: string,
  format: 'html' | 'md' = 'html',
): Promise<string> {
  const res = await fetch(
    `/api/wjgl1/document-mineru-parse/content?key=${encodeURIComponent(cacheKey)}&format=${format}`,
  );
  const ct = (res.headers.get('content-type') || '').toLowerCase();
  if (ct.includes('text/html') || ct.includes('text/markdown') || ct.includes('text/plain')) {
    if (res.status === 409) throw new Error('PARSE_PENDING');
    if (!res.ok) {
      const errText = (await res.text().catch(() => '')).trim();
      throw new Error(errText || `HTTP ${res.status}`);
    }
    return await res.text();
  }
  const data = await parseJsonResponse(res);
  if (!res.ok || !data.success) {
    if (data.code === 'parse_pending') throw new Error('PARSE_PENDING');
    throw new Error(String(data.error || `HTTP ${res.status}`));
  }
  return String((data.data as { content?: string })?.content || '');
}

export function formatMineruDoneMessage(task: {
  mineru_cached?: boolean;
  mineruCached?: boolean;
  parse_mode?: string;
  parseMode?: string;
  md_path?: string;
  mdPath?: string;
}): string {
  const cached = Boolean(task.mineru_cached ?? task.mineruCached);
  const mdPath = String(task.md_path || task.mdPath || '').trim();
  if (cached) {
    return mdPath
      ? `MinerU 缓存命中（本次未重新解析），${mdPath}`
      : 'MinerU 缓存命中（本次未重新解析）';
  }
  return mdPath ? `MinerU 完成，已保存 ${mdPath}` : 'MinerU 解析完成';
}

export function formatMineruPollMessage(task: Record<string, unknown>): string {
  const msg = String(task.message || '').trim();
  const elapsed = typeof task.elapsed_sec === 'number' ? task.elapsed_sec : 0;
  const mins = Math.floor(elapsed / 60);
  const secs = elapsed % 60;
  const timeLabel = elapsed > 0 ? `（已用时 ${mins > 0 ? `${mins}分` : ''}${secs}秒）` : '';
  if (msg) return `${msg}${timeLabel}`;
  if (task.status === 'missing') return '未找到解析任务，可能后端已重启';
  if (task.mineru_cached) return 'MinerU 缓存命中（未重新解析）';
  if (task.has_draft_html) return `fitz 草稿已显示，MinerU 后台解析中${timeLabel}…`;
  return `MinerU 解析中${timeLabel}…`;
}

/** 2 小时：7200s / 5s，与后端 MINERU_PREVIEW_STALE_SEC 默认 2h 对齐 */
export const MINERU_POLL_MAX_ATTEMPTS = 1440;
export const MINERU_POLL_INTERVAL_MS = 5000;
/** status=missing 连续次数上限（36×5s≈3 分钟） */
export const MINERU_POLL_MISSING_TOLERANCE = 36;

export async function fetchMineruPreviewStatus(cacheKey: string): Promise<Record<string, unknown>> {
  const res = await fetch(`/api/wjgl1/document-mineru-parse/status?key=${encodeURIComponent(cacheKey)}`);
  const data = await parseJsonResponse(res);
  if (!data.success) throw new Error(String(data.error || '查询 MinerU 状态失败'));
  return (data.data || {}) as Record<string, unknown>;
}

export function isMineruPollTimeout(err: unknown): boolean {
  const msg = err instanceof Error ? err.message : String(err);
  return msg.includes('MinerU 解析超时');
}

export function canResumeMineruPoll(item: {
  mineruKey?: string;
  contentDocId?: string;
  mineruStatus?: string;
  contentError?: string;
  mineruLoading?: boolean;
}): boolean {
  const key = (item.mineruKey || item.contentDocId || '').trim();
  if (!key) return false;
  if (item.mineruLoading) return true;
  const hint = `${item.mineruStatus || ''} ${item.contentError || ''}`;
  return /超时|继续等待|解析中|心跳|失败|中断|未找到|仍在后台/i.test(hint);
}

export function mineruStatusAlertType(
  mineruLoading?: boolean,
  mineruStatus?: string,
): 'info' | 'success' | 'warning' | 'error' {
  if (mineruLoading) return 'info';
  const s = mineruStatus || '';
  if (/失败|超时|中断|错误/i.test(s)) return 'warning';
  if (/完成|缓存命中/i.test(s)) return 'success';
  return 'info';
}

export async function loadMineruPreviewResult(cacheKey: string): Promise<MineruPreviewResult> {
  const task = await fetchMineruPreviewStatus(cacheKey);
  if (task.status !== 'done') {
    throw new Error(String(task.error || 'MinerU 尚未完成'));
  }
  let html = typeof task.html === 'string' ? task.html : '';
  let markdown = typeof task.markdown === 'string' ? task.markdown : '';
  if (!html.trim() && task.has_html) {
    html = await fetchMineruPreviewContent(cacheKey, 'html');
  }
  if (!markdown.trim() && task.has_markdown) {
    markdown = await fetchMineruPreviewContent(cacheKey, 'md');
  }
  if (!html.trim() && markdown.trim()) html = markdown;
  if (!html.trim() && !markdown.trim()) throw new Error('MinerU 解析结果为空');
  return {
    html,
    markdown,
    mdPath: task.md_path as string | undefined,
    localDir: task.local_dir as string | undefined,
    toc: Array.isArray(task.toc) ? (task.toc as TocItem[]) : undefined,
    mineruCached: Boolean(task.mineru_cached),
    parseMode: typeof task.parse_mode === 'string' ? task.parse_mode : undefined,
  };
}

export async function pollMineruPreviewHtml(
  cacheKey: string,
  opts?: {
    maxAttempts?: number;
    intervalMs?: number;
    onProgress?: (task: Record<string, unknown>) => void;
  },
): Promise<MineruPreviewResult> {
  const maxAttempts = opts?.maxAttempts ?? MINERU_POLL_MAX_ATTEMPTS;
  const intervalMs = opts?.intervalMs ?? MINERU_POLL_INTERVAL_MS;
  let missingStreak = 0;
  for (let i = 0; i < maxAttempts; i += 1) {
    const res = await fetch(`/api/wjgl1/document-mineru-parse/status?key=${encodeURIComponent(cacheKey)}`);
    const data = await parseJsonResponse(res);
    if (!data.success) throw new Error(String(data.error || '查询 MinerU 状态失败'));
    const task = (data.data || {}) as Record<string, unknown>;
    opts?.onProgress?.(task);
    if (task.status === 'done') {
      let html = typeof task.html === 'string' ? task.html : '';
      let markdown = typeof task.markdown === 'string' ? task.markdown : '';
      if (!html.trim() && task.has_html) {
        html = await fetchMineruPreviewContent(cacheKey, 'html');
      }
      if (!markdown.trim() && task.has_markdown) {
        markdown = await fetchMineruPreviewContent(cacheKey, 'md');
      }
      if (!html.trim() && markdown.trim()) {
        html = markdown;
      }
      if (!html.trim() && !markdown.trim()) throw new Error('MinerU 解析结果为空');
      const toc = Array.isArray(task.toc) ? (task.toc as TocItem[]) : undefined;
      return {
        html,
        markdown,
        mdPath: task.md_path as string | undefined,
        localDir: task.local_dir as string | undefined,
        toc,
        mineruCached: Boolean(task.mineru_cached),
        parseMode: typeof task.parse_mode === 'string' ? task.parse_mode : undefined,
      };
    }
    if (task.status === 'error') throw new Error(String(task.error || 'MinerU 解析失败'));
    if (task.status === 'missing') {
      missingStreak += 1;
      if (missingStreak >= MINERU_POLL_MISSING_TOLERANCE) {
        throw new Error(
          '未找到 MinerU 解析任务（后端可能已重启）。可点「继续等待」重试；若仍失败请「强制重新解析」',
        );
      }
    } else {
      missingStreak = 0;
    }
    await new Promise(r => setTimeout(r, intervalMs));
  }
  const timeoutMin = Math.round((maxAttempts * intervalMs) / 60000);
  throw new Error(
    `MinerU 解析超时（约${timeoutMin}分钟）。后台可能仍在解析，请点「继续等待」续轮询，勿点「强制重新解析」以免重头开始`,
  );
}
