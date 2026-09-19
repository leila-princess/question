'use client';

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Button,
  Card,
  Checkbox,
  Modal,
  Radio,
  Space,
  Tag,
  Typography,
  Tree,
  Empty,
} from 'antd';
import type { DataNode } from 'antd/es/tree';
import type {
  OutlineTreeNode,
  OutlineSelection,
  SectionGenerationOption,
} from '../types/flow';

const { Text, Paragraph } = Typography;

function collectAllSectionIds(nodes: OutlineTreeNode[]): string[] {
  const ids: string[] = [];
  const walk = (list: OutlineTreeNode[]) => {
    for (const n of list) {
      if (n.type === 'chapter' || n.type === 'method') {
        ids.push(n.id);
      }
      if (n.children?.length) walk(n.children);
    }
  };
  walk(nodes);
  return ids;
};

function findMethodNode(nodes: OutlineTreeNode[], id: string): OutlineTreeNode | null {
  for (const n of nodes) {
    if (n.id === id && n.type === 'method') return n;
    if (n.children?.length) {
      const found = findMethodNode(n.children, id);
      if (found) return found;
    }
  }
  return null;
};

function defaultSectionOptions(nodes: OutlineTreeNode[]): Record<string, SectionGenerationOption> {
  const opts: Record<string, SectionGenerationOption> = {};
  const walk = (list: OutlineTreeNode[]) => {
    for (const n of list) {
      if (n.type === 'method') {
        const refIds = (n.suggestedReferenceFiles || []).map((f) => f.id);
        opts[n.id] = {
          mode: n.hasDbData ? 'database_correct' : 'llm_generate',
          referenceFileIds: refIds,
        };
      }
      if (n.children?.length) walk(n.children);
    }
  };
  walk(nodes);
  return opts;
}

function toTreeData(nodes: OutlineTreeNode[]): DataNode[] {
  return nodes.map((n) => ({
    key: n.id,
    title: n.title,
    children: n.children?.length ? toTreeData(n.children) : undefined,
  }));
}

interface PlanOutlinePanelProps {
  outlineTree: OutlineTreeNode[];
  initialSelection?: OutlineSelection | null;
  onConfirm: (selection: OutlineSelection) => void;
  loading?: boolean;
}

export default function PlanOutlinePanel({
  outlineTree,
  initialSelection,
  onConfirm,
  loading,
}: PlanOutlinePanelProps) {
  const allIds = useMemo(() => collectAllSectionIds(outlineTree), [outlineTree]);
  const defaultOpts = useMemo(() => defaultSectionOptions(outlineTree), [outlineTree]);

  const [checkedKeys, setCheckedKeys] = useState<string[]>(
    initialSelection?.selectedSections?.length ? initialSelection.selectedSections : allIds,
  );
  const [sectionOptions, setSectionOptions] = useState<Record<string, SectionGenerationOption>>(
    initialSelection?.sectionOptions || defaultOpts,
  );
  const [selectedMethodId, setSelectedMethodId] = useState<string | null>(null);
  const [fullContentOpen, setFullContentOpen] = useState(false);

  useEffect(() => {
    if (!initialSelection?.selectedSections?.length) {
      setCheckedKeys(allIds);
    }
    setSectionOptions((prev) => ({ ...defaultOpts, ...prev, ...(initialSelection?.sectionOptions || {}) }));
  }, [allIds, defaultOpts, initialSelection]);

  const treeData = useMemo(() => toTreeData(outlineTree), [outlineTree]);
  const selectedMethod = selectedMethodId ? findMethodNode(outlineTree, selectedMethodId) : null;
  const methodOpt = selectedMethodId ? sectionOptions[selectedMethodId] : null;

  const handleCheck = (keys: React.Key[] | { checked: React.Key[] }) => {
    const k = Array.isArray(keys) ? keys : keys.checked;
    setCheckedKeys(k.map(String));
  };

  const updateMethodOption = useCallback(
    (methodId: string, patch: Partial<SectionGenerationOption>) => {
      setSectionOptions((prev) => ({
        ...prev,
        [methodId]: { ...(prev[methodId] || { mode: 'llm_generate', referenceFileIds: [] }), ...patch },
      }));
    },
    [],
  );

  const handleConfirm = () => {
    if (checkedKeys.length === 0) {
      return;
    }
    onConfirm({ selectedSections: checkedKeys, sectionOptions });
  };

  return (
    <div>
      <Text type="secondary" style={{ display: 'block', marginBottom: 12 }}>
        勾选需要生成的章节/方法；有数据库内容的方法可预览并选择「数据库+LLM校正」或「LLM自行生成」。
      </Text>
      <div style={{ display: 'flex', gap: 16, minHeight: 360 }}>
        <Card
          size="small"
          title="方案目录"
          style={{ flex: 1, minWidth: 0 }}
          styles={{ body: { maxHeight: 420, overflow: 'auto' } }}
        >
          {treeData.length > 0 ? (
            <Tree
              checkable
              defaultExpandAll
              checkedKeys={checkedKeys}
              onCheck={handleCheck}
              treeData={treeData}
              onSelect={(keys) => {
                const id = String(keys[0] || '');
                if (id && findMethodNode(outlineTree, id)) setSelectedMethodId(id);
              }}
            />
          ) : (
            <Empty description="暂无目录" />
          )}
        </Card>

        <Card
          size="small"
          title="段落详情"
          style={{ flex: 1, minWidth: 0 }}
          styles={{ body: { maxHeight: 420, overflow: 'auto' } }}
        >
          {!selectedMethod ? (
            <Empty description="点击方法节点查看详情" image={Empty.PRESENTED_IMAGE_SIMPLE} />
          ) : (
            <div>
              <Text strong>{selectedMethod.title}</Text>
              {selectedMethod.hasDbData ? (
                <Tag color="blue" style={{ marginLeft: 8 }}>
                  数据库检索
                </Tag>
              ) : (
                <Tag color="purple" style={{ marginLeft: 8 }}>
                  LLM生成
                </Tag>
              )}

              {selectedMethod.hasDbData && selectedMethod.dbPreview ? (
                <Card size="small" style={{ marginTop: 12, background: '#f9fafb' }}>
                  <Paragraph ellipsis={{ rows: 4 }} style={{ marginBottom: 8, fontSize: 13 }}>
                    {selectedMethod.dbPreview}
                  </Paragraph>
                  <Button type="link" size="small" onClick={() => setFullContentOpen(true)}>
                    查看全部内容
                  </Button>
                </Card>
              ) : null}

              <div style={{ marginTop: 16 }}>
                <Text style={{ fontSize: 13 }}>生成方式：</Text>
                <Radio.Group
                  style={{ display: 'block', marginTop: 8 }}
                  value={methodOpt?.mode || 'llm_generate'}
                  onChange={(e) =>
                    updateMethodOption(selectedMethod.id, { mode: e.target.value })
                  }
                >
                  <Radio value="database_correct" disabled={!selectedMethod.hasDbData}>
                    使用数据库 + LLM 校正
                  </Radio>
                  <Radio value="llm_generate">LLM 自行生成</Radio>
                </Radio.Group>
              </div>

              {(methodOpt?.mode === 'llm_generate' ||
                (selectedMethod.suggestedReferenceFiles?.length ?? 0) > 0) && (
                <div style={{ marginTop: 16 }}>
                  <Text style={{ fontSize: 13 }}>参考文件（向量匹配）：</Text>
                  <div style={{ marginTop: 8 }}>
                    {(selectedMethod.suggestedReferenceFiles || []).length === 0 ? (
                      <Text type="secondary" style={{ fontSize: 12 }}>
                        未匹配到参考文件
                      </Text>
                    ) : (
                      (selectedMethod.suggestedReferenceFiles || []).map((f) => (
                        <div key={f.id} style={{ marginBottom: 6 }}>
                          <Checkbox
                            checked={(methodOpt?.referenceFileIds || []).includes(f.id)}
                            onChange={(e) => {
                              const cur = methodOpt?.referenceFileIds || [];
                              const next = e.target.checked
                                ? [...cur, f.id]
                                : cur.filter((x) => x !== f.id);
                              updateMethodOption(selectedMethod.id, { referenceFileIds: next });
                            }}
                          >
                            <span style={{ fontSize: 12 }}>{f.name}</span>
                            {f.relevance != null ? (
                              <Tag style={{ marginLeft: 6 }}>{f.relevance}%</Tag>
                            ) : null}
                          </Checkbox>
                        </div>
                      ))
                    )}
                  </div>
                </div>
              )}
            </div>
          )}
        </Card>
      </div>

      <div style={{ marginTop: 24, textAlign: 'center' }}>
        <Button type="primary" size="large" loading={loading} onClick={handleConfirm} disabled={checkedKeys.length === 0}>
          开始生成
        </Button>
      </div>

      <Modal
        title={selectedMethod?.title || '数据库内容'}
        open={fullContentOpen}
        onCancel={() => setFullContentOpen(false)}
        footer={null}
        width={720}
      >
        <pre style={{ whiteSpace: 'pre-wrap', fontSize: 13, maxHeight: '60vh', overflow: 'auto' }}>
          {selectedMethod?.dbFull || selectedMethod?.dbPreview || '（无内容）'}
        </pre>
      </Modal>
    </div>
  );
}
