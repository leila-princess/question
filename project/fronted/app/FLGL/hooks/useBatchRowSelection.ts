'use client';

import { useCallback, useMemo, useState } from 'react';
import type { TableRowSelection } from 'antd/es/table/interface';

export function useBatchRowSelection<T extends { id?: string }>() {
  const [selectedRowKeys, setSelectedRowKeys] = useState<React.Key[]>([]);

  const rowSelection: TableRowSelection<T> = useMemo(
    () => ({
      selectedRowKeys,
      onChange: (keys) => setSelectedRowKeys(keys),
    }),
    [selectedRowKeys],
  );

  const clearSelection = useCallback(() => setSelectedRowKeys([]), []);

  return { selectedRowKeys, rowSelection, clearSelection, setSelectedRowKeys };
}

export async function batchDeleteNodes(
  nodeType: string,
  ids: string[],
  deleteFn: (type: string, id: string) => Promise<void>,
): Promise<{ ok: number; failed: string[] }> {
  const failed: string[] = [];
  let ok = 0;
  for (const id of ids) {
    const trimmed = String(id || '').trim();
    if (!trimmed) continue;
    try {
      await deleteFn(nodeType, trimmed);
      ok += 1;
    } catch {
      failed.push(trimmed);
    }
  }
  return { ok, failed };
}
