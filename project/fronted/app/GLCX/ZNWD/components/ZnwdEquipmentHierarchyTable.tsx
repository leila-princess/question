'use client';

import { Table } from 'antd';
import type { ColumnsType } from 'antd/es/table';

export type ZnwdEquipmentHierarchyRow = {
  level1Type: string;
  level2TypeName: string;
  level2TypeId: string;
  equipmentName: string;
  equipmentId: string;
};

type Props = {
  rows: ZnwdEquipmentHierarchyRow[];
  loading?: boolean;
  highlightEquipmentIds?: string[];
  highlightLevel2TypeIds?: string[];
};

export default function ZnwdEquipmentHierarchyTable({
  rows,
  loading = false,
  highlightEquipmentIds = [],
  highlightLevel2TypeIds = [],
}: Props) {
  const eqHighlight = new Set(highlightEquipmentIds.map(String));
  const l2Highlight = new Set(highlightLevel2TypeIds.map(String));

  const columns: ColumnsType<ZnwdEquipmentHierarchyRow> = [
    {
      title: '一级装备类别',
      dataIndex: 'level1Type',
      key: 'level1Type',
      width: 140,
      ellipsis: true,
    },
    {
      title: '二级装备类型',
      dataIndex: 'level2TypeName',
      key: 'level2TypeName',
      width: 160,
      ellipsis: true,
      render: (val: string, record) => {
        const highlighted = record.level2TypeId && l2Highlight.has(record.level2TypeId);
        return (
          <span style={highlighted ? { background: '#fff7e6', padding: '2px 4px', borderRadius: 4 } : undefined}>
            {val || '—'}
          </span>
        );
      },
    },
    {
      title: '具体装备',
      dataIndex: 'equipmentName',
      key: 'equipmentName',
      ellipsis: true,
      render: (val: string, record) => {
        if (!val) return '—';
        const highlighted = record.equipmentId && eqHighlight.has(record.equipmentId);
        return (
          <span style={highlighted ? { background: '#fff7e6', padding: '2px 4px', borderRadius: 4 } : undefined}>
            {val}
          </span>
        );
      },
    },
  ];

  return (
    <Table<ZnwdEquipmentHierarchyRow>
      size="small"
      loading={loading}
      rowKey={(r) => `${r.level2TypeId}::${r.equipmentId || r.level2TypeName}`}
      columns={columns}
      dataSource={rows}
      pagination={rows.length > 15 ? { pageSize: 15, showSizeChanger: rows.length > 30 } : false}
      scroll={{ y: 420 }}
      locale={{ emptyText: '未检索到装备层级数据，可点击刷新重试' }}
    />
  );
}
