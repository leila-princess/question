/** Flask 根地址（不含 /api） */
export function getWjglBackendBase(): string {
  const direct = process.env.NEXT_PUBLIC_BACKEND_URL || process.env.NEXT_PUBLIC_API_URL;
  if (direct) {
    const trimmed = direct.replace(/\/+$/, '');
    if (trimmed.endsWith('/api')) return trimmed.slice(0, -4);
    return trimmed;
  }
  return 'http://localhost:5000';
}

function normalizeWjgl1Path(path: string): string {
  const seg = path.startsWith('/') ? path : `/${path}`;
  if (seg.startsWith('/api/wjgl1/') || seg === '/api/wjgl1') return seg;
  if (seg.startsWith('/api/')) return seg;
  return `/api/wjgl1${seg}`;
}

/** JSON / GET 等同源 /api/wjgl1 代理（或 rewrite） */
export function wjglApiUrl(path: string): string {
  return normalizeWjgl1Path(path);
}

/**
 * multipart 上传必须直连 Flask：Next Route Handler 会在 ~1MB 处 500 且 Flask 无日志。
 * 浏览器端使用 NEXT_PUBLIC_BACKEND_URL（默认 http://localhost:5000）。
 */
export function wjglUploadUrl(path: string): string {
  const apiPath = normalizeWjgl1Path(path);
  if (typeof window === 'undefined') return apiPath;
  return `${getWjglBackendBase()}${apiPath}`;
}
