'use client';
import React from 'react';
import GenericNodePanel from './GenericNodePanel';
import type { GraphNode } from '../api';
export default function FilePanel({ allNodes, selectedCategory, onRefresh }: { allNodes: GraphNode[]; selectedCategory: string | null; onRefresh: () => void }) {
  return <GenericNodePanel nodeType="File" title="文件管理" allNodes={allNodes} selectedCategory={selectedCategory} onRefresh={onRefresh} />;
}
