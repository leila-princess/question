// components/KnowledgeExtraction.tsx
import React, { useState, useEffect, useMemo } from 'react';
import {
  Upload,
  Button,
  Card,
  Row,
  Col,
  Spin,
  message,
  Divider,
  Space,
  Typography,
  Table,
  Modal,
  Form,
  Input,
  Select,
  Tag,
  Popconfirm,
  Progress,
  Image,
} from 'antd';
import {
  CloudUploadOutlined,
  CheckCircleOutlined,
  RedoOutlined,
  EditOutlined,
  DeleteOutlined,
  PlusOutlined,
} from '@ant-design/icons';
import type { UploadFile, UploadProps } from 'antd/es/upload/interface';
import type { ColumnsType } from 'antd/es/table';
import { headers } from 'next/headers';
import { EyeOutlined } from '@ant-design/icons';
const { Title, Text } = Typography;
const { Option } = Select;
import { getImageUrl } from '../apiService'; // 根据实际路径调整
// 节点类型定义
interface KGNode {
  id: string;
  labels: string[];
  properties: Record<string, any>;
}

// 关系类型定义
interface KGRelationship {
  id: string;
  type: string;
  startNodeId: string;
  endNodeId: string;
  properties: Record<string, any>;
}

// 提取结果类型（与后端返回的 extraction 字段匹配）
interface ExtractionResult {
  nodes: KGNode[];
  relationships: KGRelationship[];
  metadata?: {
    pdf_name: string;
    processed_at: string;
    text_extraction_summary?: {
      total_pages: number;
      ocr_pages: number;
      total_tables: number;
      total_figures: number;
    };
    [key: string]: any;
  };
}

// 进度信息类型（与后端回调匹配）
interface ProgressInfo {
  stage: string;
  stage_name: string;
  current: number;
  total: number;
  percent: number;
  message: string;
}

const KnowledgeExtraction: React.FC = () => {
  const [uploading, setUploading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [extractionResult, setExtractionResult] = useState<ExtractionResult | null>(null);
  const [originalExtraction, setOriginalExtraction] = useState<ExtractionResult | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [fileList, setFileList] = useState<UploadFile[]>([]);
  const [editMode, setEditMode] = useState<'view' | 'edit'>('view');
  const [editedNodes, setEditedNodes] = useState<KGNode[]>([]);
  const [editedRelationships, setEditedRelationships] = useState<KGRelationship[]>([]);
  // 进度相关
  const [taskId, setTaskId] = useState<string | null>(null);
  const [progress, setProgress] = useState<ProgressInfo | null>(null);
  const [pollingInterval, setPollingInterval] = useState<NodeJS.Timeout | null>(null);

  // 节点/关系编辑弹窗
  const [nodeModalVisible, setNodeModalVisible] = useState(false);
  const [relModalVisible, setRelModalVisible] = useState(false);
  const [currentNode, setCurrentNode] = useState<KGNode | null>(null);
  const [currentRel, setCurrentRel] = useState<KGRelationship | null>(null);
  const [nodeForm] = Form.useForm();
  const [relForm] = Form.useForm();

  const [tableModalVisible, setTableModalVisible] = useState(false);
  const [currentTableNode, setCurrentTableNode] = useState<KGNode | null>(null);
  const [editingTableData, setEditingTableData] = useState<any>(null);

  const mockData = {
        nodes: [
          {
            "id": "std_GB/T38924.6-2020",
            "labels": ["Standard"],
            "properties": {
              "number": "GB/T 38924.6-2020",
              "name": "GB/T 38924.6-2020",
              "definition": "民用轻小型无人机系统环境试验方法 第6部分：振动试验",
              "context": "中华人民共和国国家标准",
              "date": "2020-07-21"
            }
          },
          {
            "id": "clause_GB/T38924.6-2020_4.1",
            "labels": ["Clause"],
            "properties": {
              "number": "4.1",
              "name": "试验条件",
              "definition": "试验应在标准大气条件下进行，温度23℃±5℃，相对湿度45%～75%。"
            }
          },
          {
            "id": "table_GB/T38924.6-2020_1",
            "labels": ["Table"],
            "properties": {
              "number": "表1",
              "name": "无人机系统振动试验等级分类",
              "tableData": {
                headers:[
                    "应用类别",
                    "典型使用环境", 
                    "振动类型",
                    "加速度均方根值（g rms）",
                    "频率范围（Hz）",
                    "持续时间（h/轴）"],
                
                rows:[
                    ["城市低空物流", "平稳气流、短时湍流", "随机", 8.5, "20–2000", 3],
                    ["山区巡检", "中等湍流、阵风", "随机", 12, "10–2000", 4],
                    ["应急救援", "强湍流、突风、着陆冲击耦合", "正弦+随机", "10 g（正弦峰值）+15 g rms（随机）", "5–2000", "2（正弦）+5（随机）"],
                    ["农业植保", "低空紊流、药液晃动激励", "随机", 6, "5–1000", 2]
                ]
            
            }
            }
          },
          {
            "id": "figure_GB/T38924.6-2020_2",
            "labels": ["Figure"],
            "properties": {
              "number": "图2",
              "name": "振动试验安装示意图",
              "imagePath": "15_img2",
              "definition": "无人机固定在振动台上，三个轴向依次进行试验。"
            }
          },
          {
            "id": "person_高凤杰",
            "labels": ["Person"],
            "properties": {
              "name": "高凤杰"
            }
          },
          {
            "id": "organization_国家标准化管理委员会",
            "labels": ["Organization"],
            "properties": {
              "name": "国家标准化管理委员会"
            }
          }
        ],
        relationships: [
          {
            "id": "rel_has_part_std_to_clause_1",
            "type": "HAS_PART",
            "startNodeId": "std_GB/T38924.6-2020",
            "endNodeId": "clause_GB/T38924.6-2020_4.1",
            "properties": {}
          },
          {
            "id": "rel_contains_table_std_to_table_1",
            "type": "CONTAINS_TABLE",
            "startNodeId": "std_GB/T38924.6-2020",
            "endNodeId": "table_GB/T38924.6-2020_1",
            "properties": {}
          },
          {
            "id": "rel_contains_figure_std_to_figure_2",
            "type": "CONTAINS_FIGURE",
            "startNodeId": "std_GB/T38924.6-2020",
            "endNodeId": "figure_GB/T38924.6-2020_2",
            "properties": {}
          },
          {
            "id": "rel_published_by_std_to_org",
            "type": "PUBLISHED_BY",
            "startNodeId": "std_GB/T38924.6-2020",
            "endNodeId": "organization_国家标准化管理委员会",
            "properties": {}
          },
          {
            "id": "rel_drafted_by_std_to_person",
            "type": "DRAFTED_BY",
            "startNodeId": "std_GB/T38924.6-2020",
            "endNodeId": "person_高凤杰",
            "properties": {}
          }
        ]
  };
  // 清理轮询
  useEffect(() => {
    return () => {
      if (pollingInterval) clearInterval(pollingInterval);
    };
  }, [pollingInterval]);

  // 重置所有状态
  const handleReset = () => {
    setExtractionResult(null);
    setOriginalExtraction(null);
    setSessionId(null);
    setFileList([]);
    setEditMode('view');
    setEditedNodes([]);
    setEditedRelationships([]);
    setTaskId(null);
    setProgress(null);
    if (pollingInterval) {
      clearInterval(pollingInterval);
      setPollingInterval(null);
    }
  };

  const handleUploadSuccess = (data: any, taskId: string) => {
    // 保存原始数据和编辑数据
    setOriginalExtraction(data);
    setExtractionResult(data);
    setEditedNodes(data.nodes);
    setEditedRelationships(data.relationships);
    setSessionId(taskId);          // 保存 taskId 作为 sessionId
  
    // 关闭上传中状态，隐藏进度条
    setUploading(false);
    // 切换到编辑模式，显示编辑界面
    setEditMode('edit');
  };

  // 轮询进度
  const startPolling = (id: string) => {
    setTaskId(id);
    const interval = setInterval(async () => {
      try {
        const res = await fetch(`/api/pdf2kg/progress/${id}`);
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
          // 获取最终结果
          const resultRes = await fetch(`/api/pdf2kg/result/${id}`);
          if (!resultRes.ok) {
            throw new Error('获取结果失败');
          }
          const resultData = await resultRes.json();
          console.log('结果数据:', resultData);
          handleUploadSuccess(resultData, id);
        }
      } catch (err) {
        console.error(err);
        // 不停止轮询，继续尝试
      }
    }, 2000);
    setPollingInterval(interval);
  };

  // 上传配置
  const uploadProps: UploadProps = {
    name: 'file',
    multiple: false,
    accept: '.pdf',
    fileList,
    beforeUpload: (file) => {
      const isPDF = file.type === 'application/pdf' || file.name.endsWith('.pdf');
      if (!isPDF) {
        message.error('只能上传PDF文件');
        return Upload.LIST_IGNORE;
      }
      return true;
    },
    customRequest: async ({ file, onSuccess, onError }) => {
      const formData = new FormData();
      formData.append('file', file as Blob);

      setUploading(true);
      try {
        const response = await fetch('/api/pdf2kg/extract', {
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

  // ---------- 节点操作 ----------
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
      },
    });
  };

  const handleNodeModalOk = () => {
    nodeForm.validateFields().then((values) => {
      const { id, labels, properties  } = values;
      const newLabels = Array.isArray(labels) ? labels : labels.split(',').map((s: string) => s.trim());
      const newNode: KGNode = {
        id: id || `temp_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`,
        labels,
        properties: properties || {},
      };
      if (currentNode) {
        setEditedNodes((prev) => prev.map((n) => (n.id === currentNode.id ? newNode : n)));
        // 如果ID改变，可能需要更新关系中的引用，此处简化，暂不处理
      } else {
        setEditedNodes((prev) => [...prev, newNode]);
      }
      setNodeModalVisible(false);
      nodeForm.resetFields();
    });
  };


 


  const handleOpenTableEdit = (node: KGNode) => {
    setCurrentTableNode(node);
    setEditingTableData(JSON.parse(JSON.stringify(node.properties.tableData))); // 深拷贝
    setTableModalVisible(true);
  };
  
  const handleTableModalCancel = () => {
    setTableModalVisible(false);
    setCurrentTableNode(null);
    setEditingTableData(null);
  };
  
  const handleTableModalOk = () => {
    if (currentTableNode && editingTableData) {
      // 更新 editedNodes 中对应节点的 tableData
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
      // 如果 originalExtraction 也需要同步？但编辑模式下 original 保持原样，仅 edited 变化，所以没关系。
    }
    handleTableModalCancel();
  };
  
  const handleCellChange = (rowIdx: number, colIdx: number, value: string) => {
    if (!editingTableData) return;
    const newRows = [...editingTableData.rows];
    newRows[rowIdx][colIdx] = value;
    setEditingTableData({ ...editingTableData, rows: newRows });
  };

   // 节点表格列定义（包含图片预览和表格HTML预览）
   const nodeColumns: ColumnsType<KGNode> = useMemo(() => {
    // 从当前编辑的节点中提取所有唯一的标签
    const uniqueLabels = Array.from(
      new Set(editedNodes.flatMap((node) => node.labels))
    ).map((label) => ({ text: label, value: label }));
  
    return [
    { title: '编号', dataIndex: ['properties', 'number'], key: 'id', width: 50 },
    { title: '命名', dataIndex: ['properties', 'name'], key: 'id', width: 100 },
    {
      title: '标签',
      dataIndex: 'labels',
      key: 'labels',
      width: 100,
      filters: uniqueLabels, // 设置筛选选项
      onFilter: (value, record) => record.labels.includes(value as string), // 筛选逻辑
      render: (labels: string[]) => labels.map((l) => <Tag key={l}>{l}</Tag>),
    },
    {
      title: '描述',
      dataIndex: ['properties', 'definition'],
      key: 'description',
      width: 500,
      render: (value: any) => {
        if (value) {
          return <Text>{String(value)}</Text>;
        }
        return <Text type="secondary">无描述</Text>;
      },
    },
    {
      title: '预览',
      key: 'preview',
      width: 200,
      render: (_, node) => {
        if (node.labels.includes('Figure') && node.properties.filePath) {
          const imgUrl = getImageUrl(node.properties.filePath);
          return (
            <Image
              src={imgUrl}
              alt="预览"
              style={{ maxWidth: '100px', maxHeight: '100px', objectFit: 'cover' }}
              fallback="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAA..."
            />
          );
        }
        if (node.labels.includes('Table') && node.properties.tableData) {
          return (
            <Button icon={<EyeOutlined />} onClick={() => handleOpenTableEdit(node)}>
              查看表格
            </Button>
          );
        }
        return null;
      },
    },
    {
      title: '操作',
      key: 'action',
      width: 120,
      render: (_, node) => (
        <Space>
          <Button icon={<EditOutlined />} size="small" onClick={() => handleEditNode(node)}>
            编辑
          </Button>
          <Popconfirm title="确定删除？" onConfirm={() => handleDeleteNode(node.id)}>
            <Button icon={<DeleteOutlined />} size="small" danger>
              删除
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ]}, [editedNodes, getImageUrl, handleOpenTableEdit, handleEditNode, handleDeleteNode]);
  // ---------- 关系操作 ----------
  const handleAddRel = () => {
    setCurrentRel(null);
    relForm.resetFields();
    setRelModalVisible(true);
  };
  const relationTypeMap: Record<string, string> = {
    HAS_PART: '包含（标准→章节，章节→子条款）',
    DEFINES: '定义（标准/条款→术语）',
    PUBLISHED_BY: '发布（标准→机构）',
    DRAFTED_BY: '起草（标准→人员）',
    CITES: '引用（标准/条款→被引标准）',
    USES_METHOD: '使用（标准/条款→试验方法）',
    UNDER_CONDITION: '条件（试验方法→条件）',
    REQUIRES_DEVICE: '需要设备（试验方法→设备）',
    REQUIRES_MATERIAL: '需要材料（试验方法→材料）',
    CONTAINS_TABLE: '包含表格（标准/条款→表格）',
    CONTAINS_FIGURE: '包含图片（标准/条款→图）',
    REFERENCED_BY: '被引用（表格/图→引用它的条款）',
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
      } else {
        setEditedRelationships((prev) => [...prev, newRel]);
      }
      setRelModalVisible(false);
      relForm.resetFields();
    });
  };

  const relColumns: ColumnsType<KGRelationship> = [
    { title: '编号', dataIndex: 'id', key: 'id', width: 100 },
    {
      title: '类型',
      dataIndex: 'type',
      key: 'type',
      width: 150,
      render: (type: string) => {
        const chinese = relationTypeMap[type] || type;
        return <Text>{chinese}</Text>;
      },
    },
    { title: '起始节点ID', dataIndex: 'startNodeId', key: 'startNodeId', width: 200 },
    { title: '目标节点ID', dataIndex: 'endNodeId', key: 'endNodeId', width: 200 },
    // {
    //     title: '属性',
    //     dataIndex: 'properties',
    //     key: 'properties',
    //     width: 300,
    //     render: (value: any) => {
    //       if (value) {
    //         return <Text>{String(value)}</Text>;
    //       }
    //       return <Text type="secondary">无描述</Text>;
    //     },
    //   },
    {
      title: '操作',
      key: 'action',
      width: 120,
      render: (_, rel) => (
        <Space>
          <Button icon={<EditOutlined />} size="small" onClick={() => handleEditRel(rel)}>
            编辑
          </Button>
          <Popconfirm title="确定删除？" onConfirm={() => handleDeleteRel(rel.id)}>
            <Button icon={<DeleteOutlined />} size="small" danger>
              删除
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  // 判断数据是否被修改
  const isDataModified = () => {
    if (!originalExtraction) return false;
    return (
      JSON.stringify(editedNodes) !== JSON.stringify(originalExtraction.nodes) ||
      JSON.stringify(editedRelationships) !== JSON.stringify(originalExtraction.relationships)
    );
  };

  // 统一保存/确认
  const handleSave = async () => {
    if (!sessionId && !isDataModified()) {
      message.error('无有效数据');
      return;
    }

    setSaving(true);
    try {
      let url = '/api/pdf2kg/confirm';
      let body = JSON.stringify({ session_id: sessionId });

      if (isDataModified()) {
        url = '/api/pdf2kg/save';
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

      message.success('知识图谱导入成功');
      handleReset(); // 导入成功后重置，返回上传界面
    } catch (error: any) {
      message.error(`保存失败: ${error.message}`);
    } finally {
      setSaving(false);
    }
  };

  // 渲染进度条
  const renderProgress = () => {
    
    if (!progress) return null;
    return (
      <Card style={{ marginTop: 24 }}>
        <Title level={5}>处理进度</Title>
        <div style={{ textAlign: 'center', padding: '20px' }}>
          <Spin size="large" />
          <div style={{ marginTop: 16 }}>
            <Text strong>{progress.stage_name}</Text>
            <Progress percent={progress.percent} format={() => `${progress.current}/${progress.total}`} />
            <p>{progress.message}</p>
          </div>
        </div>
      </Card>
    );
  };

  // 渲染编辑界面
  const renderEditor = () => {
    if (!extractionResult) return null;
    return (
      <Card
        title="编辑知识图谱数据"
        extra={
          <Space>
            <Button onClick={handleReset}>重新上传</Button>
            <Button type="primary" onClick={handleSave} loading={saving}>
              确认导入
            </Button>
          </Space>
        }
      >
        <Divider orientation="horizontal">节点管理</Divider>
        <Text> 共有{editedNodes.length}个节点</Text>
        <Button icon={<PlusOutlined />} onClick={handleAddNode} style={{ marginBottom: 16 }}>
          添加节点
        </Button>
        <Table
          columns={nodeColumns}
          dataSource={editedNodes}      // ✅ 关键修改
          rowKey="id"
          pagination={{ pageSize: 5 }}
          scroll={{ x: 'max-content' }}
        />
  
        <Divider orientation="horizontal">关系管理</Divider>
        <Text> 共有{editedRelationships.length}个关系</Text>
        <Button icon={<PlusOutlined />} onClick={handleAddRel} style={{ marginBottom: 16 }}>
          添加关系
        </Button>
        <Table
          columns={relColumns}
          dataSource={editedRelationships}
          rowKey="id"
          pagination={{ pageSize: 5 }}
          scroll={{ x: 'max-content' }}
        />
      </Card>
    );
  };

  return (
    <div style={{ padding: '20px' }}>
      <Title level={4}>知识提取与构建</Title>

      {/* 上传区域（仅在无结果且无进度时显示） */}
      {!taskId && !extractionResult && (
        <Upload.Dragger {...uploadProps} disabled={uploading}>
          <p className="ant-upload-drag-icon">
            <CloudUploadOutlined style={{ fontSize: 48 }} />
          </p>
          <p className="ant-upload-text">点击或拖拽PDF文件到此区域上传</p>
          <p className="ant-upload-hint">支持单个PDF文件，将自动进行OCR和知识提取</p>
        </Upload.Dragger>
      )}

      {/* 上传中进度 */}
      {uploading && renderProgress()}

      {/* 编辑界面 */}
      {!uploading && extractionResult && renderEditor()}

      {/* 节点编辑弹窗 */}
      {/* 节点编辑弹窗 */}
<Modal
  title={currentNode ? '编辑节点' : '新增节点'}
  open={nodeModalVisible}
  onOk={handleNodeModalOk}
  onCancel={() => setNodeModalVisible(false)}
  afterClose={() => {
    nodeForm.resetFields();
    setCurrentNode(null); // 同时清空当前编辑节点
  }}
  width={600}
>
  <Form form={nodeForm} layout="vertical">
    <Form.Item label="节点ID" name="id" rules={[{ required: true }]}>
      <Input placeholder="唯一标识，如 term_xxx" disabled/>
    </Form.Item>
    <Form.Item label="编号" name={['properties', 'number']}>
      <Input placeholder="如 GB/T 12345-2010、4.1、表1、图2等" />
    </Form.Item>
    <Form.Item label="命名" name={['properties', 'name']} rules={[{ required: true }]}>
      <Input placeholder="节点名称" />
    </Form.Item>
    <Form.Item label="标签" name="labels" rules={[{ required: true }]}>
      <Select  placeholder="输入标签，如 Standard, Clause">
        <Option value="Standard">Standard</Option>
        <Option value="Clause">Clause</Option>
        <Option value="Term">Term</Option>
        <Option value="Organization">Organization</Option>
        <Option value="Person">Person</Option>
        <Option value="Table">Table</Option>
        <Option value="Figure">Figure</Option>
      </Select>
    </Form.Item>
    <Form.Item label="定义" name={['properties', 'definition']}>
      <Input.TextArea rows={3} placeholder="定义或描述" />
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
      >
        <Form form={relForm} layout="vertical">
          <Form.Item label="关系ID" name="id" rules={[{ required: true }]}>
            <Input placeholder="唯一标识，如 rel_1" />
          </Form.Item>
          <Form.Item label="关系类型" name="type" rules={[{ required: true }]}>
            <Select placeholder="选择或输入关系类型">
              {Object.keys(relationTypeMap).map(key => (
                <Option key={key} value={key}>{key} - {relationTypeMap[key]}</Option>
              ))}
            </Select>
          </Form.Item>
          <Form.Item label="起始节点ID" name="startNodeId" rules={[{ required: true }]}>
            <Select showSearch placeholder="选择起始节点">
              {editedNodes.map((node) => (
                <Option key={node.id} value={node.id}>
                  {node.id}
                </Option>
              ))}
            </Select>
          </Form.Item>
          <Form.Item label="目标节点ID" name="endNodeId" rules={[{ required: true }]}>
            <Select showSearch placeholder="选择目标节点">
              {editedNodes.map((node) => (
                <Option key={node.id} value={node.id}>
                  {node.id}
                </Option>
              ))}
            </Select>
          </Form.Item>
          {/* <Form.Item label="属性 (JSON格式)" name="properties" initialValue="{}">
            <Input.TextArea rows={4} placeholder='{"inferred": true, "source": "..."}' />
          </Form.Item> */}
        </Form>
      </Modal>

      <Modal
        title={`编辑表格 - ${currentTableNode?.properties?.name || currentTableNode?.properties?.number || ''}`}
        open={tableModalVisible}
        onOk={handleTableModalOk}
        onCancel={handleTableModalCancel}
        width={800}
        styles={{ body:{ maxHeight: '500px', overflow: 'auto' }}}
      >
        {editingTableData && (
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr>
                {editingTableData.headers.map((header: string, idx: number) => (
                  <th key={idx} style={{ border: '1px solid #ddd', padding: '8px', background: '#f5f5f5' }}>
                    {header}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {editingTableData.rows.map((row: any[], rowIdx: number) => (
                <tr key={rowIdx}>
                  {row.map((cell: any, colIdx: number) => (
                    <td key={colIdx} style={{ border: '1px solid #ddd', padding: '4px' }}>
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
        )}
      </Modal>
    </div>
  );
};

export default KnowledgeExtraction;