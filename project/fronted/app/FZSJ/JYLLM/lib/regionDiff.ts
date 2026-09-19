/**
 * 区域修订行级 diff（轻量 LCS，无外部依赖）。
 */

export interface DiffLine {
  type: 'same' | 'add' | 'remove' | 'change';
  oldLine?: string;
  newLine?: string;
}

export function computeRegionDiff(original: string, revised: string): DiffLine[] {
  const oLines = (original || '').split('\n');
  const rLines = (revised || '').split('\n');
  const m = oLines.length;
  const n = rLines.length;
  const dp: number[][] = Array.from({ length: m + 1 }, () => Array(n + 1).fill(0));

  for (let i = 1; i <= m; i++) {
    for (let j = 1; j <= n; j++) {
      if (oLines[i - 1] === rLines[j - 1]) {
        dp[i][j] = dp[i - 1][j - 1] + 1;
      } else {
        dp[i][j] = Math.max(dp[i - 1][j], dp[i][j - 1]);
      }
    }
  }

  const ops: Array<{ tag: 'same' | 'add' | 'remove'; o?: string; r?: string }> = [];
  let i = m;
  let j = n;
  while (i > 0 || j > 0) {
    if (i > 0 && j > 0 && oLines[i - 1] === rLines[j - 1]) {
      ops.push({ tag: 'same', o: oLines[i - 1], r: rLines[j - 1] });
      i -= 1;
      j -= 1;
    } else if (j > 0 && (i === 0 || dp[i][j - 1] >= dp[i - 1][j])) {
      ops.push({ tag: 'add', r: rLines[j - 1] });
      j -= 1;
    } else {
      ops.push({ tag: 'remove', o: oLines[i - 1] });
      i -= 1;
    }
  }
  ops.reverse();

  const out: DiffLine[] = [];
  for (const op of ops) {
    if (op.tag === 'same') {
      out.push({ type: 'same', oldLine: op.o, newLine: op.r });
    } else if (op.tag === 'add') {
      out.push({ type: 'add', newLine: op.r });
    } else {
      out.push({ type: 'remove', oldLine: op.o });
    }
  }
  return out;
}

export function diffSummary(diff: DiffLine[]): { added: number; removed: number; unchanged: number } {
  let added = 0;
  let removed = 0;
  let unchanged = 0;
  for (const d of diff) {
    if (d.type === 'add') added += 1;
    else if (d.type === 'remove') removed += 1;
    else unchanged += 1;
  }
  return { added, removed, unchanged };
}

export function hasSignificantDiff(diff: DiffLine[]): boolean {
  const { added, removed } = diffSummary(diff);
  return added + removed > 0;
}
