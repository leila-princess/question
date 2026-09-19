'use client';

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Input, Table, Tag, Typography } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { fetchWjglFiles, type WjglFileRecord } from '../utils/wjglFileList';

const { Text } = Typography;

interface WjglLibraryFilePickerProps {
  category: string;
  selectedDocId?: string;
  onSelect: (record: WjglFileRecord) => void;
  acceptExtensions?: string[];
}

function extAllowed(fileType: string, accept?: string[]): boolean {
  if (!accept?.length) return true;
  const ext = (fileType || '').toLowerCase().replace(/^\./, '');
  return accept.some((a) => a.toLowerCase().replace(/^\./, '') === ext);
}

export default function WjglLibraryFilePicker({
  category,
  selectedDocId,
  onSelect,
  acceptExtensions,
}: WjglLibraryFilePickerProps) {
  const [loading, setLoading] = useState(false);
  const [files, setFiles] = useState<WjglFileRecord[]>([]);
  const [keyword, setKeyword] = useState('');

  const loadFiles = useCallback(async () => {
    if (!category) return;
    setLoading(true);
    try {
      const rows = await fetchWjglFiles(category, { keyword: keyword.trim() || undefined });
      setFiles(rows);
    } catch (e: unknown) {
      setFiles([]);
      console.error('fetchWjglFiles failed:', e);
    } finally {
      setLoading(false);
    }
  }, [category, keyword]);

  useEffect(() => {
    void loadFiles();
  }, [loadFiles]);

  const filtered = useMemo(
    () => files.filter((f) => extAllowed(f.file_type, acceptExtensions)),
    [files, acceptExtensions],
  );

  const columns: ColumnsType<WjglFileRecord> = [
    {
      title: '标题',
      dataIndex: 'title',
      key: 'title',
      ellipsis: true,
      render: (v: string, r) => v || r.std_name || r.doc_id,
    },
    {
      title: '标识',
      dataIndex: 'std_code',
      key: 'std_code',
      width: 120,
      ellipsis: true,
      render: (v: string) => v || '—',
    },
    {
      title: 'doc_id',
      dataIndex: 'doc_id',
      key: 'doc_id',
      width: 100,
    },
    {
      title: '解析',
      key: 'parse',
      width: 88,
      render: (_: unknown, r) => {
        const ps = String((r as WjglFileRecord & { parse_status?: string }).parse_status || '').toLowerCase();
        if (ps === 'done') return <Tag color="green">已完成</Tag>;
        if (ps === 'processing' || ps === 'pending') return <Tag color="blue">解析中</Tag>;
        if (ps === 'error' || ps === 'failed') return <Tag color="red">失败</Tag>;
        return <Tag>—</Tag>;
      },
    },
    {
      title: '上传时间',
      dataIndex: 'upload_time',
      key: 'upload_time',
      width: 110,
      ellipsis: true,
    },
  ];

  return (
    <div>
      <div style={{ marginBottom: 8 }}>
        <Input.Search
          placeholder="搜索标题、标识…"
          allowClear
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
          onSearch={() => void loadFiles()}
          style={{ maxWidth: 360 }}
        />
      </div>
      {acceptExtensions?.length ? (
        <Text type="secondary" style={{ fontSize: 12, display: 'block', marginBottom: 8 }}>
          仅展示 {acceptExtensions.join('、')} 类型文件
        </Text>
      ) : null}
      <Table<WjglFileRecord>
        size="small"
        rowKey="doc_id"
        loading={loading}
        columns={columns}
        dataSource={filtered}
        pagination={{ pageSize: 8, showSizeChanger: false }}
        scroll={{ y: 280 }}
        rowSelection={{
          type: 'radio',
          selectedRowKeys: selectedDocId ? [selectedDocId] : [],
          onChange: (_keys, rows) => {
            if (rows[0]) onSelect(rows[0]);
          },
        }}
        onRow={(record) => ({
          onClick: () => onSelect(record),
          style: { cursor: 'pointer' },
        })}
      />
    </div>
  );
}
