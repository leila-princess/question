import type { SupportedFileTypes } from 'pi-view-port';

/** 金软 WPS 专有后缀 → OnlyOffice 路由用近似后缀（不保证转换成功） */
const KINGSOFT_EXT_MAP: Record<string, string> = {
  wps: 'doc',
  et: 'xls',
  dps: 'ppt',
};

const DEFAULT_OFFICE_EXTENSIONS = [
  '.docx',
  '.doc',
  '.txt',
  '.rtf',
  '.odt',
  '.pptx',
  '.ppt',
  '.odp',
  '.ppsx',
  '.xlsx',
  '.xls',
  '.xlsm',
  '.ods',
  '.csv',
  '.wps',
  '.et',
  '.dps',
] as const;

/** 扩展 pi-view-port 默认识别列表，使 .wps/.et/.dps 能进入 office 预览分支 */
export const PI_VIEWPORT_OFFICE_SUPPORTED_TYPES: SupportedFileTypes = {
  office: {
    extensions: [...DEFAULT_OFFICE_EXTENSIONS],
    mimeTypes: [
      'application/msword',
      'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
      'application/vnd.ms-powerpoint',
      'application/vnd.openxmlformats-officedocument.presentationml.presentation',
      'application/vnd.ms-excel',
      'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
      'text/csv',
    ],
    component: 'OfficePlaceholder',
    enabled: true,
    description: 'Office',
  },
};

export type PiViewPortOfficeVariant = 'word' | 'excel' | 'auto';

function stripExt(name: string): string {
  return name.replace(/\.[^.]+$/, '') || name;
}

function mapKingsoftExt(name: string): string {
  const parts = name.split('.');
  if (parts.length < 2) return name;
  const ext = (parts.pop() || '').toLowerCase();
  const mapped = KINGSOFT_EXT_MAP[ext];
  if (!mapped) return name;
  return `${parts.join('.')}.${mapped}`;
}

/**
 * 供 FileViewer / OfficePlaceholder 使用的文件名。
 * 勿传 fileType: 'docx'；按扩展名自动识别为 office。
 */
export function ensureOfficeFileName(
  name: string,
  variant: PiViewPortOfficeVariant = 'auto',
): string {
  let base = (name || 'document').trim() || 'document';
  base = mapKingsoftExt(base);
  const ext = (base.split('.').pop() || '').toLowerCase();

  if (variant === 'word') {
    if (/\.(docx?|odt|rtf|txt|wps)$/i.test(base)) return base;
    return `${stripExt(base)}.docx`;
  }
  if (variant === 'excel') {
    if (/\.(xlsx?|xlsm|ods|csv|et)$/i.test(base)) return base;
    return `${stripExt(base)}.xlsx`;
  }

  if (
    /\.(docx?|wps|odt|rtf|txt|pptx?|dps|odp|ppsx|xlsx?|xlsm|ods|csv|et)$/i.test(base)
  ) {
    return base;
  }
  if (['doc', 'xls', 'ppt'].includes(ext)) return base;
  return `${stripExt(base)}.docx`;
}
