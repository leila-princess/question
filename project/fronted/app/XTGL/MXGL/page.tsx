"use client";
import React, { useState, useEffect } from 'react';
import { 
  Card, Table, Button, Space, Tag, Modal, Form, Input, Switch, Select, InputNumber,
  message, Typography, Row, Col, Tabs, Descriptions, Divider,
  Badge, Tooltip, Popconfirm, Statistic
} from 'antd';
import { 
  PlusOutlined, EditOutlined, DeleteOutlined, SyncOutlined,
  HistoryOutlined, DatabaseOutlined, CloudServerOutlined,
  ApiOutlined, KeyOutlined, CheckCircleOutlined, CloseCircleOutlined
} from '@ant-design/icons';

const { Title, Text, Paragraph } = Typography;
const { TextArea } = Input;

interface ModelConfig {
  id?: number;
  model_key: string;
  model_name: string;
  model_type: string;
  model_path?: string;
  api_url?: string;
  api_key?: string;
  description?: string;
  /** 近似总上下文 token，用于本地小模型防 400 */
  context_window?: number;
  max_output_tokens?: number;
  is_local: boolean;
  is_active: boolean;
  created_at?: string;
  updated_at?: string;
}

interface ChangeLog {
  id: number;
  model_key: string;
  model_name: string;
  change_type: string;
  old_value?: any;
  new_value?: any;
  user_id: string;
  user_name: string;
  change_note: string;
  created_at: string;
}

interface LocalModel {
  name: string;
  path: string;
  type: string;
  size: number;
  modified: string;
}

export default function MXGLPage() {
  const [models, setModels] = useState<ModelConfig[]>([]);
  const [localModels, setLocalModels] = useState<LocalModel[]>([]);
  const [changeLogs, setChangeLogs] = useState<ChangeLog[]>([]);
  const [stats, setStats] = useState({ model_count: 0, log_count: 0, recent_logs: [] });
  const [loading, setLoading] = useState(false);
  const [modalVisible, setModalVisible] = useState(false);
  const [editingModel, setEditingModel] = useState<ModelConfig | null>(null);
  const [activeTab, setActiveTab] = useState('models');
  const [form] = Form.useForm();

  const fetchModels = async () => {
    try {
      setLoading(true);
      const res = await fetch('/api/xtgl/mxgl/models');
      if (res.ok) {
        const json = await res.json();
        setModels(json.data || []);
      }
    } catch (error) {
      console.error('获取模型配置失败', error);
    } finally {
      setLoading(false);
    }
  };

  const fetchLocalModels = async () => {
    try {
      const res = await fetch('/api/xtgl/mxgl/local-models');
      if (res.ok) {
        const json = await res.json();
        setLocalModels(json.data || []);
      }
    } catch (error) {
      console.error('获取本地模型失败', error);
    }
  };

  const fetchChangeLogs = async () => {
    try {
      const res = await fetch('/api/xtgl/mxgl/change-logs?limit=50');
      if (res.ok) {
        const json = await res.json();
        setChangeLogs(json.data || []);
      }
    } catch (error) {
      console.error('获取修改记录失败', error);
    }
  };

  const fetchStats = async () => {
    try {
      const res = await fetch('/api/xtgl/mxgl/stats');
      if (res.ok) {
        const json = await res.json();
        setStats(json.data || { model_count: 0, log_count: 0, recent_logs: [] });
      }
    } catch (error) {
      console.error('获取统计信息失败', error);
    }
  };

  useEffect(() => {
    fetchModels();
    fetchLocalModels();
    fetchChangeLogs();
    fetchStats();
  }, []);

  const handleEdit = (record: ModelConfig) => {
    setEditingModel(record);
    form.setFieldsValue(record);
    setModalVisible(true);
  };

  const handleCreate = () => {
    setEditingModel(null);
    form.resetFields();
    form.setFieldsValue({
      model_type: 'llm',
      is_local: false,
      is_active: true,
      context_window: 16384,
      max_output_tokens: 2048,
    });
    setModalVisible(true);
  };

  const handleSave = async () => {
    try {
      const values = await form.validateFields();
      const method = editingModel ? 'PUT' : 'POST';
      const url = editingModel
        ? `/api/xtgl/mxgl/models/${editingModel.model_key}`
        : '/api/xtgl/mxgl/models';

      const res = await fetch(url, {
        method,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ...values,
          user_id: 'admin',
          user_name: '系统管理员',
          change_note: '通过管理界面修改'
        })
      });
      if (res.ok) {
        message.success(editingModel ? '模型配置更新成功' : '模型配置新增成功');
        setModalVisible(false);
        fetchModels();
        fetchChangeLogs();
        fetchStats();
      } else {
        message.error('更新失败');
      }
    } catch (error) {
      message.error('保存请求失败');
    }
  };

  const handleDelete = async (modelKey: string) => {
    try {
      const res = await fetch(`/api/xtgl/mxgl/models/${modelKey}`, { method: 'DELETE' });
      if (res.ok) {
        message.success('模型删除成功');
        fetchModels();
        fetchChangeLogs();
        fetchStats();
      } else {
        message.error('删除失败');
      }
    } catch {
      message.error('删除请求失败');
    }
  };

  const handleSyncToEnv = async (modelKey: string) => {
    try {
      const model = models.find(m => m.model_key === modelKey);
      if (!model) return;
      
      const res = await fetch(`/api/xtgl/mxgl/models/${modelKey}/sync-env`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ...model,
          user_id: 'admin',
          user_name: '系统管理员'
        })
      });
      if (res.ok) {
        const json = await res.json();
        message.success(`已同步到.env文件: ${json.updated_keys?.join(', ')}`);
      } else {
        message.error('同步失败');
      }
    } catch (error) {
      message.error('同步请求失败');
    }
  };

  const formatSize = (bytes: number) => {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(2)} KB`;
    if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
    return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
  };

  const modelColumns = [
    {
      title: '模型标识',
      dataIndex: 'model_key',
      key: 'model_key',
      width: 120,
      render: (text: string) => <Text strong>{text}</Text>
    },
    {
      title: '模型名称',
      dataIndex: 'model_name',
      key: 'model_name',
      width: 200,
    },
    {
      title: '类型',
      dataIndex: 'model_type',
      key: 'model_type',
      width: 100,
      render: (type: string) => {
        const colorMap: Record<string, string> = {
          llm: 'blue',
          embedding: 'green',
          reranker: 'orange',
        };
        return <Tag color={colorMap[type] || 'default'}>{type}</Tag>;
      }
    },
    {
      title: '位置',
      dataIndex: 'is_local',
      key: 'is_local',
      width: 100,
      render: (isLocal: boolean, record: ModelConfig) => (
        <Space>
          {isLocal ? (
            <Tag icon={<DatabaseOutlined />} color="cyan">本地</Tag>
          ) : (
            <Tag icon={<CloudServerOutlined />} color="purple">API</Tag>
          )}
        </Space>
      )
    },
    {
      title: '状态',
      dataIndex: 'is_active',
      key: 'is_active',
      width: 80,
      render: (isActive: boolean) => (
        isActive ? (
          <Badge status="success" text="启用" />
        ) : (
          <Badge status="error" text="禁用" />
        )
      )
    },
    {
      title: '描述',
      dataIndex: 'description',
      key: 'description',
      ellipsis: true,
    },
    {
      title: '操作',
      key: 'action',
      width: 240,
      render: (_: any, record: ModelConfig) => (
        <Space size="small">
          <Tooltip title="编辑配置">
            <Button 
              type="primary" 
              icon={<EditOutlined />} 
              size="small"
              onClick={() => handleEdit(record)}
            />
          </Tooltip>
          <Tooltip title="同步到.env">
            <Button 
              icon={<SyncOutlined />} 
              size="small"
              onClick={() => handleSyncToEnv(record.model_key)}
            />
          </Tooltip>
          <Popconfirm title="确认删除该模型？" onConfirm={() => handleDelete(record.model_key)}>
            <Button danger icon={<DeleteOutlined />} size="small" />
          </Popconfirm>
        </Space>
      )
    },
  ];

  const isLlmType = (t?: string) => (t || '').toLowerCase() === 'llm';
  const llmModels = models.filter((m) => isLlmType(m.model_type));
  const encodingModels = models.filter((m) => !isLlmType(m.model_type));

  const localModelColumns = [
    {
      title: '模型名称',
      dataIndex: 'name',
      key: 'name',
      width: 300,
      render: (text: string) => <Text strong>{text}</Text>
    },
    {
      title: '类型',
      dataIndex: 'type',
      key: 'type',
      width: 100,
      render: (type: string) => {
        const colorMap: Record<string, string> = {
          'embedding': 'green',
          'reranker': 'orange',
          'unknown': 'default'
        };
        return <Tag color={colorMap[type] || 'default'}>{type}</Tag>;
      }
    },
    {
      title: '大小',
      dataIndex: 'size',
      key: 'size',
      width: 120,
      render: (size: number) => formatSize(size)
    },
    {
      title: '路径',
      dataIndex: 'path',
      key: 'path',
      ellipsis: true,
    },
    {
      title: '修改时间',
      dataIndex: 'modified',
      key: 'modified',
      width: 180,
      render: (time: string) => time ? new Date(time).toLocaleString() : '-'
    }
  ];

  const logColumns = [
    {
      title: '时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 180,
      render: (time: string) => time ? new Date(time).toLocaleString() : '-'
    },
    {
      title: '模型',
      dataIndex: 'model_name',
      key: 'model_name',
      width: 150,
    },
    {
      title: '操作类型',
      dataIndex: 'change_type',
      key: 'change_type',
      width: 100,
      render: (type: string) => {
        const colorMap: Record<string, string> = {
          'create': 'green',
          'update': 'blue',
          'delete': 'red',
          'env_sync': 'purple'
        };
        const textMap: Record<string, string> = {
          'create': '创建',
          'update': '更新',
          'delete': '删除',
          'env_sync': '同步'
        };
        return <Tag color={colorMap[type] || 'default'}>{textMap[type] || type}</Tag>;
      }
    },
    {
      title: '操作人',
      dataIndex: 'user_name',
      key: 'user_name',
      width: 120,
    },
    {
      title: '说明',
      dataIndex: 'change_note',
      key: 'change_note',
      ellipsis: true,
    }
  ];

  const tabItems = [
    {
      key: 'models',
      label: (
        <Space>
          <CloudServerOutlined />
          模型配置
          <Badge count={models.length} style={{ backgroundColor: '#52c41a' }} showZero />
        </Space>
      ),
      children: (
        <div>
          <Row gutter={16} style={{ marginBottom: 16 }}>
            <Col span={6}>
              <Card size="small">
                <Statistic title="模型总数" value={stats.model_count} prefix={<DatabaseOutlined />} />
              </Card>
            </Col>
            <Col span={6}>
              <Card size="small">
                <Statistic title="修改记录" value={stats.log_count} prefix={<HistoryOutlined />} />
              </Card>
            </Col>
            <Col span={12}>
              <Card size="small" title="最近修改">
                <Space direction="vertical" style={{ width: '100%' }}>
                  {stats.recent_logs?.slice(0, 3).map((log: any, index: number) => (
                    <div key={index} style={{ display: 'flex', justifyContent: 'space-between' }}>
                      <Text ellipsis style={{ maxWidth: 200 }}>{log.model_name}</Text>
                      <Tag color="blue">{log.change_type}</Tag>
                    </div>
                  ))}
                </Space>
              </Card>
            </Col>
          </Row>

          <Card
            size="small"
            title="LLM 模型"
            extra={<Text type="secondary">共 {llmModels.length} 条</Text>}
            style={{ marginBottom: 16 }}
          >
            <Table
              dataSource={llmModels}
              rowKey="model_key"
              columns={modelColumns}
              size="small"
              pagination={{ pageSize: 10 }}
              loading={loading}
              locale={{ emptyText: '暂无 LLM 模型配置' }}
            />
          </Card>

          <Card
            size="small"
            title="编码模型管理"
            extra={<Text type="secondary">共 {encodingModels.length} 条（Embedding、Reranker 等非 LLM）</Text>}
          >
            <Table
              dataSource={encodingModels}
              rowKey="model_key"
              columns={modelColumns}
              size="small"
              pagination={{ pageSize: 10 }}
              loading={loading}
              locale={{ emptyText: '暂无编码 / 检索类模型配置' }}
            />
          </Card>
        </div>
      )
    },
    {
      key: 'local',
      label: (
        <Space>
          <DatabaseOutlined />
          本地模型
          <Badge count={localModels.length} style={{ backgroundColor: '#1890ff' }} />
        </Space>
      ),
      children: (
        <Table 
          dataSource={localModels} 
          rowKey="name" 
          columns={localModelColumns} 
          size="small" 
          pagination={{ pageSize: 10 }}
        />
      )
    },
    {
      key: 'logs',
      label: (
        <Space>
          <HistoryOutlined />
          修改记录
          <Badge count={changeLogs.length} style={{ backgroundColor: '#faad14' }} />
        </Space>
      ),
      children: (
        <Table 
          dataSource={changeLogs} 
          rowKey="id" 
          columns={logColumns} 
          size="small" 
          pagination={{ pageSize: 10 }}
        />
      )
    }
  ];

  return (
    <div
      style={{
        height: '100%',
        minHeight: 0,
        overflow: 'hidden',
        boxSizing: 'border-box',
        padding: 24,
        display: 'flex',
        flexDirection: 'column',
      }}
    >
      <Card 
        style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}
        styles={{ body: { flex: 1, minHeight: 0, overflow: 'auto' } }}
        title={
          <Space>
            <CloudServerOutlined style={{ fontSize: 20 }} />
            <Title level={4} style={{ margin: 0 }}>模型管理</Title>
          </Space>
        }
        extra={
          <Space>
            <Button type="primary" icon={<PlusOutlined />} onClick={handleCreate}>
              新增模型
            </Button>
            <Button icon={<SyncOutlined />} onClick={() => { fetchModels(); fetchLocalModels(); fetchChangeLogs(); fetchStats(); }}>
              刷新
            </Button>
          </Space>
        }
      >
        <Tabs activeKey={activeTab} onChange={setActiveTab} items={tabItems} />
      </Card>

      <Modal
        title={editingModel ? '编辑模型配置' : '新增模型配置'}
        open={modalVisible}
        onOk={handleSave}
        onCancel={() => setModalVisible(false)}
        width={600}
      >
        <Form form={form} layout="vertical">
          {!editingModel && (
            <Form.Item name="model_key" label="模型标识" rules={[{ required: true }]}>
              <Input placeholder="例如：qwen_14b_local" />
            </Form.Item>
          )}
          <Form.Item name="model_type" label="模型类型" rules={[{ required: true }]}>
            <Select
              options={[
                { value: 'llm', label: 'LLM' },
                { value: 'embedding', label: 'Embedding' },
                { value: 'reranker', label: 'Reranker' },
              ]}
            />
          </Form.Item>
          <Form.Item name="model_name" label="模型名称" rules={[{ required: true }]}>
            <Input placeholder="输入模型名称" />
          </Form.Item>
          <Form.Item name="is_local" label="模型位置" valuePropName="checked">
            <Switch checkedChildren="本地" unCheckedChildren="API" />
          </Form.Item>
          <Form.Item name="api_url" label="API地址">
            <Input placeholder="输入API地址（本地模型留空）" />
          </Form.Item>
          <Form.Item name="api_key" label="API密钥">
            <Input placeholder="输入API密钥（明文显示）" />
          </Form.Item>
          <Form.Item name="description" label="描述">
            <TextArea rows={3} placeholder="输入模型描述" />
          </Form.Item>
          <Form.Item name="is_active" label="启用状态" valuePropName="checked">
            <Switch checkedChildren="启用" unCheckedChildren="禁用" />
          </Form.Item>
          <Form.Item noStyle shouldUpdate={(prev, cur) => prev.model_type !== cur.model_type}>
            {() =>
              form.getFieldValue('model_type') === 'llm' ? (
                <>
                  <Form.Item
                    name="context_window"
                    label="上下文长度（近似 token）"
                    rules={[{ required: true, message: '请填写上下文上限' }]}
                    extra="与本地/云端模型总上下文一致或略小，用于自动压缩与分片摘要"
                  >
                    <InputNumber min={1024} max={2000000} style={{ width: '100%' }} />
                  </Form.Item>
                  <Form.Item
                    name="max_output_tokens"
                    label="单次最大输出 token"
                    rules={[{ required: true, message: '请填写输出上限' }]}
                    extra="须小于上下文长度，避免 input+max_tokens 超限"
                  >
                    <InputNumber min={128} max={65536} style={{ width: '100%' }} />
                  </Form.Item>
                </>
              ) : null
            }
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}