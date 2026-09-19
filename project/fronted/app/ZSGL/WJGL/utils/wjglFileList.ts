import { fetchJsonSafe } from '../components/documentTocUtils';

export interface WjglFileRecord {
  object_name: string;
  doc_id: string;
  title: string;
  category: string;
  file_type: string;
  file_label: string;
  ics: string;
  ccs: string;
  std_code: string;
  std_name: string;
  publish_date: string;
  issuer: string;
  review_status: string;
  upload_userid: string;
  upload_time: string;
  file_size: number;
  abstract: string;
  keywords: string;
  parse_status?: string;
}

export interface WjglFileListFilters {
  keyword?: string;
  stdCode?: string;
  uploadUser?: string;
  dateFrom?: string;
  dateTo?: string;
}

export async function fetchWjglFiles(
  category: string,
  filters: WjglFileListFilters = {},
): Promise<WjglFileRecord[]> {
  const params = new URLSearchParams({ category });
  if (filters.keyword) params.set('keyword', filters.keyword);
  if (filters.stdCode) params.set('std_code', filters.stdCode);
  if (filters.uploadUser) params.set('upload_user', filters.uploadUser);
  if (filters.dateFrom) params.set('upload_time_from', filters.dateFrom);
  if (filters.dateTo) params.set('upload_time_to', filters.dateTo);
  const res = await fetch(`/api/wjgl1/list?${params}`);
  const data = await fetchJsonSafe(res);
  if (!data.success) {
    throw new Error(String(data.error || '获取文件列表失败'));
  }
  return Array.isArray(data.data) ? (data.data as WjglFileRecord[]) : [];
}
