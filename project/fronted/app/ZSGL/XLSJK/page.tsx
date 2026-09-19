'use client';

import React, { useState, useEffect, useCallback } from 'react';
import {
  Card, Table, Button, Upload, message, Space, Tag, Input, Modal, Form,
  Select, Popconfirm, DatePicker, Row, Col, Table as AntTable
} from 'antd';
import {
  UploadOutlined, DeleteOutlined, FileTextOutlined, CloudUploadOutlined, PlusOutlined, EditOutlined
} from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';

const { Option } = Select;

const NODE_TYPES = [
  { value: 'EquipmentType', label: '设备类型', color: '#1890ff' },
  { value: 'Equipment', label: '设备', color: '#52c41a' },
  { value: 'Environment', label: '环境', color: '#722ed1' },
  { value: 'TestProject', label: '项目', color: '#fa8c16' },
  { value: 'Indicator', label: '指标', color: '#13c2c2' },
  { value: 'TestMethod', label: '方法', color: '#eb2f96' },
];

interface VectorChunk {
  id: string;
  document: string;
  metadata: {
    doc_id: string;
    title: string;
    chunk_index: number;
    start_pos: number | null;
    end_pos: number | null;
    type: string;
    neo4j_testmethod_id: string;
    source_file: string;
    images: string;
    tables: string;
    formulas: string;
    User_id: string;
    Create_time: string;
  };
}

interface AssocRow {
  key: string;
  node_type: string;
  node_ids: string[];
  nodes: { id: string; name: string }[];
}

export default function XLSJKPage() {
  const [chunks, setChunks] = useState<VectorChunk[]>([]);
  const [loading, setLoading] = useState(false);
  const [uploadModalOpen, setUploadModalOpen] = useState(false);
  const [uploadFileList, setUploadFileList] = useState<File[]>([]);
  const [uploadDocType, setUploadDocType] = useState('标准');
  const [uploadAssociations, setUploadAssociations] = useState<AssocRow[]>([]);
  const [assocModalOpen, setAssocModalOpen] = useState(false);
  const [neo4jNodesCache, setNeo4jNodesCache] = useState<Record<string, { id: string; name: string }[]>>({});

  const [filters, setFilters] = useState({ type: '', user_id: '', doc_id: '' });

  const fetchChunks = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      if (filters.type) params.set('type', filters.type);
      if (filters.user_id) params.set('user_id', filters.user_id);
      if (filters.doc_id) params.set('doc_id', filters.doc_id);
      const res = await fetch(`/api/chroma/list?${params}`);
      const data = await res.json();
      if (data.success) setChunks(data.data);
    } catch {
      message.error('获取数据失败');
    } finally {
      setLoading(false);
    }
  }, [filters]);

  useEffect(() => { fetchChunks(); }, [fetchChunks]);

  const handleUpload = () => {
    if (!uploadFileList.length) { message.warning('请先选择文件'); return; }
    const formData = new FormData();
    uploadFileList.forEach(f => formData.append('files', f));
    formData.append('doc_type', uploadDocType);
    formData.append('user_id', 'admin');
    formData.append('associations', JSON.stringify(
      uploadAssociations.map(r => ({ node_type: r.node_type, node_ids: r.node_ids }))
    ));

    setLoading(true);
    fetch('/api/chroma/upload', { method: 'POST', body: formData })
      .then(r => r.json())
      .then(data => {
        if (data.success) {
          message.success(`成功处理 ${data.data?.length || 0} 个文件`);
          setUploadModalOpen(false);
          setUploadFileList([]);
          setUploadAssociations([]);
          fetchChunks();
        } else {
          message.error(data.error || '上传失败');
        }
      })
      .catch(() => message.error('上传失败'))
      .finally(() => setLoading(false));
  };

  const handleDelete = async (doc_id: string) => {
    try {
      const res = await fetch('/api/chroma/delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ doc_id }),
      });
      const data = await res.json();
      if (data.success) { message.success('删除成功'); fetchChunks(); }
      else message.error(data.error || '删除失败');
    } catch { message.error('删除失败'); }
  };

  const fetchNeo4jNodes = async (nodeType: string) => {
    if (neo4jNodesCache[nodeType]) return neo4jNodesCache[nodeType];
    try {
      const res = await fetch(`/api/wjgl/neo4j-nodes?node_type=${nodeType}`);
      const data = await res.json();
      if (data.success) {
        setNeo4jNodesCache(prev => ({ ...prev, [nodeType]: data.data }));
        return data.data;
      }
    } catch { }
    return [];
  };

  const addAssocRow = async (setAssocs: React.Dispatch<React.SetStateAction<AssocRow[]>>) => {
    setAssocs(prev => [...prev, { key: `assoc-${Date.now()}`, node_type: '', node_ids: [], nodes: [] }]);
  };

  const removeAssocRow = (key: string, setAssocs: React.Dispatch<React.SetStateAction<AssocRow[]>>) => {
    setAssocs(prev => prev.filter(r => r.key !== key));
  };

  const columns: ColumnsType<VectorChunk> = [
    { title: '文档ID', dataIndex: ['metadata', 'doc_id'], key: 'doc_id', width: 100 },
    { title: '标题', dataIndex: ['metadata', 'title'], key: 'title', ellipsis: true, width: 180 },
    { title: '块索引', dataIndex: ['metadata', 'chunk_index'], key: 'chunk_index', width: 70 },
    { title: '类型', dataIndex: ['metadata', 'type'], key: 'type', width: 70, render: (v: string) => <Tag color="blue">{v}</Tag> },
    { title: '关联方法ID', dataIndex: ['metadata', 'neo4j_testmethod_id'], key: 'neo4j_testmethod_id', width: 120 },
    { title: '源文件', dataIndex: ['metadata', 'source_file'], key: 'source_file', ellipsis: true, width: 200 },
    { title: '上传用户', dataIndex: ['metadata', 'User_id'], key: 'User_id', width: 80 },
    { title: '上传时间', dataIndex: ['metadata', 'Create_time'], key: 'Create_time', width: 170 },
    {
      title: '操作', key: 'action', width: 80, fixed: 'right' as const,
      render: (_: any, record: VectorChunk) => (
        <Popconfirm title="确定删除此文档所有块？" onConfirm={() => handleDelete(record.metadata.doc_id)}>
          <Button type="link" size="small" danger icon={<DeleteOutlined />}>删除</Button>
        </Popconfirm>
      ),
    },
  ];

  const renderAssocTable = (
    associations: AssocRow[],
    setAssociations: React.Dispatch<React.SetStateAction<AssocRow[]>>
  ) => {
    const assocColumns: ColumnsType<AssocRow> = [
      {
        title: '关联节点类型', dataIndex: 'node_type', key: 'node_type', width: 150,
        render: (val: string, record: AssocRow) => (
          <Select value={val || undefined} style={{ width: '100%' }} placeholder="选择节点类型"
            onChange={async (v) => {
              const nodes = await fetchNeo4jNodes(v);
              setAssociations(prev => prev.map(r => r.key === record.key ? { ...r, node_type: v, nodes, node_ids: [] } : r));
            }}>
            {NODE_TYPES.map(nt => <Option key={nt.value} value={nt.value}>{nt.label}</Option>)}
          </Select>
        ),
      },
      {
        title: '关联具体节点', dataIndex: 'node_ids', key: 'node_ids',
        render: (_: string[], record: AssocRow) => (
          <Select mode="multiple" value={record.node_ids} style={{ width: '100%' }}
            placeholder={record.node_type ? "选择节点" : "请先选择节点类型"} disabled={!record.node_type}
            onChange={(vals) => {
              setAssociations(prev => prev.map(r => r.key === record.key ? { ...r, node_ids: vals } : r));
            }}>
            {record.nodes?.map(n => <Option key={n.id} value={n.id}>{n.name}</Option>)}
          </Select>
        ),
      },
      {
        title: '', key: 'action', width: 60,
        render: (_: any, record: AssocRow) => (
          <Button type="link" danger size="small" onClick={() => removeAssocRow(record.key, setAssociations)}>删除</Button>
        ),
      },
    ];
    return (
      <div>
        <AntTable columns={assocColumns} dataSource={associations} rowKey="key" pagination={false} size="small" />
        <Button type="dashed" block style={{ marginTop: 8 }} onClick={() => addAssocRow(setAssociations)}>
          <PlusOutlined /> 新增关联行
        </Button>
      </div>
    );
  };

  const renderAssocLegend = (associations: AssocRow[]) => {
    const usedTypes = new Set(associations.filter(r => r.node_type).map(r => r.node_type));
    if (!usedTypes.size) return null;
    return (
      <Space wrap style={{ marginTop: 12 }}>
        {NODE_TYPES.filter(nt => usedTypes.has(nt.value)).map(nt => (
          <Tag key={nt.value} color={nt.color}>{nt.label}</Tag>
        ))}
      </Space>
    );
  };

  const uniqueDocs = (() => {
    const seen = new Set<string>();
    return chunks.filter(c => {
      if (seen.has(c.metadata.doc_id)) return false;
      seen.add(c.metadata.doc_id);
      return true;
    });
  })();

  const filteredChunks = uniqueDocs.filter(c => {
    if (filters.type && c.metadata.type !== filters.type) return false;
    if (filters.user_id && c.metadata.User_id !== filters.user_id) return false;
    if (filters.doc_id && !c.metadata.doc_id.includes(filters.doc_id)) return false;
    return true;
  });

  return (
    <div style={{ padding: 24, background: '#f5f5f5', minHeight: '100vh' }}>
      <Card style={{ borderRadius: 12 }}>
        <Card
          size="small"
          title={<Space><FileTextOutlined /> 向量数据库</Space>}
          extra={
            <Space>
              <Input placeholder="文档ID" value={filters.doc_id} onChange={e => setFilters(p => ({ ...p, doc_id: e.target.value }))} allowClear style={{ width: 120 }} />
              <Select value={filters.type} onChange={v => setFilters(p => ({ ...p, type: v }))} allowClear placeholder="类型" style={{ width: 100 }}>
                <Option value="标准">标准</Option>
                <Option value="方案">方案</Option>
                <Option value="规范">规范</Option>
                <Option value="模板">模板</Option>
                <Option value="其他">其他</Option>
              </Select>
              <Input placeholder="用户ID" value={filters.user_id} onChange={e => setFilters(p => ({ ...p, user_id: e.target.value }))} allowClear style={{ width: 100 }} />
              <Button type="primary" icon={<CloudUploadOutlined />} onClick={() => { setUploadFileList([]); setUploadAssociations([]); setUploadModalOpen(true); }}>
                上传文件
              </Button>
            </Space>
          }
        >
          <Table
            columns={columns}
            dataSource={filteredChunks}
            rowKey="id"
            loading={loading}
            scroll={{ x: 1200 }}
            pagination={{ pageSize: 15, showTotal: (t) => `共 ${t} 条` }}
          />
        </Card>
      </Card>

      {/* Upload Modal */}
      <Modal title="上传文件到向量数据库" open={uploadModalOpen} onCancel={() => setUploadModalOpen(false)} footer={null} width={800}>
        <div style={{ marginBottom: 16 }}>
          <Upload.Dragger multiple beforeUpload={(file) => { setUploadFileList(prev => [...prev, file]); return false; }} accept=".pdf,.docx,.doc">
            <p><CloudUploadOutlined /> 点击或拖拽文件到此区域（支持批量上传）</p>
          </Upload.Dragger>
        </div>

        {uploadFileList.length > 0 && (
          <>
            <div style={{ marginBottom: 12 }}>
              <Form layout="inline">
                <Form.Item label="文档类型">
                  <Select value={uploadDocType} onChange={setUploadDocType} style={{ width: 100 }}>
                    <Option value="标准">标准</Option>
                    <Option value="方案">方案</Option>
                    <Option value="规范">规范</Option>
                    <Option value="模板">模板</Option>
                    <Option value="其他">其他</Option>
                  </Select>
                </Form.Item>
              </Form>
            </div>

            <Table size="small"
              dataSource={uploadFileList.map((f, i) => ({ key: i, name: f.name, size: `${(f.size / 1024).toFixed(1)} KB`, type: f.name.split('.').pop() }))}
              columns={[
                { title: '文件名', dataIndex: 'name' },
                { title: '大小', dataIndex: 'size', width: 100 },
                { title: '类型', dataIndex: 'type', width: 60 },
              ]}
              pagination={false}
            />

            <div style={{ marginTop: 16 }}>
              <Button type="dashed" block onClick={() => setAssocModalOpen(true)}>
                配置关联节点
              </Button>
            </div>

            <div style={{ textAlign: 'right', marginTop: 16 }}>
              <Space>
                <Button onClick={() => setUploadModalOpen(false)}>取消</Button>
                <Button type="primary" onClick={handleUpload} loading={loading}>开始上传</Button>
              </Space>
            </div>
          </>
        )}
      </Modal>

      {/* Association Config Modal */}
      <Modal title="配置关联节点" open={assocModalOpen} onCancel={() => setAssocModalOpen(false)} onOk={() => setAssocModalOpen(false)} width={700}>
        {renderAssocTable(uploadAssociations, setUploadAssociations)}
        {renderAssocLegend(uploadAssociations)}
      </Modal>
    </div>
  );
}

export const dynamic = 'force-dynamic';
