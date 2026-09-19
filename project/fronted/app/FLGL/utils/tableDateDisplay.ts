/** 展示层日期格式：YYYY年MM月DD日。提交后端仍使用完整时间字符串，勿改表单/提交逻辑。 */
export function formatTableDateDisplay(value: unknown): string {
  if (value == null) return '—';
  const s = String(value).trim();
  if (!s) return '—';

  // 兼容：2026-04-07T05:30:00.476000000+00:00 / 2026-04-07 13:30:00 / 2026-04-07
  const m = s.match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (m) return `${m[1]}年${m[2]}月${m[3]}日`;

  return s;
}
