/** User-facing copy for ZNWD4 UI — hide Neo4j/Chroma/internal terms. */

import { formatNodeTitle } from '@/app/GLCX/ZNWD/formatSourceNode';

const TECH_PATTERN =
  /Neo4j|Chroma|Cypher|probe_|BIZ_[A-Z0-9_]+|UBIZ_|neo_[a-z_]+|focus_id|MinIO|match_confidence|db_direct|routing/i;

const STEP_LABELS: Record<string, string> = {
  routing: '理解问题',
  retrieval: '检索清单与资料',
  guard: '核对检索结果',
  generating: '整理回答',
  intent_judge: '理解问题',
  neo4j_search: '清单检索',
  chroma_search: '文档检索',
  fusion: '合并结果',
  synthesis: '整理回答',
};

const KIND_LABELS: Record<string, string> = {
  system: '',
  route: '理解问题',
  retrieval: '检索',
  synthesis: '整理回答',
  guard: '核对',
};

export function sanitizeUserText(text: string, fallback = '正在处理…'): string {
  const s = (text || '').trim();
  if (!s) return fallback;
  if (TECH_PATTERN.test(s)) return fallback;
  return s.replace(/图谱/g, '清单').replace(/清单库/g, '清单与标准库').replace(/\bSYNR\b/g, '方法正文');
}

export function humanizeThinkingKind(kind?: string): string {
  const k = (kind || 'system').toLowerCase();
  return KIND_LABELS[k] ?? '';
}

export function humanizeStepLabel(type: string): string {
  return STEP_LABELS[type] || '处理中';
}

export function humanizeStepMessage(raw: string | undefined, type?: string): string {
  const s = sanitizeUserText(raw || '', '');
  if (s) return s;
  if (type) return humanizeStepLabel(type);
  return '正在处理…';
}

export function formatSourceButtonLabel(opts: {
  neoCount?: number;
  chromaCount?: number;
  tableLoading?: boolean;
  sourceLoading?: boolean;
  sourceTableReady?: boolean;
}): string {
  const { neoCount = 0, chromaCount = 0, tableLoading, sourceLoading, sourceTableReady } = opts;
  if (tableLoading || sourceLoading) {
    return '查看来源（加载中…）';
  }
  const parts: string[] = [];
  if (neoCount > 0) parts.push(`清单 ${neoCount} 条`);
  if (chromaCount > 0) parts.push(`文档 ${chromaCount} 份`);
  if (parts.length > 0) {
    const suffix = sourceTableReady ? '，表格已就绪' : '';
    return `查看来源（${parts.join('，')}${suffix}）`;
  }
  return sourceTableReady ? '查看来源（表格已就绪）' : '查看来源';
}

export function formatSourcePreviewLine(neoResults: { labels?: string[]; properties?: Record<string, unknown> }[]): string {
  if (!neoResults?.length) return '';
  const titles = neoResults
    .slice(0, 3)
    .map((n) => formatNodeTitle(n.labels, n.properties))
    .filter(Boolean);
  if (!titles.length) return `已找到 ${neoResults.length} 条清单记录`;
  const tail = neoResults.length > 3 ? `等 ${neoResults.length} 条` : '';
  return `已找到清单记录：${titles.join('、')}${tail}`;
}
