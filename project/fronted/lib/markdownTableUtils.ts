/**
 * GFM 表格：remark-gfm 需要表头分隔行；OCR/粘贴常把多行表格挤在一行。
 */

function isTableRow(line: string): boolean {
  return /^\s*\|/.test(line);
}

function isTableSeparatorRow(line: string): boolean {
  const t = line.trim();
  if (!t.startsWith('|')) return false;
  const inner = t.endsWith('|') ? t.slice(1, -1) : t.slice(1);
  const cells = inner.split('|');
  return cells.length > 0 && cells.every((c) => /^[-:\s]+$/.test(c.trim()) || !c.trim());
}

function colCount(row: string): number {
  return Math.max(1, (row.match(/\|/g) || []).length - 1);
}

function ensureGfmTableBlock(block: string[]): string[] {
  if (!block.length) return block;
  if (block.length >= 2 && isTableSeparatorRow(block[1])) return block;
  const n = colCount(block[0]);
  const sep = `| ${Array(n).fill('---').join(' | ')} |`;
  return [block[0], sep, ...block.slice(1)];
}

function splitCollapsedTableLine(line: string): string[] {
  if ((line.match(/\|/g) || []).length < 4) return [line];
  const fixed = line.replace(/\|\s*\|(?=\s*(?:\d+(?:\s*\|)|序号\b))/g, '|\n|');
  if (!fixed.includes('\n')) return [line];
  return fixed.split('\n').filter((ln) => ln.trim());
}

export function repairMarkdownTables(text: string): string {
  if (!text || !text.includes('|')) return text;
  const expanded: string[] = [];
  for (const line of text.split('\n')) {
    const parts = splitCollapsedTableLine(line);
    if (parts.length > 1) expanded.push(...parts);
    else expanded.push(line);
  }

  const result: string[] = [];
  let i = 0;
  while (i < expanded.length) {
    const line = expanded[i];
    if (isTableRow(line)) {
      const block = [line];
      i += 1;
      while (i < expanded.length && isTableRow(expanded[i])) {
        block.push(expanded[i]);
        i += 1;
      }
      result.push(...ensureGfmTableBlock(block));
    } else {
      result.push(line);
      i += 1;
    }
  }
  return result.join('\n');
}
