'use client';
import React, { useMemo, useState, useCallback, useEffect } from 'react';
import { Upload, Button, message, Drawer, Tabs, Space, Spin, Typography, Input, Modal, Table, Radio, Tag, Form, Card, Select } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { FileSearchOutlined, ImportOutlined, EditOutlined } from '@ant-design/icons';
import GenericNodePanel from './GenericNodePanel';
import RichTextLatexEditor from '@/components/RichTextLatexEditor';
import MathAwareTextArea from '@/components/MathAwareTextArea';
import MixedContent from '@/components/ThreeColumnLayout/MixedContent';
import {
  parseEnvironmentDoc,
  importEnvironments,
  updateNode,
  type EnvImportConflict,
  type EnvImportResolution,
  type GraphNode,
  type ParsedEnvironment,
} from '../api';
import {
  FLGL_STATUS_OPTIONS,
  normalizeStatusValue,
} from '../utils/statusDisplay';

const { Title, Paragraph } = Typography;

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

const DIRECT_BACKEND = process.env.NEXT_PUBLIC_BACKEND_URL || 'http://localhost:5000';

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

  // 编辑抽屉状态
  const [editDrawerVisible, setEditDrawerVisible] = useState(false);
  const [editRecord, setEditRecord] = useState<GraphNode | null>(null);
  const [editForm] = Form.useForm();
  const [fiveElements, setFiveElements] = useState<any>(null);
  const [fiveLoading, setFiveLoading] = useState(false);
  // 富文本编辑器的值
  const [editorValues, setEditorValues] = useState({
    type: '',
    class: '',
    combination: '',
    limit: '',
    request: '',
  });

  // 获取环境五要素数据
  const fetchFiveElements = async (envName: string) => {
    setFiveLoading(true);
    try {
      const res = await fetch(`/api/GLCX/JQCX?path=/environment-settings-all`);
      const json = await res.json();
      if (json.success && json.data) {
        const found = json.data.find((item: any) => item.env_name === envName);
        if (found) {
          setFiveElements(found);
          // 更新编辑器的值
          setEditorValues({
            type: found.type || '',
            class: found.class || '',
            combination: found.combination || '',
            limit: found.limit || '',
            request: found.request || '',
          });
        } else {
          setFiveElements(null);
          setEditorValues({ type: '', class: '', combination: '', limit: '', request: '' });
        }
      } else {
        setFiveElements(null);
        setEditorValues({ type: '', class: '', combination: '', limit: '', request: '' });
      }
    } catch {
      setFiveElements(null);
      setEditorValues({ type: '', class: '', combination: '', limit: '', request: '' });
    } finally {
      setFiveLoading(false);
    }
  };

  // 打开编辑抽屉
  const handleOpenEditDrawer = useCallback((record: GraphNode): boolean => {
    setEditRecord(record);
    // 先设置表单基本字段
    editForm.setFieldsValue({
      name: record.name,
      description: record.description,
      status: normalizeStatusValue(record.status),
      user_name: record.user_name,
      create_time: record.create_time,
    });
    setEditDrawerVisible(true);
    // 获取五要素数据并填充到表单
    fetchFiveElements(String(record.name || record.id || '')).then(() => {
      // fiveElements 会在 state 更新后自动填充
    });
    return true;
  }, [editForm]);

  // 保存编辑
  const handleSaveEdit = async () => {
    try {
      const values = await editForm.validateFields();
      const { user_name, create_time, ...rest } = values;
      // 合并富文本编辑器的值
      const submitData = {
        ...rest,
        ...editorValues,
      };
      await updateNode('Environment', editRecord!.id, submitData);
      message.success('保存成功');
      setEditDrawerVisible(false);
      setEditRecord(null);
      setFiveElements(null);
      setRefreshKey((k) => k + 1);
      onRefresh();
    } catch (err: any) {
      if (err?.message) {
        message.error(err.message);
      }
    }
  };

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
          <MathAwareTextArea
            value={content || ''}
            onChange={(e) => updateParsedEnv(index, field, e.target.value)}
            autoSize={{ minRows: 4, maxRows: 12 }}
          />
        ) : hasHtml ? (
          <div style={{ overflowX: 'auto' }}><MixedContent content={content} /></div>
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
        onEdit={handleOpenEditDrawer}
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
          <Spin description="暂无数据" />
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
                        <MathAwareTextArea
                          value={env.description || ''}
                          onChange={(e) => updateParsedEnv(i, 'description', e.target.value)}
                          autoSize={{ minRows: 3, maxRows: 8 }}
                        />
                      ) : (env.description && (env.description.includes('<table') || env.description.includes('<img'))) ? (
                        <div style={{ overflowX: 'auto' }}><MixedContent content={env.description} /></div>
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

      {/* 环境编辑抽屉 */}
      <Drawer
        title={`编辑环境 - ${editRecord?.name || ''}`}
        placement="right"
        size={900}
        open={editDrawerVisible}
        onClose={() => {
          setEditDrawerVisible(false);
          setEditRecord(null);
          setFiveElements(null);
          setEditorValues({ type: '', class: '', combination: '', limit: '', request: '' });
        }}
        extra={
          <Space>
            <Button onClick={() => {
              setEditDrawerVisible(false);
              setEditRecord(null);
              setFiveElements(null);
              setEditorValues({ type: '', class: '', combination: '', limit: '', request: '' });
            }}>取消</Button>
            <Button type="primary" onClick={handleSaveEdit}>保存</Button>
          </Space>
        }
      >
        {editRecord && (
          <Form
            form={editForm}
            layout="vertical"
            initialValues={editRecord}
          >
            <Form.Item label="环境名称" name="name" rules={[{ required: true, message: '请输入环境名称' }]}>
              <Input />
            </Form.Item>

            <Form.Item label="类型">
              {fiveLoading ? <Spin /> : (
                <RichTextLatexEditor
                  value={editorValues.type}
                  onChange={(v) => setEditorValues(prev => ({ ...prev, type: v }))}
                  height={200}
                  active={editDrawerVisible}
                />
              )}
            </Form.Item>

            <Form.Item label="类别">
              {fiveLoading ? <Spin /> : (
                <RichTextLatexEditor
                  value={editorValues.class}
                  onChange={(v) => setEditorValues(prev => ({ ...prev, class: v }))}
                  height={200}
                  active={editDrawerVisible}
                />
              )}
            </Form.Item>

            <Form.Item label="组合条件">
              {fiveLoading ? <Spin /> : (
                <RichTextLatexEditor
                  value={editorValues.combination}
                  onChange={(v) => setEditorValues(prev => ({ ...prev, combination: v }))}
                  height={200}
                  active={editDrawerVisible}
                />
              )}
            </Form.Item>

            <Form.Item label="限制条件">
              {fiveLoading ? <Spin /> : (
                <RichTextLatexEditor
                  value={editorValues.limit}
                  onChange={(v) => setEditorValues(prev => ({ ...prev, limit: v }))}
                  height={200}
                  active={editDrawerVisible}
                />
              )}
            </Form.Item>

            <Form.Item label="试验要求">
              {fiveLoading ? <Spin /> : (
                <RichTextLatexEditor
                  value={editorValues.request}
                  onChange={(v) => setEditorValues(prev => ({ ...prev, request: v }))}
                  height={200}
                  active={editDrawerVisible}
                />
              )}
            </Form.Item>

            <Form.Item label="描述" name="description">
              <MathAwareTextArea rows={3} autoSize={{ minRows: 2, maxRows: 6 }} />
            </Form.Item>
            <Form.Item label="状态" name="status" rules={[{ required: true, message: '请选择状态' }]}>
              <Select options={FLGL_STATUS_OPTIONS} />
            </Form.Item>
            <Form.Item label="编辑用户" name="user_name">
              <Input readOnly />
            </Form.Item>
            <Form.Item label="编辑时间" name="create_time">
              <Input readOnly />
            </Form.Item>
          </Form>
        )}
      </Drawer>
    </>
  );
}
