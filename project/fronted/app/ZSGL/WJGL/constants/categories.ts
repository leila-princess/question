/** 文件管理 Tab key → MinIO/Neo4j 类别名 */
export const TAB_CATEGORIES: Record<string, string> = {
  standard: '法规标准',
  normative: '规范性文件',
  guidance: '指导性文件',
  scheme: '试验方案',
  outline: '试验大纲',
  report: '试验报告',
  issue: '问题',
  assessment: '专项评估报告',
  other: '其他',
};

export const DEFAULT_WJGL_CATEGORY = '法规标准';

/** 下拉选项（含「其他」） */
export const CATEGORY_OPTIONS = Object.entries(TAB_CATEGORIES).map(([key, label]) => ({
  key,
  label,
}));

/** 方法提取可选类别（「其他」走 simple-upload，不支持 auto-parse） */
export const METHOD_EXTRACT_CATEGORY_OPTIONS = CATEGORY_OPTIONS.filter((o) => o.key !== 'other');

export function categoryLabelFromKey(tabKey: string): string {
  return TAB_CATEGORIES[tabKey] || DEFAULT_WJGL_CATEGORY;
}
