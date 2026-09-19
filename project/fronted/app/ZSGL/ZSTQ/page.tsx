'use client';

import React, { useState, useEffect, useMemo, useCallback, Suspense } from 'react';
import {
  Upload, Button, Card, Row, Col, Spin, message, Space, Typography,
  Table, Modal, Form, Input, Select, Tag, Popconfirm, Progress, Image,
  Tabs, Tree, Badge, Alert, Empty, Tooltip, Divider, Statistic, Collapse,
  Descriptions, Switch, Slider, InputNumber, Steps
} from 'antd';
import {
  CloudUploadOutlined, EditOutlined, DeleteOutlined, PlusOutlined,
  EyeOutlined, ArrowLeftOutlined, FileTextOutlined, BranchesOutlined,
  CheckCircleOutlined, CloseCircleOutlined, FilePdfOutlined, FileWordOutlined,
  LoadingOutlined, SaveOutlined, SyncOutlined, InfoCircleOutlined,
  TableOutlined, PictureOutlined, NodeIndexOutlined, SettingOutlined
} from '@ant-design/icons';
import type { UploadFile, UploadProps } from 'antd/es/upload/interface';
import type { ColumnsType } from 'antd/es/table';
import { useRouter } from 'next/navigation';

const { Title, Text, Paragraph } = Typography;
const { Panel } = Collapse;

interface KGNode {
  id: string;
  labels: string[];
  properties: Record<string, any>;
}

interface KGRelationship {
  id: string;
  type: string;
  startNodeId: string;
  endNodeId: string;
  properties: Record<string, any>;
}

interface ExtractionResult {
  nodes: KGNode[];
  relationships: KGRelationship[];
  metadata?: {
    pdf_name: string;
    processed_at: string;
    watermarks?: string[];
    [key: string]: any;
  };
}

interface ProgressInfo {
  figure_progress: number;
  text_progress: number;
  stage: string;
  stage_name: string;
  current: number;
  total: number;
  percent: number;
  message: string;
}

interface ConfigItem {
  value: any;
  type: string;
  label: string;
  description?: string;
  group?: string;
  min?: number;
  max?: number;
  step?: number;
  options?: string[];
}

interface ExtractionConfig {
  gibberish_min_length: number;
  gibberish_acceptable_ratio: number;
  enable_ocr: boolean;
  force_ocr_all_pages: boolean;
  pdf_image_dpi: number;
  llm_model: string;
  llm_temperature: number;
  llm_max_tokens: number;
  save_text: boolean;
  save_tables: boolean;
  save_figures: boolean;
}

interface DocumentMetadata {
  standard_number: string;
  chinese_name: string;
  english_name: string;
  publish_date: string;
  implement_date: string;
  publish_department: string;
  replaced_standard: string;
  filing_department: string;
}

function KnowledgeExtractionPageContent() {
  const router = useRouter();
  const [uploading, setUploading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [extractionResult, setExtractionResult] = useState<ExtractionResult | null>(null);
  const [originalExtraction, setOriginalExtraction] = useState<ExtractionResult | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [fileList, setFileList] = useState<UploadFile[]>([]);
  const [editMode, setEditMode] = useState<'view' | 'edit'>('view');
  const [editedNodes, setEditedNodes] = useState<KGNode[]>([]);
  const [editedRelationships, setEditedRelationships] = useState<KGRelationship[]>([]);
  
  const [taskId, setTaskId] = useState<string | null>(null);
  const [progress, setProgress] = useState<ProgressInfo | null>(null);
  const [pollingInterval, setPollingInterval] = useState<NodeJS.Timeout | null>(null);

  const [nodeModalVisible, setNodeModalVisible] = useState(false);
  const [relModalVisible, setRelModalVisible] = useState(false);
  const [currentNode, setCurrentNode] = useState<KGNode | null>(null);
  const [currentRel, setCurrentRel] = useState<KGRelationship | null>(null);
  const [nodeForm] = Form.useForm();
  const [relForm] = Form.useForm();

  const [tableModalVisible, setTableModalVisible] = useState(false);
  const [currentTableNode, setCurrentTableNode] = useState<KGNode | null>(null);
  const [editingTableData, setEditingTableData] = useState<any>(null);

  const [imagePreviewVisible, setImagePreviewVisible] = useState(false);
  const [imagePreviewSrc, setImagePreviewSrc] = useState<string | null>(null);

  const [docMetadata, setDocMetadata] = useState<DocumentMetadata | null>(null);
  const [extractingMeta, setExtractingMeta] = useState(false);

  const [activeTab, setActiveTab] = useState('nodes');
  const [configModalVisible, setConfigModalVisible] = useState(false);
  const [extractionConfig, setExtractionConfig] = useState<ExtractionConfig>({
    gibberish_min_length: 20,
    gibberish_acceptable_ratio: 0.8,
    enable_ocr: true,
    force_ocr_all_pages: false,
    pdf_image_dpi: 300,
    llm_model: 'qwen-plus',
    llm_temperature: 0.1,
    llm_max_tokens: 4000,
    save_text: true,
    save_tables: true,
    save_figures: true,
  });
  const [configSchema, setConfigSchema] = useState<Record<string, ConfigItem>>({});
  const [loadingConfig, setLoadingConfig] = useState(false);

  useEffect(() => {
    fetchConfigSchema();
  }, []);

  const fetchConfigSchema = async () => {
    setLoadingConfig(true);
    try {
      const res = await fetch('/api/pdf2kg/config');
      if (res.ok) {
        const data = await res.json();
        const config = data.config as Record<string, ConfigItem> | undefined;
        setConfigSchema(config || {});
        if (config) {
          const defaultValues: Record<string, any> = {};
          Object.entries(config).forEach(([key, item]) => {
            defaultValues[key] = item.value;
          });
          setExtractionConfig(prev => ({ ...prev, ...defaultValues }));
        }
      }
    } catch (err) {
      console.error('获取配置失败:', err);
    } finally {
      setLoadingConfig(false);
    }
  };

  const handleConfigChange = (key: string, value: any) => {
    setExtractionConfig(prev => ({ ...prev, [key]: value }));
  };

  const handleReset = useCallback(() => {
    setExtractionResult(null);
    setOriginalExtraction(null);
    setSessionId(null);
    setFileList([]);
    setEditMode('view');
    setEditedNodes([]);
    setEditedRelationships([]);
    setTaskId(null);
    setProgress(null);
    setDocMetadata(null);
    setExtractingMeta(false);
    if (pollingInterval) {
      clearInterval(pollingInterval);
      setPollingInterval(null);
    }
  }, [pollingInterval]);

  const handleUploadSuccess = (data: any, taskId: string) => {
    setOriginalExtraction(data);
    setExtractionResult(data);
    setEditedNodes(data.nodes || []);
    setEditedRelationships(data.relationships || []);
    setSessionId(taskId);
    setUploading(false);
    setEditMode('edit');
    message.success('文档解析完成！请确认知识图谱数据');

    extractMetadataFromUploadedFile();
  };

  const extractMetadataFromUploadedFile = async () => {
    if (!fileList.length) return;
    setExtractingMeta(true);
    try {
      const formData = new FormData();
      formData.append('file', fileList[0].originFileObj as Blob);
      const res = await fetch('/api/ZSGL/ZSTQ/extract-metadata', {
        method: 'POST',
        body: formData,
      });
      if (res.ok) {
        const data = await res.json();
        setDocMetadata(data.metadata);
      }
    } catch (err) {
      console.error('元数据提取失败:', err);
    } finally {
      setExtractingMeta(false);
    }
  };

  const startPolling = (id: string) => {
    setTaskId(id);
    const interval = setInterval(async () => {
      try {
        const res = await fetch(`/api/ZSGL/ZSTQ/progress/${id}`);
        if (!res.ok) {
          if (res.status === 404) {
            clearInterval(interval);
            setPollingInterval(null);
            return;
          }
          throw new Error('进度获取失败');
        }
        const data = await res.json();
        if (data.status === 'failed') {
          clearInterval(interval);
          setPollingInterval(null);
          message.error(`处理失败: ${data.error}`);
          handleReset();
          return;
        }
        setProgress(data.progress);
        if (data.status === 'completed') {
          clearInterval(interval);
          setPollingInterval(null);
          const resultRes = await fetch(`/api/ZSGL/ZSTQ/result/${id}`);
          if (!resultRes.ok) {
            throw new Error('获取结果失败');
          }
          const resultData = await resultRes.json();
          handleUploadSuccess(resultData, id);
        }
      } catch (err) {
        console.error(err);
      }
    }, 2000);
    setPollingInterval(interval);
  };

  const uploadProps: UploadProps = {
    name: 'file',
    multiple: false,
    accept: '.pdf,.docx,.doc',
    fileList,
    beforeUpload: (file) => {
      const ext = file.name.toLowerCase();
      const isPDF = ext.endsWith('.pdf');
      const isWord = ext.endsWith('.docx') || ext.endsWith('.doc');
      if (!isPDF && !isWord) {
        message.error('只能上传 PDF、Word(.docx/.doc) 文件');
        return Upload.LIST_IGNORE;
      }
      const isLt16M = file.size / 1024 / 1024 < 16;
      if (!isLt16M) {
        message.error('文件大小不能超过 16MB');
        return Upload.LIST_IGNORE;
      }
      return true;
    },
    customRequest: async ({ file, onSuccess, onError }) => {
      const formData = new FormData();
      formData.append('file', file as Blob);

      Object.entries(extractionConfig).forEach(([key, value]) => {
        formData.append(key, String(value));
      });

      setUploading(true);
      setDocMetadata(null);
      try {
        const response = await fetch('/api/ZSGL/ZSTQ/extract', {
          method: 'POST',
          body: formData,
        });

        if (!response.ok) {
          const errorData = await response.json().catch(() => ({}));
          throw new Error(errorData.message || `请求失败 (${response.status})`);
        }

        const data = await response.json();
        startPolling(data.task_id);
        if (onSuccess) onSuccess('ok');
      } catch (error: any) {
        if (onError) onError(error);
        message.error(`上传失败: ${error.message}`);
        setFileList([]);
        setUploading(false);
      }
    },
    onChange: ({ fileList }) => {
      setFileList(fileList);
    },
    onRemove: handleReset,
  };

  const getFileIcon = (filename: string) => {
    const ext = filename.toLowerCase();
    if (ext.endsWith('.pdf')) {
      return <FilePdfOutlined style={{ color: '#ef4444', fontSize: 20 }} />;
    }
    return <FileWordOutlined style={{ color: '#3b82f6', fontSize: 20 }} />;
  };

  // ============ 图片预览 ============
  const handleImagePreview = useCallback((node: KGNode) => {
    const imagePath = node.properties?.imagePath;
    if (!imagePath) {
      message.warning('该图片节点暂无图片路径');
      return;
    }
    // 构建图片URL — 通过后端API获取图片
    const imageUrl = `/api/ZSGL/ZSTQ/images/${imagePath}.png`;
    setImagePreviewSrc(imageUrl);
    setImagePreviewVisible(true);
  }, []);

  // ============ 节点操作 ============
  const handleAddNode = () => {
    setCurrentNode(null);
    nodeForm.resetFields();
    setNodeModalVisible(true);
  };

  const handleEditNode = (node: KGNode) => {
    setCurrentNode(node);
    nodeForm.setFieldsValue({
      id: node.id,
      labels: node.labels,
      properties: node.properties,
    });
    setNodeModalVisible(true);
  };

  const handleDeleteNode = (nodeId: string) => {
    Modal.confirm({
      title: '确认删除',
      content: '删除节点后，所有相关的关系也会被删除。确定吗？',
      onOk: () => {
        setEditedNodes((prev) => prev.filter((n) => n.id !== nodeId));
        setEditedRelationships((prev) =>
          prev.filter((r) => r.startNodeId !== nodeId && r.endNodeId !== nodeId)
        );
        message.success('节点已删除');
      },
    });
  };

  const handleNodeModalOk = () => {
    nodeForm.validateFields().then((values) => {
      const { id, labels, properties } = values;
      const newNode: KGNode = {
        id: id || `temp_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`,
        labels,
        properties: properties || {},
      };
      if (currentNode) {
        setEditedNodes((prev) => prev.map((n) => (n.id === currentNode.id ? newNode : n)));
        message.success('节点已更新');
      } else {
        setEditedNodes((prev) => [...prev, newNode]);
        message.success('节点已添加');
      }
      setNodeModalVisible(false);
      nodeForm.resetFields();
    });
  };

  // ============ 表格编辑 ============
  const handleOpenTableEdit = (node: KGNode) => {
    setCurrentTableNode(node);
    setEditingTableData(JSON.parse(JSON.stringify(node.properties.tableData)));
    setTableModalVisible(true);
  };
  
  const handleTableModalCancel = () => {
    setTableModalVisible(false);
    setCurrentTableNode(null);
    setEditingTableData(null);
  };
  
  const handleTableModalOk = () => {
    if (currentTableNode && editingTableData) {
      const updatedNodes = editedNodes.map((n) => {
        if (n.id === currentTableNode.id) {
          return {
            ...n,
            properties: {
              ...n.properties,
              tableData: editingTableData,
            },
          };
        }
        return n;
      });
      setEditedNodes(updatedNodes);
      message.success('表格已更新');
    }
    handleTableModalCancel();
  };
  
  const handleCellChange = (rowIdx: number, colIdx: number, value: string) => {
    if (!editingTableData) return;
    const newRows = [...editingTableData.rows];
    newRows[rowIdx][colIdx] = value;
    setEditingTableData({ ...editingTableData, rows: newRows });
  };

  // ============ 节点表格列定义 ============
  const nodeColumns: ColumnsType<KGNode> = useMemo(() => {
    const uniqueLabels = Array.from(
      new Set(editedNodes.flatMap((node) => node.labels))
    ).map((label) => ({ text: label, value: label }));
  
    return [
      { 
        title: 'ID', 
        dataIndex: ['properties', 'number'], 
        key: 'id', 
        width: 80,
        ellipsis: true,
        render: (val: string, record: KGNode) => record.id.substring(0, 20) + (record.id.length > 20 ? '...' : '')
      },
      { title: '命名', dataIndex: ['properties', 'name'], key: 'name', width: 150, ellipsis: true },
      {
        title: '标签',
        dataIndex: 'labels',
        key: 'labels',
        width: 120,
        filters: uniqueLabels,
        onFilter: (value, record) => record.labels.includes(value as string),
        render: (labels: string[]) => labels.map((l) => <Tag key={l} color={getLabelColor(l)}>{l}</Tag>),
      },
      {
        title: '描述',
        dataIndex: ['properties', 'definition'],
        key: 'description',
        ellipsis: true,
        render: (value: any) => {
          if (value) {
            return <Text ellipsis={{ tooltip: value }}>{String(value).substring(0, 100)}</Text>;
          }
          return <Text type="secondary">-</Text>;
        },
      },
      {
        title: '预览',
        key: 'preview',
        width: 100,
        render: (_, node) => {
          if (node.labels.includes('Figure') && node.properties.imagePath) {
            return (
              <Button icon={<PictureOutlined />} size="small" type="link" onClick={() => handleImagePreview(node)}>
                查看图片
              </Button>
            );
          }
          if (node.labels.includes('Table') && node.properties.tableData) {
            return (
              <Button icon={<TableOutlined />} size="small" type="link" onClick={() => handleOpenTableEdit(node)}>
                查看表格
              </Button>
            );
          }
          return <Text type="secondary">-</Text>;
        },
      },
      {
        title: '操作',
        key: 'action',
        width: 100,
        render: (_, node) => (
          <Space size="small">
            <Button icon={<EditOutlined />} size="small" type="link" onClick={() => handleEditNode(node)}>
              编辑
            </Button>
            <Popconfirm title="确定删除？" onConfirm={() => handleDeleteNode(node.id)}>
              <Button icon={<DeleteOutlined />} size="small" type="link" danger>
                删除
              </Button>
            </Popconfirm>
          </Space>
        ),
      },
    ];
  }, [editedNodes, currentTableNode, editingTableData]);

  const getLabelColor = (label: string) => {
    const colorMap: Record<string, string> = {
      Standard: 'blue',
      Clause: 'green',
      Term: 'orange',
      Organization: 'purple',
      Person: 'cyan',
      TestMethod: 'magenta',
      Condition: 'red',
      Device: 'volcano',
      Table: 'geekblue',
      Figure: 'gold',
    };
    return colorMap[label] || 'default';
  };

  // ============ 关系操作 ============
  const handleAddRel = () => {
    setCurrentRel(null);
    relForm.resetFields();
    setRelModalVisible(true);
  };

  const relationTypeMap: Record<string, string> = {
    HAS_PART: '包含',
    DEFINES: '定义',
    PUBLISHED_BY: '发布',
    DRAFTED_BY: '起草',
    CITES: '引用',
    USES_METHOD: '使用方法',
    UNDER_CONDITION: '条件',
    REQUIRES_DEVICE: '需要设备',
    REQUIRES_MATERIAL: '需要材料',
    CONTAINS_TABLE: '包含表格',
    CONTAINS_FIGURE: '包含图片',
    REFERENCED_BY: '被引用',
    RELATED_TO: '关联',
  };

  const handleEditRel = (rel: KGRelationship) => {
    setCurrentRel(rel);
    relForm.setFieldsValue({
      id: rel.id,
      type: rel.type,
      startNodeId: rel.startNodeId,
      endNodeId: rel.endNodeId,
      properties: JSON.stringify(rel.properties, null, 2),
    });
    setRelModalVisible(true);
  };

  const handleDeleteRel = (relId: string) => {
    setEditedRelationships((prev) => prev.filter((r) => r.id !== relId));
    message.success('关系已删除');
  };

  const handleRelModalOk = () => {
    relForm.validateFields().then((values) => {
      const { id, type, startNodeId, endNodeId, properties } = values;
      let propsObj = {};
      try {
        propsObj = JSON.parse(properties || '{}');
      } catch {
        propsObj = {};
      }
      const newRel: KGRelationship = {
        id: id || `rel_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`,
        type,
        startNodeId,
        endNodeId,
        properties: propsObj,
      };
      if (currentRel) {
        setEditedRelationships((prev) => prev.map((r) => (r.id === currentRel.id ? newRel : r)));
        message.success('关系已更新');
      } else {
        setEditedRelationships((prev) => [...prev, newRel]);
        message.success('关系已添加');
      }
      setRelModalVisible(false);
      relForm.resetFields();
    });
  };

  const relColumns: ColumnsType<KGRelationship> = [
    { 
      title: '编号', 
      dataIndex: 'id', 
      key: 'id', 
      width: 120,
      ellipsis: true
    },
    {
      title: '类型',
      dataIndex: 'type',
      key: 'type',
      width: 120,
      render: (type: string) => (
        <Tag color="blue">{relationTypeMap[type] || type}</Tag>
      ),
    },
    { 
      title: '起始节点', 
      dataIndex: 'startNodeId', 
      key: 'startNodeId', 
      ellipsis: true,
      render: (id: string) => {
        const node = editedNodes.find(n => n.id === id);
        return <Tooltip title={id}>{node?.properties?.name || id.substring(0, 25)}</Tooltip>;
      }
    },
    { 
      title: '目标节点', 
      dataIndex: 'endNodeId', 
      key: 'endNodeId', 
      ellipsis: true,
      render: (id: string) => {
        const node = editedNodes.find(n => n.id === id);
        return <Tooltip title={id}>{node?.properties?.name || id.substring(0, 25)}</Tooltip>;
      }
    },
    {
      title: '操作',
      key: 'action',
      width: 100,
      render: (_, rel) => (
        <Space size="small">
          <Button icon={<EditOutlined />} size="small" type="link" onClick={() => handleEditRel(rel)}>
            编辑
          </Button>
          <Popconfirm title="确定删除？" onConfirm={() => handleDeleteRel(rel.id)}>
            <Button icon={<DeleteOutlined />} size="small" type="link" danger>
              删除
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  const isDataModified = () => {
    if (!originalExtraction) return false;
    return (
      JSON.stringify(editedNodes) !== JSON.stringify(originalExtraction.nodes) ||
      JSON.stringify(editedRelationships) !== JSON.stringify(originalExtraction.relationships)
    );
  };

  const handleSave = async () => {
    if (!sessionId && !isDataModified()) {
      message.error('无有效数据');
      return;
    }

    setSaving(true);
    try {
      let url = '/api/ZSGL/ZSTQ/confirm';
      let body = JSON.stringify({ session_id: sessionId });

      if (isDataModified()) {
        url = '/api/ZSGL/ZSTQ/save';
        body = JSON.stringify({ nodes: editedNodes, relationships: editedRelationships });
      }

      const response = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body,
      });

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        throw new Error(errorData.message || `保存失败 (${response.status})`);
      }

      message.success('知识图谱导入成功！');
      handleReset();
    } catch (error: any) {
      message.error(`保存失败: ${error.message}`);
    } finally {
      setSaving(false);
    }
  };

  // ============ 渲染函数 ============
  const renderProgress = () => {
    if (!progress) return null;
    const textPct = progress.text_progress ?? 0;
    const tablePct = progress.text_progress ?? 0;
    const figurePct = progress.figure_progress ?? 0;
    const isTextExtraction = progress.stage === 'text_extraction';
    const isKnowledgeExtraction = progress.stage === 'knowledge_extraction';
    const isChunking = progress.stage === 'chunking';
    const isMerging = progress.stage === 'merging';
    const isCompleted = progress.stage === 'completed';
    const isUpload = progress.stage === 'upload';

    const stepStatus = (stage: string) => {
      if (isCompleted) return 'finish';
      if (stage === progress.stage) return 'process';
      const order = ['upload', 'text_extraction', 'chunking', 'knowledge_extraction', 'merging'];
      const currentIdx = order.indexOf(progress.stage);
      const stageIdx = order.indexOf(stage);
      return stageIdx < currentIdx ? 'finish' : 'wait';
    };

    return (
      <Card style={{ marginTop: 24, borderRadius: 12 }}>
        <div style={{ padding: '24px' }}>
          <Steps
            current={
              ['upload', 'text_extraction', 'chunking', 'knowledge_extraction', 'merging', 'completed']
                .indexOf(progress.stage)
            }
            items={[
              { title: '文件上传', status: stepStatus('upload') },
              { title: '文本提取', status: stepStatus('text_extraction') },
              { title: '文本分块', status: stepStatus('chunking') },
              { title: '知识提取', status: stepStatus('knowledge_extraction') },
              { title: '合并保存', status: stepStatus('merging') },
            ]}
            style={{ marginBottom: 32 }}
          />

          <Space orientation="vertical" style={{ width: '100%' }} size="large">
            {isTextExtraction ? (
              <>
                <div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6 }}>
                    <Text strong><FileTextOutlined style={{ marginRight: 6, color: '#1890ff' }} />文字提取</Text>
                    <Text type="secondary">{textPct}%</Text>
                  </div>
                  <Progress
                    percent={textPct}
                    status={textPct >= 100 ? 'success' : 'active'}
                    strokeColor={{ '0%': '#1890ff', '100%': '#36cfc9' }}
                    size={10}
                  />
                </div>
                {tablePct > 0 && (
                  <div>
                    <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6 }}>
                      <Text strong><TableOutlined style={{ marginRight: 6, color: '#52c41a' }} />表格提取</Text>
                      <Text type="secondary">{tablePct}%</Text>
                    </div>
                    <Progress
                      percent={tablePct}
                      status={tablePct >= 100 ? 'success' : 'active'}
                      strokeColor={{ '0%': '#52c41a', '100%': '#95de64' }}
                      size={10}
                    />
                  </div>
                )}
                {figurePct > 0 && (
                  <div>
                    <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6 }}>
                      <Text strong><PictureOutlined style={{ marginRight: 6, color: '#722ed1' }} />图片提取</Text>
                      <Text type="secondary">{figurePct}%</Text>
                    </div>
                    <Progress
                      percent={figurePct}
                      status={figurePct >= 100 ? 'success' : 'active'}
                      strokeColor={{ '0%': '#722ed1', '100%': '#b37feb' }}
                      size={10}
                    />
                  </div>
                )}
              </>
            ) : isKnowledgeExtraction ? (
              <div>
                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6 }}>
                  <Text strong><NodeIndexOutlined style={{ marginRight: 6, color: '#fa8c16' }} />知识提取 (LLM)</Text>
                  <Text type="secondary">{progress.current}/{progress.total}</Text>
                </div>
                <Progress
                  percent={progress.percent}
                  status="active"
                  strokeColor={{ '0%': '#fa8c16', '100%': '#ffc069' }}
                  size={10}
                />
              </div>
            ) : (
              <Progress percent={progress.percent} status="active" size={10} />
            )}
          </Space>
        </div>
      </Card>
    );
  };

  const renderStatistics = () => {
    if (!extractionResult) return null;
    
    const nodeTypeCount: Record<string, number> = {};
    const relTypeCount: Record<string, number> = {};
    
    editedNodes.forEach(n => {
      n.labels.forEach(l => {
        nodeTypeCount[l] = (nodeTypeCount[l] || 0) + 1;
      });
    });
    
    editedRelationships.forEach(r => {
      relTypeCount[r.type] = (relTypeCount[r.type] || 0) + 1;
    });

    return (
      <Card size="small" style={{ marginBottom: 16 }}>
        <Row gutter={16}>
          <Col span={6}>
            <Statistic title="文档名称" value={extractionResult.metadata?.pdf_name || '-'} />
          </Col>
          <Col span={6}>
            <Statistic title="节点数量" value={editedNodes.length} prefix={<NodeIndexOutlined />} />
          </Col>
          <Col span={6}>
            <Statistic title="关系数量" value={editedRelationships.length} prefix={<BranchesOutlined />} />
          </Col>
          <Col span={6}>
            <Statistic 
              title="数据状态" 
              value={isDataModified() ? '已修改' : '未修改'} 
              valueStyle={{ color: isDataModified() ? '#faad14' : '#52c41a' }}
            />
          </Col>
        </Row>
        {extractionResult.metadata?.watermarks && extractionResult.metadata.watermarks.length > 0 && (
          <Alert 
            message={`检测到 ${extractionResult.metadata.watermarks.length} 处水印内容，已自动过滤`}
            type="info" 
            showIcon 
            style={{ marginTop: 12 }}
          />
        )}
      </Card>
    );
  };

  const renderEditor = () => {
    if (!extractionResult) return null;
    return (
      <Card
        title={
          <Space>
            <BranchesOutlined />
            <span>编辑知识图谱数据</span>
            <Badge status={isDataModified() ? 'processing' : 'success'} text={isDataModified() ? '已修改' : '已保存'} />
          </Space>
        }
        extra={
          <Space>
            <Button onClick={handleReset} icon={<CloseCircleOutlined />}>
              重新上传
            </Button>
            <Button type="primary" onClick={handleSave} loading={saving} icon={<SaveOutlined />}>
              确认导入
            </Button>
          </Space>
        }
      >
        {renderStatistics()}
        
        <Tabs 
          activeKey={activeTab} 
          onChange={setActiveTab}
          items={[
            {
              key: 'nodes',
              label: (
                <span>
                  <NodeIndexOutlined /> 节点管理 ({editedNodes.length})
                </span>
              ),
              children: (
                <Card 
                  size="small"
                  extra={
                    <Button icon={<PlusOutlined />} type="primary" onClick={handleAddNode}>
                      添加节点
                    </Button>
                  }
                >
                  <Table
                    columns={nodeColumns}
                    dataSource={editedNodes}
                    rowKey="id"
                    pagination={{ pageSize: 10, showTotal: (total) => `共 ${total} 条` }}
                    scroll={{ x: 'max-content' }}
                    size="small"
                  />
                </Card>
              ),
            },
            {
              key: 'relationships',
              label: (
                <span>
                  <BranchesOutlined /> 关系管理 ({editedRelationships.length})
                </span>
              ),
              children: (
                <Card 
                  size="small"
                  extra={
                    <Button icon={<PlusOutlined />} type="primary" onClick={handleAddRel}>
                      添加关系
                    </Button>
                  }
                >
                  <Table
                    columns={relColumns}
                    dataSource={editedRelationships}
                    rowKey="id"
                    pagination={{ pageSize: 10, showTotal: (total) => `共 ${total} 条` }}
                    scroll={{ x: 'max-content' }}
                    size="small"
                  />
                </Card>
              ),
            },
            {
              key: 'metadata',
              label: (
                <span>
                  <InfoCircleOutlined /> 解析详情
                </span>
              ),
              children: (
                <Card size="small">
                  <Collapse defaultActiveKey={['basic', 'llm']}>
                    <Panel header="基本信息" key="basic">
                      <Descriptions column={2} size="small">
                        <Descriptions.Item label="文档名称">{extractionResult.metadata?.pdf_name}</Descriptions.Item>
                        <Descriptions.Item label="处理时间">{extractionResult.metadata?.processed_at}</Descriptions.Item>
                        {extractionResult.metadata?.paragraphs_count && (
                          <Descriptions.Item label="段落数量">{extractionResult.metadata.paragraphs_count}</Descriptions.Item>
                        )}
                        {extractionResult.metadata?.table_count && (
                          <Descriptions.Item label="表格数量">{extractionResult.metadata.table_count}</Descriptions.Item>
                        )}
                        {extractionResult.metadata?.image_count && (
                          <Descriptions.Item label="图片数量">{extractionResult.metadata.image_count}</Descriptions.Item>
                        )}
                        {extractionResult.metadata?.watermarks && extractionResult.metadata.watermarks.length > 0 && (
                          <Descriptions.Item label="检测水印">
                            <Tag color="orange">{extractionResult.metadata.watermarks.length} 处</Tag>
                          </Descriptions.Item>
                        )}
                      </Descriptions>
                    </Panel>
                    <Panel header="LLM配置" key="llm">
                      <Descriptions column={1} size="small">
                        <Descriptions.Item label="模型">
                          {extractionResult.metadata?.llm_config?.model || '-'}
                        </Descriptions.Item>
                        <Descriptions.Item label="API地址">
                          {extractionResult.metadata?.llm_config?.base_url || '-'}
                        </Descriptions.Item>
                      </Descriptions>
                    </Panel>
                  </Collapse>
                </Card>
              ),
            },
          ]}
        />
      </Card>
    );
  };

  return (
    <div style={{ padding: 24, background: '#f5f5f5', minHeight: '100vh' }}>
      <Card style={{ borderRadius: 12, boxShadow: '0 2px 8px rgba(0,0,0,0.06)' }}>
        <div style={{ display: 'flex', alignItems: 'center', marginBottom: 24 }}>
          <Button 
            icon={<ArrowLeftOutlined />} 
            onClick={() => router.push('/ZSGL')}
            style={{ marginRight: 16 }}
          >
            返回
          </Button>
          <div>
            <Title level={3} style={{ margin: 0, color: '#235cff' }}>
              <FileTextOutlined style={{ marginRight: 8 }} />
              知识提取
            </Title>
            <Text type="secondary" style={{ display: 'block', marginTop: 4 }}>
              上传 PDF 或 Word 文档，自动提取知识图谱数据，支持手动编辑和确认导入
            </Text>
          </div>
        </div>

        {!taskId && !extractionResult && (
          <Card style={{ borderRadius: 12 }}>
            <div style={{
              border: '2px dashed #d9d9d9',
              borderRadius: 12,
              padding: 60,
              textAlign: 'center',
              background: '#fafafa',
              transition: 'all 0.3s',
            }}>
              <div style={{
                fontSize: 64,
                color: '#1890ff',
                marginBottom: 24,
              }}>
                <CloudUploadOutlined />
              </div>
              <Title level={4} style={{ marginBottom: 8 }}>
                点击或拖拽文件到此区域上传
              </Title>
              <Text type="secondary" style={{ display: 'block', marginBottom: 16 }}>
                支持 PDF、Word(.docx/.doc) 格式，文件大小不超过 16MB
              </Text>
              <Space style={{ marginBottom: 24 }}>
                <Tag icon={<FilePdfOutlined />} color="red">PDF</Tag>
                <Tag icon={<FileWordOutlined />} color="blue">Word</Tag>
                <Button 
                  size="small" 
                  icon={<SettingOutlined />} 
                  onClick={(e) => { e.stopPropagation(); setConfigModalVisible(true); }}
                >
                  参数设置
                </Button>
              </Space>
              <Upload.Dragger
                {...uploadProps}
                disabled={uploading}
                showUploadList={false}
                style={{ display: 'inline-block' }}
              >
                <Button type="primary" size="large" icon={<CloudUploadOutlined />}>
                  选择文件上传
                </Button>
              </Upload.Dragger>
              <div style={{ marginTop: 16 }}>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  上传后将自动进行 OCR 识别（扫描件）、水印过滤和知识图谱构建
                </Text>
              </div>
            </div>
          </Card>
        )}

        {uploading && renderProgress()}

        {!uploading && extractionResult && (
          <>
            {docMetadata && (
              <Card
                title={<Space><InfoCircleOutlined style={{ color: '#1890ff' }} /><span>文档元数据</span></Space>}
                style={{ borderRadius: 12, marginBottom: 16 }}
                extra={
                  <Space>
                    {extractingMeta && <Spin size="small" />}
                    <Button size="small" onClick={() => setDocMetadata(null)}>收起</Button>
                  </Space>
                }
              >
                <Row gutter={[16, 12]}>
                  <Col span={8}>
                    <Text type="secondary" style={{ fontSize: 12 }}>标准号</Text>
                    <Input value={docMetadata.standard_number} onChange={e => setDocMetadata({ ...docMetadata, standard_number: e.target.value })} size="small" />
                  </Col>
                  <Col span={16}>
                    <Text type="secondary" style={{ fontSize: 12 }}>中文名称</Text>
                    <Input value={docMetadata.chinese_name} onChange={e => setDocMetadata({ ...docMetadata, chinese_name: e.target.value })} size="small" />
                  </Col>
                  <Col span={24}>
                    <Text type="secondary" style={{ fontSize: 12 }}>英文名称</Text>
                    <Input value={docMetadata.english_name} onChange={e => setDocMetadata({ ...docMetadata, english_name: e.target.value })} size="small" />
                  </Col>
                  <Col span={8}>
                    <Text type="secondary" style={{ fontSize: 12 }}>发布日期</Text>
                    <Input value={docMetadata.publish_date} onChange={e => setDocMetadata({ ...docMetadata, publish_date: e.target.value })} size="small" />
                  </Col>
                  <Col span={8}>
                    <Text type="secondary" style={{ fontSize: 12 }}>实施日期</Text>
                    <Input value={docMetadata.implement_date} onChange={e => setDocMetadata({ ...docMetadata, implement_date: e.target.value })} size="small" />
                  </Col>
                  <Col span={8}>
                    <Text type="secondary" style={{ fontSize: 12 }}>发布部门</Text>
                    <Input value={docMetadata.publish_department} onChange={e => setDocMetadata({ ...docMetadata, publish_department: e.target.value })} size="small" />
                  </Col>
                  <Col span={12}>
                    <Text type="secondary" style={{ fontSize: 12 }}>代替标准</Text>
                    <Input value={docMetadata.replaced_standard} onChange={e => setDocMetadata({ ...docMetadata, replaced_standard: e.target.value })} size="small" />
                  </Col>
                  <Col span={12}>
                    <Text type="secondary" style={{ fontSize: 12 }}>归口单位</Text>
                    <Input value={docMetadata.filing_department} onChange={e => setDocMetadata({ ...docMetadata, filing_department: e.target.value })} size="small" />
                  </Col>
                </Row>
              </Card>
            )}
            {renderEditor()}
          </>
        )}

        {/* 节点编辑弹窗 */}
        <Modal
          title={currentNode ? '编辑节点' : '新增节点'}
          open={nodeModalVisible}
          onOk={handleNodeModalOk}
          onCancel={() => setNodeModalVisible(false)}
          afterClose={() => {
            nodeForm.resetFields();
            setCurrentNode(null);
          }}
          width={600}
          okText="确定"
          cancelText="取消"
        >
          <Form form={nodeForm} layout="vertical" style={{ marginTop: 16 }}>
            <Form.Item label="节点ID" name="id" rules={[{ required: true }]}>
              <Input placeholder="唯一标识，如 term_xxx" disabled={!!currentNode} />
            </Form.Item>
            <Form.Item label="编号" name={['properties', 'number']}>
              <Input placeholder="如 GB/T 12345-2010、4.1、表1、图2等" />
            </Form.Item>
            <Form.Item label="命名" name={['properties', 'name']} rules={[{ required: true }]}>
              <Input placeholder="节点名称" />
            </Form.Item>
            <Form.Item label="标签" name="labels" rules={[{ required: true }]}>
              <Select mode="multiple" placeholder="选择标签">
                <Select.Option value="Standard">Standard - 标准</Select.Option>
                <Select.Option value="Clause">Clause - 条款</Select.Option>
                <Select.Option value="Term">Term - 术语</Select.Option>
                <Select.Option value="Organization">Organization - 机构</Select.Option>
                <Select.Option value="Person">Person - 人员</Select.Option>
                <Select.Option value="TestMethod">TestMethod - 试验方法</Select.Option>
                <Select.Option value="Condition">Condition - 条件</Select.Option>
                <Select.Option value="Device">Device - 设备</Select.Option>
                <Select.Option value="Table">Table - 表格</Select.Option>
                <Select.Option value="Figure">Figure - 图片</Select.Option>
              </Select>
            </Form.Item>
            <Form.Item label="定义" name={['properties', 'definition']}>
              <Input.TextArea rows={4} placeholder="定义或描述" />
            </Form.Item>
          </Form>
        </Modal>

        {/* 关系编辑弹窗 */}
        <Modal
          title={currentRel ? '编辑关系' : '新增关系'}
          open={relModalVisible}
          onOk={handleRelModalOk}
          onCancel={() => setRelModalVisible(false)}
          width={600}
          okText="确定"
          cancelText="取消"
        >
          <Form form={relForm} layout="vertical" style={{ marginTop: 16 }}>
            <Form.Item label="关系ID" name="id" rules={[{ required: true }]}>
              <Input placeholder="唯一标识，如 rel_1" disabled={!!currentRel} />
            </Form.Item>
            <Form.Item label="关系类型" name="type" rules={[{ required: true }]}>
              <Select placeholder="选择关系类型">
                {Object.keys(relationTypeMap).map(key => (
                  <Select.Option key={key} value={key}>{relationTypeMap[key]} ({key})</Select.Option>
                ))}
              </Select>
            </Form.Item>
            <Form.Item label="起始节点ID" name="startNodeId" rules={[{ required: true }]}>
              <Select showSearch placeholder="选择起始节点" allowClear>
                {editedNodes.map((node) => (
                  <Select.Option key={node.id} value={node.id}>
                    {node.properties?.name || node.id}
                  </Select.Option>
                ))}
              </Select>
            </Form.Item>
            <Form.Item label="目标节点ID" name="endNodeId" rules={[{ required: true }]}>
              <Select showSearch placeholder="选择目标节点" allowClear>
                {editedNodes.map((node) => (
                  <Select.Option key={node.id} value={node.id}>
                    {node.properties?.name || node.id}
                  </Select.Option>
                ))}
              </Select>
            </Form.Item>
            <Form.Item label="属性 (JSON)" name="properties">
              <Input.TextArea rows={3} placeholder='{"key": "value"}' />
            </Form.Item>
          </Form>
        </Modal>

        {/* 图片预览弹窗 */}
        <Modal
          title="图片预览"
          open={imagePreviewVisible}
          onCancel={() => { setImagePreviewVisible(false); setImagePreviewSrc(null); }}
          footer={null}
          width={800}
        >
          {imagePreviewSrc && (
            <Image
              src={imagePreviewSrc}
              style={{ width: '100%' }}
              fallback="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
            />
          )}
        </Modal>

        {/* 表格编辑弹窗 */}
        <Modal
          title={`编辑表格 - ${currentTableNode?.properties?.name || currentTableNode?.properties?.number || ''}`}
          open={tableModalVisible}
          onOk={handleTableModalOk}
          onCancel={handleTableModalCancel}
          width={900}
          okText="确定"
          cancelText="取消"
        >
          {editingTableData && (
            <div style={{ overflow: 'auto', maxHeight: 500 }}>
              <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                <thead>
                  <tr>
                    {editingTableData.headers?.map((header: string, idx: number) => (
                      <th key={idx} style={{ border: '1px solid #d9d9d9', padding: '8px', background: '#fafafa', minWidth: 100 }}>
                        <Input
                          value={header}
                          bordered={false}
                          style={{ width: '100%', fontWeight: 'bold' }}
                        />
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {editingTableData.rows?.map((row: any[], rowIdx: number) => (
                    <tr key={rowIdx}>
                      {row.map((cell: any, colIdx: number) => (
                        <td key={colIdx} style={{ border: '1px solid #d9d9d9', padding: '4px' }}>
                          <Input
                            value={cell}
                            onChange={(e) => handleCellChange(rowIdx, colIdx, e.target.value)}
                            bordered={false}
                            style={{ width: '100%' }}
                          />
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Modal>

        <Modal
          title="提取参数设置"
          open={configModalVisible}
          onCancel={() => setConfigModalVisible(false)}
          footer={[
            <Button key="cancel" onClick={() => setConfigModalVisible(false)}>
              取消
            </Button>,
            <Button key="reset" onClick={fetchConfigSchema}>
              恢复默认
            </Button>,
            <Button key="ok" type="primary" onClick={() => setConfigModalVisible(false)}>
              确定
            </Button>,
          ]}
          width={600}
        >
          <div style={{ maxHeight: 500, overflow: 'auto' }}>
            <Row gutter={[16, 16]}>
              {Object.entries(configSchema).map(([key, item]) => (
                <Col span={12} key={key}>
                  {item.type === 'switch' && (
                    <Form.Item label={item.label} tooltip={item.description}>
                      <Switch
                        checked={Boolean(extractionConfig[key as keyof ExtractionConfig])}
                        onChange={(checked) => handleConfigChange(key, checked)}
                      />
                    </Form.Item>
                  )}
                  {item.type === 'number' && (
                    <Form.Item label={item.label} tooltip={item.description}>
                      <InputNumber
                        value={Number(extractionConfig[key as keyof ExtractionConfig])}
                        onChange={(value) => handleConfigChange(key, value ?? 0)}
                        min={item.min}
                        max={item.max}
                        step={item.step}
                        style={{ width: '100%' }}
                      />
                    </Form.Item>
                  )}
                  {item.type === 'slider' && (
                    <Form.Item label={`${item.label} (${Number(extractionConfig[key as keyof ExtractionConfig]).toFixed(2)})`} tooltip={item.description}>
                      <Slider
                        value={Number(extractionConfig[key as keyof ExtractionConfig])}
                        onChange={(value) => handleConfigChange(key, value)}
                        min={item.min}
                        max={item.max}
                        step={item.step}
                        marks={item.min !== undefined && item.max !== undefined ? {
                          [item.min]: String(item.min),
                          [item.max]: String(item.max)
                        } : undefined}
                      />
                    </Form.Item>
                  )}
                  {item.type === 'select' && (
                    <Form.Item label={item.label} tooltip={item.description}>
                      <Select
                        value={String(extractionConfig[key as keyof ExtractionConfig])}
                        onChange={(value) => handleConfigChange(key, value)}
                        style={{ width: '100%' }}
                      >
                        {item.options?.map(opt => (
                          <Select.Option key={opt} value={opt}>{opt}</Select.Option>
                        ))}
                      </Select>
                    </Form.Item>
                  )}
                </Col>
              ))}
            </Row>
          </div>
        </Modal>
      </Card>
    </div>
  );
}

export default function KnowledgeExtractionPage() {
  return (
    <Suspense fallback={<div style={{ padding: 50, textAlign: 'center' }}><Spin size="large" /></div>}>
      <KnowledgeExtractionPageContent />
    </Suspense>
  );
}
