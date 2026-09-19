/** 试验方案在知识库中的来源标识（写入 MinIO x-amz-meta-file-label，最长 20 字符） */
export const SCHEME_KM_SOURCE_LABELS = {
  SYSTEM_SYNC: '系统同步',
  LOCAL_UPLOAD: '本地上传',
} as const;

export type SchemeKmSourceLabel =
  (typeof SCHEME_KM_SOURCE_LABELS)[keyof typeof SCHEME_KM_SOURCE_LABELS];

export function resolveSchemeSourceDisplay(fileLabel: string | undefined | null): {
  label: SchemeKmSourceLabel;
  color: 'blue' | 'default';
} {
  if (fileLabel === SCHEME_KM_SOURCE_LABELS.SYSTEM_SYNC) {
    return { label: SCHEME_KM_SOURCE_LABELS.SYSTEM_SYNC, color: 'blue' };
  }
  return { label: SCHEME_KM_SOURCE_LABELS.LOCAL_UPLOAD, color: 'default' };
}
