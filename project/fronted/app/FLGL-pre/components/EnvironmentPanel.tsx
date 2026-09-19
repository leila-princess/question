'use client';
import React, { useMemo, useState } from 'react';
import { Upload, Button, message, Drawer, Tabs, Space, Spin, Typography, Input, Modal, Table, Radio, Tag } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { FileSearchOutlined, ImportOutlined, EditOutlined } from '@ant-design/icons';
import GenericNodePanel from './GenericNodePanel';
import {
  parseEnvironmentDoc,
  importEnvironments,
  type EnvImportConflict,
  type EnvImportResolution,
  type GraphNode,
  type ParsedEnvironment,
} from '../api';

const { Title, Paragraph } = Typography;
const { TextArea } = Input;

function defaultResolutionsForConflicts(envs: ParsedEnvironment[], conflicts: EnvImportConflict[]): EnvImportResolution[] {
  const cm = new Map(conflicts.map((c) => [c.index, c]));
  return envs.map((_, i) => {
    const c = cm.get(i);
    if (!c) return { action: 'create' as const };
    if (c.reason === 'empty_name') return { action: 'skip' as const };
    if (c.reason === 'exists_in_db' && c.existing_id) return { action: 'replace' as const, existing_id: c.existing_id };
    return { action: 'rename' as const, new_name: '' };
  });
}

function reasonLabel(c: EnvImportConflict): string {
  if (c.reason === 'empty_name') return '名称为空';
  if (c.reason === 'exists_in_db') return '库中已有同名节点';
  return `批次内重复（与第 ${(c.duplicate_of_index ?? 0) + 1} 条同名）`;
}

export default function EnvironmentPanel({ allNodes, onRefresh }: { allNodes: GraphNode[]; onRefresh: () => void }) {
  const [drawerVisible, setDrawerVisible] = useState(false);
  const [parsedEnvs, setParsedEnvs] = useState<ParsedEnvironment[]>([]);
  const [uploading, setUploading] = useState(false);
  const [importing, setImporting] = useState(false);
  const [editing, setEditing] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);
  const [conflictOpen, setConflictOpen] = useState(false);
  const [conflicts, setConflicts] = useState<EnvImportConflict[]>([]);
  const [importResolutions, setImportResolutions] = useState<EnvImportResolution[]>([]);

  const handleUpload = async (file: File) => {
    setUploading(true);
    try {
      const result = await parseEnvironmentDoc(file);
      if (result.success && result.data) {
        setParsedEnvs(result.data);
        setEditing(false);
        setDrawerVisible(true);
        message.success(`解析完成，共提取 ${result.data.length} 个环境`);
      } else {
        message.error(result.error || '解析失败');
      }
    } catch {
      message.error('解析请求失败');
    } finally {
      setUploading(false);
    }
    return false; // 阻止 antd 默认上传
  };

  const finishImportDone = (created: number, replaced: number, skipped: number, renamed: number) => {
    const parts = [
      created ? `新建 ${created}` : '',
      replaced ? `替换 ${replaced}` : '',
      skipped ? `跳过 ${skipped}` : '',
      renamed ? `重命名新建 ${renamed}` : '',
    ].filter(Boolean);
    message.success(parts.length ? `导入完成：${parts.join('，')}` : '导入完成');
    setDrawerVisible(false);
    setParsedEnvs([]);
    setConflictOpen(false);
    setConflicts([]);
    setImportResolutions([]);
    setRefreshKey((k) => k + 1);
    onRefresh();
  };

  const handleImport = async () => {
    if (parsedEnvs.length === 0) {
      message.warning('没有可导入的环境数据');
      return;
    }
    setImporting(true);
    try {
      const result = await importEnvironments(parsedEnvs);
      if (!result.success) {
        message.error(result.error || '导入失败');
        return;
      }
      const data = result.data;
      if (!data) {
        message.error('导入失败');
        return;
      }
      if (data.status === 'conflict') {
        setConflicts(data.conflicts);
        setImportResolutions(defaultResolutionsForConflicts(parsedEnvs, data.conflicts));
        setConflictOpen(true);
        return;
      }
      if (data.status === 'done') {
        finishImportDone(data.created, data.replaced, data.skipped, data.renamed);
      }
    } catch {
      message.error('导入请求失败');
    } finally {
      setImporting(false);
    }
  };

  const applyConflictResolutions = async () => {
    if (importResolutions.length !== parsedEnvs.length) {
      message.error('策略数据异常');
      return;
    }
    for (let i = 0; i < importResolutions.length; i++) {
      const r = importResolutions[i];
      if (r.action === 'rename' && !(r.new_name || '').trim()) {
        message.warning(`第 ${i + 1} 条选择了「重命名」，请填写新名称`);
        return;
      }
      if (r.action === 'replace' && !(r.existing_id || '').trim()) {
        message.warning(`第 ${i + 1} 条缺少可替换的节点 ID`);
        return;
      }
    }
    setImporting(true);
    try {
      const result = await importEnvironments(parsedEnvs, importResolutions);
      if (!result.success) {
        message.error(result.error || '导入失败');
        return;
      }
      const data = result.data;
      if (data?.status === 'done') {
        finishImportDone(data.created, data.replaced, data.skipped, data.renamed);
      }
    } catch {
      message.error('导入请求失败');
    } finally {
      setImporting(false);
    }
  };

  const conflictColumns: ColumnsType<EnvImportConflict> = useMemo(
    () => [
      {
        title: '序号',
        width: 56,
        render: (_, row) => row.index + 1,
      },
      {
        title: '名称',
        dataIndex: 'name',
        ellipsis: true,
        render: (v: string) => v || '—',
      },
      {
        title: '原因',
        width: 220,
        render: (_, row) => <Tag color="orange">{reasonLabel(row)}</Tag>,
      },
      {
        title: '已有节点 ID',
        width: 140,
        ellipsis: true,
        render: (_, row) => row.existing_id || '—',
      },
      {
        title: '处理方式',
        width: 320,
        render: (_, row) => {
          const i = row.index;
          const r = importResolutions[i];
          if (!r) return null;
          const canReplace = !!row.existing_id;
          return (
            <Radio.Group
              value={r.action}
              onChange={(e) => {
                const action = e.target.value as EnvImportResolution['action'];
                setImportResolutions((prev) => {
                  const next = [...prev];
                  let cur: EnvImportResolution;
                  if (action === 'skip') cur = { action: 'skip' };
                  else if (action === 'replace' && row.existing_id) cur = { action: 'replace', existing_id: row.existing_id };
                  else if (action === 'rename') cur = { action: 'rename', new_name: prev[i]?.new_name || '' };
                  else cur = { action: 'skip' };
                  next[i] = cur;
                  return next;
                });
              }}
            >
              <Space direction="vertical" size={4}>
                {canReplace ? (
                  <Radio value="replace">替换库中同名节点（覆盖字段）</Radio>
                ) : null}
                <Radio value="skip">跳过，不导入</Radio>
                <Radio value="rename">另存为新名称</Radio>
              </Space>
            </Radio.Group>
          );
        },
      },
      {
        title: '新名称',
        width: 200,
        render: (_, row) => {
          const i = row.index;
          const r = importResolutions[i];
          if (r?.action !== 'rename') return '—';
          return (
            <Input
              placeholder="不与库中重复"
              value={r.new_name || ''}
              onChange={(e) => {
                const v = e.target.value;
                setImportResolutions((prev) => {
                  const next = [...prev];
                  next[i] = { ...next[i], action: 'rename', new_name: v };
                  return next;
                });
              }}
            />
          );
        },
      },
    ],
    [conflicts, importResolutions],
  );

  const updateParsedEnv = (index: number, field: keyof ParsedEnvironment, value: string) => {
    setParsedEnvs(prev => prev.map((env, i) => (i === index ? { ...env, [field]: value } : env)));
  };

  const renderSection = (title: string, content: string, index: number, field: keyof ParsedEnvironment) => {
    if (!content && !editing) return null;
    const hasHtml = !!content && (content.includes('<table') || content.includes('<img'));
    return (
      <div style={{ marginBottom: 16 }}>
        <Title level={5}>{title}</Title>
        {editing ? (
          <TextArea
            value={content || ''}
            onChange={(e) => updateParsedEnv(index, field, e.target.value)}
            autoSize={{ minRows: 4, maxRows: 12 }}
          />
        ) : hasHtml ? (
          <div dangerouslySetInnerHTML={{ __html: content }} style={{ overflowX: 'auto' }} />
        ) : (
          <Paragraph style={{ whiteSpace: 'pre-wrap' }}>{content}</Paragraph>
        )}
      </div>
    );
  };

  const extraButtons = (
    <Upload beforeUpload={handleUpload} showUploadList={false} accept=".doc,.docx">
      <Button icon={<FileSearchOutlined />} loading={uploading}>解析环境文档</Button>
    </Upload>
  );

  return (
    <>
      <GenericNodePanel
        nodeType="Environment"
        title="环境管理"
        allNodes={allNodes}
        selectedCategory={null}
        onRefresh={onRefresh}
        extraButtons={extraButtons}
        refreshKey={refreshKey}
      />

      <Modal
        title="导入名称冲突"
        open={conflictOpen}
        onCancel={() => setConflictOpen(false)}
        width={960}
        okText="按选择导入"
        cancelText="取消"
        confirmLoading={importing}
        onOk={() => applyConflictResolutions()}
      >
        <Paragraph type="secondary" style={{ marginBottom: 12 }}>
          以下条目与已有数据或批次内名称冲突，请选择替换（覆盖已有节点）、跳过或重命名后再导入。未列出的条目将按「新建」写入。
        </Paragraph>
        <Table
          size="small"
          rowKey={(r) => String(r.index)}
          dataSource={conflicts}
          columns={conflictColumns}
          pagination={false}
          scroll={{ x: 900 }}
        />
      </Modal>

      <Drawer
        title="环境文档解析结果"
        open={drawerVisible}
        onClose={() => setDrawerVisible(false)}
        size="large"
        extra={
          <Space>
            <Button icon={<EditOutlined />} onClick={() => setEditing(v => !v)}>
              {editing ? '完成编辑' : '编辑解析结果'}
            </Button>
            <Button onClick={() => setDrawerVisible(false)}>关闭</Button>
            <Button type="primary" icon={<ImportOutlined />} onClick={handleImport} loading={importing}>
              批量导入 Neo4j
            </Button>
          </Space>
        }
      >
        {parsedEnvs.length === 0 ? (
          <Spin tip="暂无数据" />
        ) : (
          <Tabs
            items={parsedEnvs.map((env, i) => ({
              key: String(i),
              label: env.name || `环境 ${i + 1}`,
              children: (
                <div>
                  <div style={{ marginBottom: 16 }}>
                    <Title level={5}>环境名称</Title>
                    {editing ? (
                      <Input value={env.name || ''} onChange={(e) => updateParsedEnv(i, 'name', e.target.value)} />
                    ) : (
                      <Paragraph>{env.name}</Paragraph>
                    )}
                  </div>
                  {(env.description || editing) && (
                    <div style={{ marginBottom: 16 }}>
                      <Title level={5}>环境描述</Title>
                      {editing ? (
                        <TextArea
                          value={env.description || ''}
                          onChange={(e) => updateParsedEnv(i, 'description', e.target.value)}
                          autoSize={{ minRows: 3, maxRows: 8 }}
                        />
                      ) : (env.description && (env.description.includes('<table') || env.description.includes('<img'))) ? (
                        <div dangerouslySetInnerHTML={{ __html: env.description }} style={{ overflowX: 'auto' }} />
                      ) : (
                        <Paragraph style={{ whiteSpace: 'pre-wrap' }}>{env.description}</Paragraph>
                      )}
                    </div>
                  )}
                  {renderSection('环境分类', env.type, i, 'type')}
                  {renderSection('环境分级', env.class, i, 'class')}
                  {renderSection('环境组合', env.combination, i, 'combination')}
                  {renderSection('约束条件', env.limit, i, 'limit')}
                  {renderSection('试验要求', env.request, i, 'request')}
                </div>
              ),
            }))}
          />
        )}
      </Drawer>
    </>
  );
}
