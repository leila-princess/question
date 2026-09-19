'use client';

import React, { useEffect, useMemo, useState } from 'react';
import { Input, Tree, Spin, Empty } from 'antd';
import { SearchOutlined } from '@ant-design/icons';
import type { DataNode } from 'antd/es/tree';
import {
  collectTreeKeys,
  filterEquipmentTreeByKeyword,
  treeNodeTitleText,
  withSearchSelectability,
} from '../utils/equipmentTreeUtils';

export type EquipmentSearchTreeSelectMode = 'flglSidebar' | 'graphPicker';

interface EquipmentSearchTreeProps {
  treeData: DataNode[];
  loading?: boolean;
  /** flglSidebar: key 为 equipment_*；graphPicker: key 为 equipment id */
  selectMode?: EquipmentSearchTreeSelectMode;
  onSelectEquipment: (equipmentId: string, equipmentName: string) => void;
  searchPlaceholder?: string;
  hint?: string;
  emptyHint?: string;
  height?: number;
}

export default function EquipmentSearchTree({
  treeData,
  loading = false,
  selectMode = 'graphPicker',
  onSelectEquipment,
  searchPlaceholder = '输入设备名称关键词模糊搜索',
  hint = '请展开设备类型，选择具体设备（叶子节点）',
  emptyHint = '暂无可选设备',
  height,
}: EquipmentSearchTreeProps) {
  const [keyword, setKeyword] = useState('');
  const [expandedKeys, setExpandedKeys] = useState<React.Key[]>([]);

  const filteredTree = useMemo(() => {
    const filtered = filterEquipmentTreeByKeyword(treeData, keyword);
    return withSearchSelectability(filtered, keyword);
  }, [treeData, keyword]);

  const filteredKeys = useMemo(() => collectTreeKeys(filteredTree), [filteredTree]);
  const allKeys = useMemo(() => collectTreeKeys(treeData), [treeData]);

  useEffect(() => {
    if (keyword.trim()) {
      setExpandedKeys(filteredKeys);
    } else {
      setExpandedKeys(allKeys);
    }
  }, [keyword, filteredKeys, allKeys]);

  const resolveEquipmentId = (key: string, node: DataNode): string => {
    if (selectMode === 'flglSidebar') {
      return key.startsWith('equipment_') ? key.slice('equipment_'.length) : '';
    }
    return key;
  };

  const handleSelect = (_keys: React.Key[], info: { node: DataNode }) => {
    const node = info.node;
    if (!node.isLeaf) return;
    const key = String(node.key || '');
    const eqId = resolveEquipmentId(key, node);
    if (!eqId) return;
    const name = treeNodeTitleText(node.title) || eqId;
    onSelectEquipment(eqId, name);
  };

  if (loading) {
    return (
      <div style={{ textAlign: 'center', padding: 24 }}>
        <Spin />
      </div>
    );
  }

  return (
    <div>
      <Input
        allowClear
        placeholder={searchPlaceholder}
        prefix={<SearchOutlined />}
        value={keyword}
        onChange={(e) => setKeyword(e.target.value)}
        style={{ marginBottom: 12 }}
      />
      {hint ? (
        <div style={{ fontSize: 12, color: '#888', marginBottom: 8 }}>{hint}</div>
      ) : null}
      {filteredTree.length ? (
        <Tree
          showLine
          treeData={filteredTree}
          expandedKeys={expandedKeys}
          onExpand={(keys) => setExpandedKeys(keys)}
          onSelect={handleSelect}
          style={height ? { maxHeight: height, overflow: 'auto' } : undefined}
        />
      ) : treeData.length && keyword.trim() ? (
        <span style={{ color: '#999' }}>未找到匹配「{keyword.trim()}」的设备</span>
      ) : (
        <Empty description={emptyHint} image={Empty.PRESENTED_IMAGE_SIMPLE} />
      )}
    </div>
  );
}
