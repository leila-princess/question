/** 判断是否为数据库乱码/占位用的问号名 */
export function isGarbledDisplayName(name: string | null | undefined): boolean {
  const n = (name || '').trim();
  if (!n) return true;
  return /^[\?\uFFFD\uFF1F？\s]+$/.test(n);
}

/** 右上角等展示用：姓名正常则用姓名，否则用登录账号 */
export function resolveDisplayName(
  name: string | null | undefined,
  loginId: string | null | undefined,
  fallback = '用户',
): string {
  const id = (loginId || '').trim();
  if (!isGarbledDisplayName(name)) return (name || '').trim();
  return id || fallback;
}
