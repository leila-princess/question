import { enrichMetadataFromText, parseJsonResponse } from '../components/documentTocUtils';
import type { TocItem } from '../components/documentTocUtils';
import type { UploadFileItem } from '../components/UploadModal';
import { wjglApiUrl, wjglUploadUrl } from './wjglApi';

export type UploadFileMeta = UploadFileItem['meta'];

export function getWjglUploadUserLabel(): string {
  if (typeof window === 'undefined') return '未登录用户';
  return (localStorage.getItem('userName') || '').trim().slice(0, 80) || '未登录用户';
}

export function fallbackMetaFromFileName(fileName: string): UploadFileMeta {
  const baseName = fileName.replace(/\.[^/.]+$/, '');
  const ext = fileName.split('.').pop() || '';
  return {
    title: baseName,
    std_name: baseName,
    std_code: '',
    publish_date: '',
    issuer: '',
    abstract: '',
    keywords: [],
    file_type: ext,
  };
}

export function metaFromAutoParseData(
  data: Record<string, unknown>,
  fileName: string,
): UploadFileMeta {
  const fallback = fallbackMetaFromFileName(fileName);
  return {
    title: String(data.std_name || data.title || fallback.title),
    std_name: String(data.std_name || ''),
    std_code: String(data.std_code || ''),
    publish_date: String(data.publish_date || ''),
    issuer: String(data.issuer || ''),
    abstract: String(data.abstract || ''),
    keywords: Array.isArray(data.keywords) ? (data.keywords as string[]) : [],
    file_type: String(data.file_type || fallback.file_type),
  };
}

/** 调用 auto-parse 提取元数据（不含正文预览） */
export async function parseUploadFileMetadata(file: File): Promise<{
  meta: UploadFileMeta;
  warnings: string[];
}> {
  const metaFd = new FormData();
  metaFd.append('file', file);
  const metaRes = await fetch(wjglUploadUrl('/auto-parse'), { method: 'POST', body: metaFd });
  const metaJson = await parseJsonResponse(metaRes);
  if (!metaJson.success) {
    throw new Error(String(metaJson.error || '元数据提取失败'));
  }
  const m = metaJson.data as Record<string, unknown>;
  return {
    meta: metaFromAutoParseData(m, file.name),
    warnings: Array.isArray(m.warnings) ? (m.warnings as string[]) : [],
  };
}

/** MinerU markdown 补全元数据 */
export async function enrichUploadMetaFromMarkdown(
  markdown: string,
  meta: UploadFileMeta,
): Promise<{ meta: UploadFileMeta; warnings: string[] }> {
  const warnings: string[] = [];
  if (!markdown.trim()) return { meta, warnings };
  try {
    const enriched = await enrichMetadataFromText(markdown.slice(0, 8000), meta);
    const nextMeta: UploadFileMeta = {
      ...meta,
      std_name: enriched.std_name || meta.std_name,
      std_code: enriched.std_code || meta.std_code,
      publish_date: enriched.publish_date || meta.publish_date,
      issuer: enriched.issuer || meta.issuer,
      abstract: enriched.abstract || meta.abstract,
      keywords: enriched.keywords?.length ? enriched.keywords : meta.keywords,
      title: enriched.std_name || meta.title,
    };
    if (Array.isArray(enriched.warnings)) {
      warnings.push(...enriched.warnings);
    }
    return { meta: nextMeta, warnings };
  } catch (err: unknown) {
    warnings.push(err instanceof Error ? err.message : 'MinerU 正文元数据补全失败');
    return { meta, warnings };
  }
}

export interface CommitWjglUploadParams {
  file: File;
  uid: string;
  meta: UploadFileMeta;
  category: string;
  syncChroma: boolean;
  /** 写入 MinIO file-label，用于区分来源（如试验方案：系统同步 / 本地上传） */
  fileLabel?: string;
  duplicateAction?: 'replace' | 'skip' | 'check';
  userId?: string;
  /** 上传预览时已解析的目录，入库后写入 toc.json / Neo4j，避免后续重复解析 */
  documentToc?: TocItem[];
  /** 上传预览时的全文，入库后写入 parse 目录，供审核/方法提取直接加载 */
  documentContent?: {
    html?: string;
    markdown?: string;
    mineruKey?: string;
  };
}

export interface CommitWjglUploadResult {
  doc_id: string;
  object_name: string;
  duplicate?: boolean;
}

export async function resolveMetaForLibraryCommit(
  entry: Pick<UploadFileItem, 'file' | 'name' | 'meta' | 'fullMarkdown' | 'fullHtml'>,
): Promise<UploadFileMeta> {
  let meta: UploadFileMeta = { ...(entry.meta || fallbackMetaFromFileName(entry.name)) };
  if (entry.file?.size) {
    try {
      const parsed = await parseUploadFileMetadata(entry.file);
      meta = { ...meta, ...parsed.meta };
    } catch {
      /* 保留已有 meta */
    }
  }
  const md = String(entry.fullMarkdown || '').trim();
  if (md) {
    try {
      const enriched = await enrichUploadMetaFromMarkdown(md.slice(0, 8000), meta);
      meta = enriched.meta;
    } catch {
      /* 保留 auto-parse 结果 */
    }
  }
  if (!meta.title?.trim()) {
    meta.title = meta.std_name || entry.name.replace(/\.[^/.]+$/, '');
  }
  if (!meta.std_name?.trim()) {
    meta.std_name = meta.title;
  }
  return meta;
}

/** 上传文件至 MinIO + Neo4j，并 PATCH 元数据（不含 graph-link-confirm） */
export async function commitWjglFileUpload(
  params: CommitWjglUploadParams,
): Promise<CommitWjglUploadResult> {
  const {
    file,
    uid,
    meta,
    category,
    syncChroma,
    fileLabel,
    duplicateAction = 'skip',
    userId = getWjglUploadUserLabel(),
    documentToc,
    documentContent,
  } = params;

  const formData = new FormData();
  formData.append('files', file);
  formData.append(`uid_${file.name}`, uid);
  formData.append(`title_${uid}`, meta.std_name || meta.title);
  formData.append(`std_name_${uid}`, meta.std_name);
  formData.append(`std_code_${uid}`, meta.std_code);
  formData.append(`publish_date_${uid}`, meta.publish_date);
  formData.append(`issuer_${uid}`, meta.issuer);
  formData.append(`abstract_${uid}`, meta.abstract);
  formData.append(`keywords_${uid}`, JSON.stringify(meta.keywords));
  formData.append(`sync_chroma_${uid}`, String(syncChroma));
  formData.append(`associations_${uid}`, '[]');
  formData.append('category', category);
  formData.append('user_id', userId);
  formData.append('duplicate_action', duplicateAction);
  formData.append('review_mode', '1');
  if (fileLabel) {
    formData.append('label', fileLabel);
  }

  const uploadRes = await fetch(wjglUploadUrl('/upload'), { method: 'POST', body: formData });
  const uploadJson = await parseJsonResponse(uploadRes);
  if (!uploadJson.success || !Array.isArray(uploadJson.data)) {
    throw new Error(String(uploadJson.error || '入库失败'));
  }
  const row = (uploadJson.data as Array<{
    success?: boolean;
    doc_id?: string;
    object_name?: string;
    minio_id?: string;
    duplicate?: boolean;
  }>).find((d) => d.success);
  if (!row?.doc_id) throw new Error('上传未返回 doc_id');

  const patchBody: Record<string, unknown> = {
    category,
    ...meta,
    title: meta.std_name || meta.title,
    keywords: meta.keywords,
  };
  if (documentToc?.length) {
    patchBody.document_toc = documentToc;
  }
  const html = documentContent?.html?.trim();
  const markdown = documentContent?.markdown?.trim();
  const mineruKey = documentContent?.mineruKey?.trim();
  if (html || markdown || mineruKey) {
    if (html) patchBody.full_html = html;
    if (markdown) patchBody.full_markdown = markdown;
    if (mineruKey) patchBody.mineru_key = mineruKey;
  }
  try {
    await fetch(wjglApiUrl(`/files/${encodeURIComponent(row.doc_id)}/metadata`), {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patchBody),
    });
  } catch {
    /* 入库已成功，元数据 PATCH 失败不阻断主流程 */
  }

  const objectName = String(
    row.object_name || row.minio_id || `${category}/${row.doc_id}/origin.${meta.file_type}`,
  );
  return {
    doc_id: row.doc_id,
    object_name: objectName,
    duplicate: row.duplicate,
  };
}

export interface SyncWjglMetadataParams {
  docId: string;
  meta: UploadFileMeta;
  category: string;
  documentToc?: TocItem[];
  documentContent?: {
    html?: string;
    markdown?: string;
    mineruKey?: string;
  };
}

/** 已入库文件仅 PATCH 元数据（摘要、目录、预览正文等），不重复上传原文件 */
export async function syncWjglFileMetadata(params: SyncWjglMetadataParams): Promise<void> {
  const { docId, meta, category, documentToc, documentContent } = params;
  const patchBody: Record<string, unknown> = {
    category,
    ...meta,
    title: meta.std_name || meta.title,
    keywords: meta.keywords,
  };
  if (documentToc?.length) {
    patchBody.document_toc = documentToc;
  }
  const html = documentContent?.html?.trim();
  const markdown = documentContent?.markdown?.trim();
  const mineruKey = documentContent?.mineruKey?.trim();
  if (html || markdown || mineruKey) {
    if (html) patchBody.full_html = html;
    if (markdown) patchBody.full_markdown = markdown;
    if (mineruKey) patchBody.mineru_key = mineruKey;
  }
  const res = await fetch(wjglApiUrl(`/files/${encodeURIComponent(docId)}/metadata`), {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patchBody),
  });
  const json = await parseJsonResponse(res);
  if (!json.success) {
    throw new Error(String(json.error || '元数据同步失败'));
  }
}
