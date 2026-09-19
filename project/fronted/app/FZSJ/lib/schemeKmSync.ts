import {
  commitWjglFileUpload,
  type UploadFileMeta,
} from '@/app/ZSGL/WJGL/utils/wjglUploadPipeline';
import { SCHEME_KM_SOURCE_LABELS } from '@/app/ZSGL/WJGL/constants/schemeSourceLabels';
import type { SchemeExportFormat } from './schemeKmMeta';
import { blobToFile, schemeFilename } from './schemeKmMeta';

const KM_CATEGORY = '试验方案';
const JYGZ_API_BASE = '/api/fzsj/jygz';

export function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export interface ExportBlobResult {
  blob: Blob;
  filename: string;
}

export async function exportJyllmBlob(taskId: string, format: SchemeExportFormat): Promise<ExportBlobResult> {
  const response = await fetch(`/api/fzsj/jyllm/export/${encodeURIComponent(taskId)}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ format }),
  });
  if (!response.ok) {
    const errData = await response.json().catch(() => ({}));
    throw new Error((errData as { error?: string }).error || `导出失败 (HTTP ${response.status})`);
  }

  if (format === 'md') {
    const data = await response.json();
    const content = String((data as { content?: string }).content || '');
    const blob = new Blob([content], { type: 'text/markdown;charset=utf-8' });
    return { blob, filename: schemeFilename(taskId, 'md') };
  }

  const blob = await response.blob();
  return { blob, filename: schemeFilename(taskId, format) };
}

export async function exportJygzBlob(
  markdown: string,
  format: SchemeExportFormat,
  title?: string,
  docNumber?: string,
): Promise<ExportBlobResult> {
  const base = (docNumber || 'export').trim() || 'export';
  if (format === 'md') {
    const blob = new Blob([markdown], { type: 'text/markdown;charset=utf-8' });
    return { blob, filename: schemeFilename(base, 'md') };
  }

  const endpoint = format === 'pdf' ? '/export/pdf' : '/export/word';
  const res = await fetch(`${JYGZ_API_BASE}${endpoint}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      markdown,
      title,
      equipment_name: title,
      doc_number: docNumber,
    }),
  });
  if (!res.ok) {
    let errText = '导出失败';
    try {
      const j = await res.json();
      errText = String((j as { error?: string }).error || errText);
    } catch {
      /* ignore */
    }
    throw new Error(errText);
  }
  const blob = await res.blob();
  return { blob, filename: schemeFilename(base, format) };
}

export async function syncSchemeToKm(params: {
  file: File;
  meta: UploadFileMeta;
}): Promise<{ doc_id: string; object_name: string }> {
  const uid = `fzsj-${Date.now()}`;
  const result = await commitWjglFileUpload({
    file: params.file,
    uid,
    meta: params.meta,
    category: KM_CATEGORY,
    syncChroma: true,
    duplicateAction: 'replace',
    fileLabel: SCHEME_KM_SOURCE_LABELS.SYSTEM_SYNC,
  });
  return { doc_id: result.doc_id, object_name: result.object_name };
}

export function fileFromExport(blob: Blob, filename: string): File {
  return blobToFile(blob, filename);
}
