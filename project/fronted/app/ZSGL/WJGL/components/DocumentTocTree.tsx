'use client';

import React from 'react';
import { Tree, Empty, Spin } from 'antd';
import { FileTextOutlined } from '@ant-design/icons';
import type { TocTreeNode } from './documentTocUtils';

interface DocumentTocTreeProps {
  treeData: TocTreeNode[];
  loading?: boolean;
  selectedKey?: string;
  onSelect: (key: string, node: TocTreeNode | null) => void;
}

export default function DocumentTocTree({
  treeData,
  loading,
  selectedKey,
  onSelect,
}: DocumentTocTreeProps) {
  if (loading && !treeData.length) {
    return <div style={{ textAlign: 'center', padding: 24 }}><Spin description="加载文档目录…" /></div>;
  }
  if (!treeData.length) {
    return <Empty description="暂无章节目录" image={Empty.PRESENTED_IMAGE_SIMPLE} />;
  }

  return (
    <div>
      <div style={{ marginBottom: 6, fontWeight: 600, fontSize: 13 }}>
        <FileTextOutlined /> 文档结构
      </div>
      <Tree
        showLine
        defaultExpandAll
        selectedKeys={selectedKey ? [selectedKey] : []}
        treeData={treeData}
        onSelect={(keys, info) => {
          const key = String(keys[0] || '');
          if (key) onSelect(key, (info.node as TocTreeNode) || null);
        }}
        style={{ maxHeight: 420, overflow: 'auto', background: '#fafafa', padding: 8, borderRadius: 6 }}
      />
    </div>
  );
}
