import { useState, useEffect, useMemo } from 'react';
import type { TablePaginationConfig } from 'antd';

export interface UseTablePaginationOptions {
  defaultPageSize?: number;
  showSizeChanger?: boolean;
  pageSizeOptions?: number[];
  showTotal?: boolean;
  /** 筛选、切换 Tab/树节点等上下文变化时回到第 1 页 */
  resetDeps?: readonly unknown[];
}

/** 受控表格分页：筛选后自动回第 1 页，数据变少时避免空白页 */
export function useTablePagination(
  total: number,
  options: UseTablePaginationOptions = {},
): TablePaginationConfig {
  const {
    defaultPageSize = 15,
    showSizeChanger = true,
    pageSizeOptions = [10, 15, 20, 50, 100],
    showTotal = true,
    resetDeps = [],
  } = options;

  const [current, setCurrent] = useState(1);
  const [pageSize, setPageSize] = useState(defaultPageSize);

  const resetKey = JSON.stringify(resetDeps);

  useEffect(() => {
    setCurrent(1);
  }, [resetKey]);

  useEffect(() => {
    const maxPage = Math.max(1, Math.ceil(total / pageSize) || 1);
    if (current > maxPage) {
      setCurrent(maxPage);
    }
  }, [total, pageSize, current]);

  return useMemo(
    () => ({
      current,
      pageSize,
      total,
      showSizeChanger,
      pageSizeOptions,
      ...(showTotal ? { showTotal: (t: number) => `共 ${t} 条` } : {}),
      onChange: (page: number, size?: number) => {
        setCurrent(page);
        if (size && size !== pageSize) {
          setPageSize(size);
        }
      },
    }),
    [current, pageSize, total, showSizeChanger, pageSizeOptions, showTotal],
  );
}
