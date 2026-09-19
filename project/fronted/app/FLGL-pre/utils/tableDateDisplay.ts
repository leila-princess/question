/** 表格展示：仅日期 YYYY-MM-DD。提交后端仍使用完整时间字符串，勿改表单/提交逻辑。 */
export function formatTableDateDisplay(value: unknown): string {
  if (value == null) return '—';
  const s = String(value).trim();
  if (!s) return '—';
  const m = s.match(/^(\d{4}-\d{2}-\d{2})/);
  return m ? m[1] : s;
}
