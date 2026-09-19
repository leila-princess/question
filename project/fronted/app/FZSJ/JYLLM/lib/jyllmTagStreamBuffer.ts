/**
 * 流式文本标签缓冲：先累积若干 chunk 再开始向前端输出，并在缓冲区内识别完整标签对，
 * 剔除「串台」片段（如正文里混入的 [THINK]），保留可能未闭合的尾部。
 *
 * 支持：
 * - [THINK] … [/THINK]、[CONTENT] … [/CONTENT]（大小写、内侧空格兼容）
 * - <thinking> … </thinking>、<content> … </content>
 */

export const DEFAULT_BOOTSTRAP_CHUNK_COUNT = 10;

/** 可能为未闭合标签前缀的最大保留尾长（避免把 "[TH" 提前显示出去） */
const TAIL_RESERVE = 48;

type PairDef = { open: RegExp; close: RegExp };

const THINK_PAIRS: PairDef[] = [
  { open: /\[\s*THINK\s*\]/i, close: /\[\s*\/\s*THINK\s*\]/i },
  { open: /<\s*thinking\s*>/i, close: /<\s*\/\s*thinking\s*>/i },
];

const CONTENT_PAIRS: PairDef[] = [
  { open: /\[\s*CONTENT\s*\]/i, close: /\[\s*\/\s*CONTENT\s*\]/i },
  { open: /<\s*content\s*>/i, close: /<\s*\/\s*content\s*>/i },
];

function stripCompletePairs(input: string, pairs: PairDef[]): string {
  let s = input;
  let changed = true;
  while (changed) {
    changed = false;
    for (const { open, close } of pairs) {
      const m = open.exec(s);
      if (!m) continue;
      const start = m.index;
      const afterOpen = start + m[0].length;
      const tail = s.slice(afterOpen);
      const mc = close.exec(tail);
      if (!mc) continue;
      const end = afterOpen + mc.index + mc[0].length;
      s = s.slice(0, start) + s.slice(end);
      changed = true;
      break;
    }
  }
  return s;
}

function couldBeTagPrefix(tail: string): boolean {
  const t = tail.slice(-TAIL_RESERVE);
  return /[[<]\s*\/?\s*[TtHhIiNnKkCcOoNnEeTt]{0,12}$/.test(t);
}

/**
 * @param mode
 * - `thinking`：供「思考区」展示；**移除**完整 CONTENT 标签块（避免误入思考区）
 * - `content`：供「正文区」展示；**移除**完整 THINK 标签块（避免误入正文区）
 */
export class JyllmTagStreamBuffer {
  private buf = '';
  private chunkCount = 0;

  constructor(
    private readonly mode: 'thinking' | 'content',
    private readonly bootstrapChunks: number = DEFAULT_BOOTSTRAP_CHUNK_COUNT,
  ) {}

  reset() {
    this.buf = '';
    this.chunkCount = 0;
  }

  /**
   * 追加一个网络 chunk；返回**本轮**可安全追加到 UI 的文本（可能为空）。
   * 前 `bootstrapChunks - 1` 次调用只累积不返回；从第 `bootstrapChunks` 次起开始按缓冲规则吐出。
   */
  append(chunk: string): string {
    this.buf += chunk;
    this.chunkCount += 1;

    if (this.chunkCount < this.bootstrapChunks) {
      return '';
    }

    return this.drainSafeEmit();
  }

  /** 流结束：强制吐出剩余（仍做反标签剔除） */
  flushFinal(): string {
    const pairsToStrip = this.mode === 'thinking' ? CONTENT_PAIRS : THINK_PAIRS;
    this.buf = stripCompletePairs(this.buf, pairsToStrip);
    const emit = this.buf;
    this.buf = '';
    return emit;
  }

  private drainSafeEmit(): string {
    const pairsToStrip = this.mode === 'thinking' ? CONTENT_PAIRS : THINK_PAIRS;
    this.buf = stripCompletePairs(this.buf, pairsToStrip);

    if (this.buf.length <= TAIL_RESERVE) {
      return '';
    }

    if (!couldBeTagPrefix(this.buf)) {
      const emit = this.buf;
      this.buf = '';
      return emit;
    }

    const safeLen = this.buf.length - TAIL_RESERVE;
    const emit = this.buf.slice(0, safeLen);
    this.buf = this.buf.slice(safeLen);
    return emit;
  }
}
