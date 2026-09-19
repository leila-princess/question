'use client';

import React, { useMemo } from 'react';
import { Tree, Tag, Tooltip, Spin, Empty } from 'antd';
import { ApartmentOutlined, LinkOutlined } from '@ant-design/icons';
import type { SemanticTreeData, SemanticTreeNode } from './semanticTypes';
import { treeToAntData } from './semanticTypes';

interface SemanticTreePanelProps {
  tree?: SemanticTreeData | null;
  loading?: boolean;
  llmUpgrading?: boolean;
  selectedNodeId?: string;
  onSelect: (nodeId: string, node: SemanticTreeNode | null) => void;
}

function findNode(root: SemanticTreeNode | undefined, id: string): SemanticTreeNode | null {
  if (!root) return null;
  if (root.node_id === id) return root;
  for (const ch of root.children || []) {
    const f = findNode(ch, id);
    if (f) return f;
  }
  return null;
}

export default function SemanticTreePanel({
  tree,
  loading,
  llmUpgrading,
  selectedNodeId,
  onSelect,
}: SemanticTreePanelProps) {
  const root = tree?.root;
  const treeData = useMemo(() => (root ? [treeToAntData(root)] : []), [root]);

  if (loading && !root) {
    return <div style={{ textAlign: 'center', padding: 24 }}><Spin tip="加载语义树…" /></div>;
  }
  if (!root) {
    return <Empty description="暂无章节结构" image={Empty.PRESENTED_IMAGE_SIMPLE} />;
  }

  const gl = root.graph_link;

  return (
    <div>
      {gl?.node_name && (
        <div style={{ marginBottom: 8, fontSize: 12 }}>
          <LinkOutlined /> 推荐装备：
          <Tag color="blue" style={{ marginLeft: 4 }}>
            {gl.node_name}
          </Tag>
          {gl.confidence != null && (
            <Tooltip title={gl.reason}>
              <Tag>{Math.round((gl.confidence || 0) * 100)}%</Tag>
            </Tooltip>
          )}
        </div>
      )}
      <div style={{ marginBottom: 6, fontWeight: 600, fontSize: 13 }}>
        <ApartmentOutlined /> 语义结构
        {tree?.document_class && <Tag style={{ marginLeft: 6 }}>{tree.document_class}</Tag>}
        {llmUpgrading && <Tag color="processing" style={{ marginLeft: 6 }}>LLM 优化中</Tag>}
        {tree?.tree_source === 'fallback' && !llmUpgrading && (
          <Tag style={{ marginLeft: 6 }}>规则预览</Tag>
        )}
      </div>
      <Tree
        showLine
        defaultExpandAll
        selectedKeys={selectedNodeId ? [selectedNodeId] : []}
        treeData={treeData}
        onSelect={keys => {
          const id = String(keys[0] || root.node_id);
          onSelect(id, findNode(root, id));
        }}
        style={{ maxHeight: 420, overflow: 'auto', background: '#fafafa', padding: 8, borderRadius: 6 }}
      />
    </div>
  );
}
