"use client";
import React, { useState, useEffect } from 'react';
import { 
  Card, Tabs, Table, Tag, Button, Space, Typography, Row, Col, 
  Input, Modal, message, Popconfirm, Tree, Form,
  Switch, Select, Alert
} from 'antd';
import { 
  DatabaseOutlined, BranchesOutlined, FileOutlined, CloudServerOutlined,
  ReloadOutlined, PlusOutlined, DeleteOutlined, EditOutlined,
  ExclamationCircleOutlined, SyncOutlined
} from '@ant-design/icons';
import type { DataNode } from 'antd/es/tree';
import { useTablePagination } from '@/utils/useTablePagination';

const { Title, Text } = Typography;

interface ColumnMeta {
  id: number;
  column_name: string;
  column_type: string;
  data_type: string;
  is_nullable: boolean;
  column_key: string;
  column_default: string;
  description: string;
  status: string;
}

interface PropertyMeta {
  id: number;
  property_name: string;
  property_type: string;
  display_name: string;
  description: string;
  is_required: boolean;
  is_indexed: boolean;
  is_unique: boolean;
  status: string;
}

interface ChromaMeta {
  id: number;
  field_name: string;
  field_type: string;
  display_name: string;
  description: string;
  is_required: boolean;
  default_value: string;
  status: string;
}

interface MinioMeta {
  id: number;
  tag_key: string;
  tag_type: string;
  display_name: string;
  description: string;
  is_required: boolean;
  status: string;
}

export default function SJKGLPage() {
  const [activeTab, setActiveTab] = useState('mysql');
  const [loading, setLoading] = useState(false);
  const [syncing, setSyncing] = useState(false);
  
  const [mysqlTree, setMysqlTree] = useState<DataNode[]>([]);
  const [selectedMysqlTable, setSelectedMysqlTable] = useState<{db: string, table: string} | null>(null);
  const [mysqlColumns, setMysqlColumns] = useState<ColumnMeta[]>([]);
  const [mysqlColumnModal, setMysqlColumnModal] = useState(false);
  const [editingMysqlColumn, setEditingMysqlColumn] = useState<ColumnMeta | null>(null);
  const [mysqlForm] = Form.useForm();
  
  const [neo4jTree, setNeo4jTree] = useState<DataNode[]>([]);
  const [selectedNeo4jLabel, setSelectedNeo4jLabel] = useState<{type: string, name: string} | null>(null);
  const [neo4jProperties, setNeo4jProperties] = useState<PropertyMeta[]>([]);
  const [neo4jPropertyModal, setNeo4jPropertyModal] = useState(false);
  const [editingNeo4jProperty, setEditingNeo4jProperty] = useState<PropertyMeta | null>(null);
  const [neo4jLabelModal, setNeo4jLabelModal] = useState(false);
  const [neo4jForm] = Form.useForm();
  
  const [chromaTree, setChromaTree] = useState<DataNode[]>([]);
  const [selectedChromaCollection, setSelectedChromaCollection] = useState<string | null>(null);
  const [chromaMetadata, setChromaMetadata] = useState<ChromaMeta[]>([]);
  const [chromaMetadataModal, setChromaMetadataModal] = useState(false);
  const [editingChromaMetadata, setEditingChromaMetadata] = useState<ChromaMeta | null>(null);
  const [chromaForm] = Form.useForm();
  
  const [minioTree, setMinioTree] = useState<DataNode[]>([]);
  const [selectedMinioBucket, setSelectedMinioBucket] = useState<string | null>(null);
  const [minioMetadata, setMinioMetadata] = useState<MinioMeta[]>([]);
  const [minioMetadataModal, setMinioMetadataModal] = useState(false);
  const [editingMinioMetadata, setEditingMinioMetadata] = useState<MinioMeta | null>(null);
  const [minioForm] = Form.useForm();

  const fetchMysqlTree = async () => {
    try {
      setLoading(true);
      const res = await fetch('/api/xtgl/sjkgl/db/metadata/mysql/tree');
      if (res.ok) {
        const json = await res.json();
        console.log(json.data);
          // 预处理：为每个表节点添加 db 属性
        const enrichTree = (nodes: any[], parentDb = null) => {
          return nodes.map(node => {
            const newNode = { ...node };
            if (node.isLeaf) {
              // 叶子节点（表）：添加 db 属性
              newNode.db = parentDb;
            }
            if (node.children && node.children.length) {
              newNode.children = enrichTree(node.children, node.title);
            }
            return newNode;
          });
        };

        const enrichedData = enrichTree(json.data || []);
        setMysqlTree(enrichedData);
      }
    } catch (error) {
      console.error('获取MySQL树失败', error);
    } finally {
      setLoading(false);
    }
  };

  const fetchMysqlColumns = async (db: string, table: string) => {
    try {
      setLoading(true);
      
      const res = await fetch(`/api/xtgl/sjkgl/db/metadata/mysql/columns?database=${db}&table=${table}`);
      if (res.ok) {
        const json = await res.json();
        setMysqlColumns(json.data || []);
      }
    } catch (error) {
      console.error('获取MySQL字段失败', error);
    } finally {
      setLoading(false);
    }
  };

  const handleMysqlTreeSelect = (selectedKeys: React.Key[], info: any) => {
    const node = info.node;
    if (info.node.isLeaf && info.node.key.startsWith('table_')) {
      const key = info.node.key as string;
      const parts = key.replace('table_', '').split('_');
      const table = node.title;
      const db = node.db; // 从预处理的树节点中获取数据库名
      
      setSelectedMysqlTable({ db, table: table! });
      fetchMysqlColumns(db, table!);
    }
  };

  const handleSaveMysqlColumn = async () => {
    try {
      const values = await mysqlForm.validateFields();
      const url = editingMysqlColumn 
        ? `/api/xtgl/sjkgl/db/metadata/mysql/column/${editingMysqlColumn.id}`
        : '/api/xtgl/sjkgl/db/metadata/mysql/column';
      const method = editingMysqlColumn ? 'PUT' : 'POST';
      
      const res = await fetch(url, {
        method,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ...values,
          database_name: selectedMysqlTable?.db,
          table_name: selectedMysqlTable?.table
        })
      });
      
      if (res.ok) {
        message.success(editingMysqlColumn ? '字段更新成功' : '字段创建成功');
        setMysqlColumnModal(false);
        setEditingMysqlColumn(null);
        mysqlForm.resetFields();
        if (selectedMysqlTable) {
          fetchMysqlColumns(selectedMysqlTable.db, selectedMysqlTable.table);
        }
      } else {
        const j = await res.json().catch(() => ({} as { error?: string }));
        message.error((j as { error?: string }).error || '操作失败');
      }
    } catch (error) {
      message.error(error instanceof Error ? error.message : '操作失败');
    }
  };

  const handleDeleteMysqlColumn = async (id: number) => {
    try {
      const res = await fetch(`/api/xtgl/sjkgl/db/metadata/mysql/column/${id}`, {
        method: 'DELETE'
      });
      if (res.ok) {
        message.success('字段删除成功');
        if (selectedMysqlTable) {
          fetchMysqlColumns(selectedMysqlTable.db, selectedMysqlTable.table);
        }
      } else {
        message.error('删除失败');
      }
    } catch (error) {
      message.error('删除失败');
    }
  };

  const fetchNeo4jTree = async () => {
    try {
      setLoading(true);
      const res = await fetch('/api/xtgl/sjkgl/db/metadata/neo4j/tree');
      if (res.ok) {
        const json = await res.json();
        setNeo4jTree(json.data || []);
      }
    } catch (error) {
      console.error('获取Neo4j树失败', error);
    } finally {
      setLoading(false);
    }
  };

  const fetchNeo4jProperties = async (type: string, label: string) => {
    try {
      setLoading(true);
      const res = await fetch(`/api/xtgl/sjkgl/db/metadata/neo4j/properties?type=${type}&label=${label}`);
      if (res.ok) {
        const json = await res.json();
        setNeo4jProperties(json.data || []);
      }
    } catch (error) {
      console.error('获取Neo4j属性失败', error);
    } finally {
      setLoading(false);
    }
  };

  const handleNeo4jTreeSelect = (selectedKeys: React.Key[], info: any) => {
    if (info.node.isLeaf) {
      const key = info.node.key as string;
      if (key.startsWith('node_') || key.startsWith('relation_')) {
        const type = key.startsWith('node_') ? 'node' : 'relation';
        const name = key.replace(/^(node_|relation_)/, '');
        setSelectedNeo4jLabel({ type, name });
        fetchNeo4jProperties(type, name);
      }
    }
  };

  const handleCreateNeo4jLabel = async (type: 'node' | 'relation') => {
    try {
      const values = await neo4jForm.validateFields();
      const res = await fetch('/api/xtgl/sjkgl/db/metadata/neo4j/label', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          label_type: type,
          label_name: values.label_name
        })
      });
      
      if (res.ok) {
        message.success('标签创建成功');
        setNeo4jLabelModal(false);
        neo4jForm.resetFields();
        fetchNeo4jTree();
      } else {
        const json = await res.json();
        message.error(json.error || '创建失败');
      }
    } catch (error) {
      message.error('创建失败');
    }
  };

  const handleDeleteNeo4jLabel = async (type: string, name: string) => {
    try {
      const res = await fetch(`/api/xtgl/sjkgl/db/metadata/neo4j/label/${type}/${name}`, {
        method: 'DELETE'
      });
      if (res.ok) {
        message.success('标签删除成功');
        setSelectedNeo4jLabel(null);
        setNeo4jProperties([]);
        fetchNeo4jTree();
      } else {
        message.error('删除失败');
      }
    } catch (error) {
      message.error('删除失败');
    }
  };

  const handleSaveNeo4jProperty = async () => {
    try {
      const values = await neo4jForm.validateFields();
      const url = editingNeo4jProperty 
        ? `/api/xtgl/sjkgl/db/metadata/neo4j/property/${editingNeo4jProperty.id}`
        : '/api/xtgl/sjkgl/db/metadata/neo4j/property';
      const method = editingNeo4jProperty ? 'PUT' : 'POST';
      
      const res = await fetch(url, {
        method,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ...values,
          label_type: selectedNeo4jLabel?.type,
          label_name: selectedNeo4jLabel?.name
        })
      });
      
      if (res.ok) {
        message.success(editingNeo4jProperty ? '属性更新成功' : '属性创建成功');
        setNeo4jPropertyModal(false);
        setEditingNeo4jProperty(null);
        neo4jForm.resetFields();
        if (selectedNeo4jLabel) {
          fetchNeo4jProperties(selectedNeo4jLabel.type, selectedNeo4jLabel.name);
        }
      } else {
        const j = await res.json().catch(() => ({} as { error?: string }));
        message.error((j as { error?: string }).error || '操作失败');
      }
    } catch (error) {
      message.error(error instanceof Error ? error.message : '操作失败');
    }
  };

  const handleDeleteNeo4jProperty = async (id: number) => {
    try {
      const res = await fetch(`/api/xtgl/sjkgl/db/metadata/neo4j/property/${id}`, {
        method: 'DELETE'
      });
      if (res.ok) {
        message.success('属性删除成功');
        if (selectedNeo4jLabel) {
          fetchNeo4jProperties(selectedNeo4jLabel.type, selectedNeo4jLabel.name);
        }
      } else {
        message.error('删除失败');
      }
    } catch (error) {
      message.error('删除失败');
    }
  };

  const handleDeleteChromaMetadata = async (id: number) => {
    try {
      const res = await fetch(`/api/xtgl/sjkgl/db/metadata/chroma/field/${id}`, {
        method: 'DELETE'
      });
      if (res.ok) {
        message.success('字段删除成功');
        if (selectedChromaCollection) {
          fetchChromaMetadata(selectedChromaCollection);
        }
      } else {
        message.error('删除失败');
      }
    } catch (error) {
      message.error('删除失败');
    }
  };

  const handleDeleteMinioMetadata = async (id: number) => {
    try {
      const res = await fetch(`/api/xtgl/sjkgl/db/metadata/minio/tag/${id}`, {
        method: 'DELETE'
      });
      if (res.ok) {
        message.success('标签删除成功');
        if (selectedMinioBucket) {
          fetchMinioMetadata(selectedMinioBucket);
        }
      } else {
        message.error('删除失败');
      }
    } catch (error) {
      message.error('删除失败');
    }
  };

  const fetchChromaTree = async () => {
    try {
      setLoading(true);
      const res = await fetch('/api/xtgl/sjkgl/db/metadata/chroma/tree');
      if (res.ok) {
        const json = await res.json();
        setChromaTree(json.data || []);
      }
    } catch (error) {
      console.error('获取Chroma树失败', error);
    } finally {
      setLoading(false);
    }
  };

  const fetchMinioTree = async () => {
    try {
      setLoading(true);
      const res = await fetch('/api/xtgl/sjkgl/db/metadata/minio/tree');
      if (res.ok) {
        const json = await res.json();
        setMinioTree(json.data || []);
      }
    } catch (error) {
      console.error('获取MinIO树失败', error);
    } finally {
      setLoading(false);
    }
  };

  const fetchChromaMetadata = async (collection: string) => {
    try {
      setLoading(true);
      const res = await fetch(`/api/xtgl/sjkgl/db/metadata/chroma/metadata?collection=${collection}`);
      if (res.ok) {
        const json = await res.json();
        setChromaMetadata(json.data || []);
      }
    } catch (error) {
      console.error('获取Chroma元数据失败', error);
    } finally {
      setLoading(false);
    }
  };

  const fetchMinioMetadata = async (bucket: string) => {
    try {
      setLoading(true);
      const res = await fetch(`/api/xtgl/sjkgl/db/metadata/minio/metadata?bucket=${bucket}`);
      if (res.ok) {
        const json = await res.json();
        setMinioMetadata(json.data || []);
      }
    } catch (error) {
      console.error('获取MinIO元数据失败', error);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (activeTab === 'mysql') fetchMysqlTree();
    if (activeTab === 'neo4j') fetchNeo4jTree();
    if (activeTab === 'chroma') fetchChromaTree();
    if (activeTab === 'minio') fetchMinioTree();
  }, [activeTab]);

  const refreshActiveTab = () => {
    if (activeTab === 'mysql') fetchMysqlTree();
    if (activeTab === 'neo4j') fetchNeo4jTree();
    if (activeTab === 'chroma') fetchChromaTree();
    if (activeTab === 'minio') fetchMinioTree();
  };

  const handleSyncMetadata = async () => {
    try {
      setSyncing(true);
      const res = await fetch('/api/xtgl/sjkgl/sync-metadata', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      });
      const json = await res.json();
      if (res.ok && json.success) {
        message.success(json.message || '元数据同步完成');
        refreshActiveTab();
        if (selectedMysqlTable) {
          fetchMysqlColumns(selectedMysqlTable.db, selectedMysqlTable.table);
        }
        if (selectedNeo4jLabel) {
          fetchNeo4jProperties(selectedNeo4jLabel.type, selectedNeo4jLabel.name);
        }
        if (selectedChromaCollection) {
          fetchChromaMetadata(selectedChromaCollection);
        }
        if (selectedMinioBucket) {
          fetchMinioMetadata(selectedMinioBucket);
        }
      } else {
        message.error(json.error || '元数据同步失败');
      }
    } catch (error) {
      message.error('元数据同步失败');
    } finally {
      setSyncing(false);
    }
  };

  const mysqlColumnsTable = [
    { title: '字段名', dataIndex: 'column_name', key: 'column_name', width: 150, render: (t: string) => <Text strong>{t}</Text> },
    { title: '字段类型', dataIndex: 'column_type', key: 'column_type', width: 120, render: (t: string) => <Tag color="blue">{t}</Tag> },
    { title: '数据类型', dataIndex: 'data_type', key: 'data_type', width: 100, render: (t: string) => <Tag color="cyan">{t}</Tag> },
    { title: '可空', dataIndex: 'is_nullable', key: 'is_nullable', width: 60, render: (v: boolean) => v ? '是' : '否' },
    { title: '键', dataIndex: 'column_key', key: 'column_key', width: 80, render: (v: string) => v ? <Tag color="orange">{v}</Tag> : '-' },
    { title: '默认值', dataIndex: 'column_default', key: 'column_default', width: 100, ellipsis: true },
    { title: '描述', dataIndex: 'description', key: 'description', ellipsis: true },
    { title: '状态', dataIndex: 'status', key: 'status', width: 80, render: (v: string) => <Tag color={v === '正常' ? 'green' : 'red'}>{v}</Tag> },
    {
      title: '操作',
      key: 'action',
      width: 120,
      render: (_: any, record: ColumnMeta) => (
        <Space size="small">
          <Button 
            size="small" 
            icon={<EditOutlined />}
            onClick={() => {
              setEditingMysqlColumn(record);
              mysqlForm.setFieldsValue(record);
              setMysqlColumnModal(true);
            }}
          />
          <Popconfirm
            title="确定删除此字段？"
            description="删除后不可恢复，请确认"
            onConfirm={() => handleDeleteMysqlColumn(record.id)}
            okText="确定"
            cancelText="取消"
          >
            <Button size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      )
    }
  ];

  const neo4jPropertiesTable = [
    { title: '属性名', dataIndex: 'property_name', key: 'property_name', width: 150, render: (t: string) => <Text strong>{t}</Text> },
    { title: '属性类型', dataIndex: 'property_type', key: 'property_type', width: 120, render: (t: string) => <Tag color="green">{t}</Tag> },
    { title: '显示名', dataIndex: 'display_name', key: 'display_name', width: 120 },
    { title: '必填', dataIndex: 'is_required', key: 'is_required', width: 60, render: (v: boolean) => v ? '是' : '否' },
    { title: '索引', dataIndex: 'is_indexed', key: 'is_indexed', width: 60, render: (v: boolean) => v ? '是' : '否' },
    { title: '唯一', dataIndex: 'is_unique', key: 'is_unique', width: 60, render: (v: boolean) => v ? '是' : '否' },
    { title: '描述', dataIndex: 'description', key: 'description', ellipsis: true },
    { title: '状态', dataIndex: 'status', key: 'status', width: 80, render: (v: string) => <Tag color={v === '正常' ? 'green' : 'red'}>{v}</Tag> },
    {
      title: '操作',
      key: 'action',
      width: 120,
      render: (_: any, record: PropertyMeta) => (
        <Space size="small">
          <Button 
            size="small" 
            icon={<EditOutlined />}
            onClick={() => {
              setEditingNeo4jProperty(record);
              neo4jForm.setFieldsValue(record);
              setNeo4jPropertyModal(true);
            }}
          />
          <Popconfirm
            title="确定删除此属性？"
            description="删除后不可恢复，请确认"
            onConfirm={() => handleDeleteNeo4jProperty(record.id)}
            okText="确定"
            cancelText="取消"
          >
            <Button size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      )
    }
  ];

  const chromaMetadataTable = [
    { title: '字段名', dataIndex: 'field_name', key: 'field_name', width: 150, render: (t: string) => <Text strong>{t}</Text> },
    { title: '字段类型', dataIndex: 'field_type', key: 'field_type', width: 100, render: (t: string) => <Tag color="blue">{t}</Tag> },
    { title: '显示名', dataIndex: 'display_name', key: 'display_name', width: 120 },
    { title: '必填', dataIndex: 'is_required', key: 'is_required', width: 60, render: (v: boolean) => v ? '是' : '否' },
    { title: '默认值', dataIndex: 'default_value', key: 'default_value', width: 100, ellipsis: true },
    { title: '描述', dataIndex: 'description', key: 'description', ellipsis: true },
    { title: '状态', dataIndex: 'status', key: 'status', width: 80, render: (v: string) => <Tag color={v === '正常' ? 'green' : 'red'}>{v}</Tag> },
    {
      title: '操作',
      key: 'action',
      width: 120,
      render: (_: any, record: ChromaMeta) => (
        <Space size="small">
          <Button 
            size="small" 
            icon={<EditOutlined />}
            onClick={() => {
              setEditingChromaMetadata(record);
              chromaForm.setFieldsValue(record);
              setChromaMetadataModal(true);
            }}
          />
          <Popconfirm
            title="确定删除此字段？"
            description="删除后不可恢复，请确认"
            onConfirm={() => handleDeleteChromaMetadata(record.id)}
            okText="确定"
            cancelText="取消"
          >
            <Button size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      )
    }
  ];

  const minioMetadataTable = [
    { title: '标签键', dataIndex: 'tag_key', key: 'tag_key', width: 150, render: (t: string) => <Text strong>{t}</Text> },
    { title: '标签类型', dataIndex: 'tag_type', key: 'tag_type', width: 100, render: (t: string) => <Tag color="orange">{t}</Tag> },
    { title: '显示名', dataIndex: 'display_name', key: 'display_name', width: 120 },
    { title: '必填', dataIndex: 'is_required', key: 'is_required', width: 60, render: (v: boolean) => v ? '是' : '否' },
    { title: '描述', dataIndex: 'description', key: 'description', ellipsis: true },
    { title: '状态', dataIndex: 'status', key: 'status', width: 80, render: (v: string) => <Tag color={v === '正常' ? 'green' : 'red'}>{v}</Tag> },
    {
      title: '操作',
      key: 'action',
      width: 120,
      render: (_: any, record: MinioMeta) => (
        <Space size="small">
          <Button 
            size="small" 
            icon={<EditOutlined />}
            onClick={() => {
              setEditingMinioMetadata(record);
              minioForm.setFieldsValue(record);
              setMinioMetadataModal(true);
            }}
          />
          <Popconfirm
            title="确定删除此标签？"
            description="删除后不可恢复，请确认"
            onConfirm={() => handleDeleteMinioMetadata(record.id)}
            okText="确定"
            cancelText="取消"
          >
            <Button size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      )
    }
  ];

  const mysqlPagination = useTablePagination(mysqlColumns.length, {
    defaultPageSize: 20,
    resetDeps: [selectedMysqlTable?.db, selectedMysqlTable?.table, activeTab],
  });
  const neo4jPagination = useTablePagination(neo4jProperties.length, {
    defaultPageSize: 20,
    resetDeps: [selectedNeo4jLabel?.type, selectedNeo4jLabel?.name, activeTab],
  });
  const chromaPagination = useTablePagination(chromaMetadata.length, {
    defaultPageSize: 20,
    resetDeps: [selectedChromaCollection, activeTab],
  });
  const minioPagination = useTablePagination(minioMetadata.length, {
    defaultPageSize: 20,
    resetDeps: [selectedMinioBucket, activeTab],
  });

  const tabItems = [
    {
      key: 'mysql',
      label: (
        <Space>
          <DatabaseOutlined />
          MySQL数据库
        </Space>
      ),
      children: (
        <Row gutter={16}>
          <Col span={6}>
            <Card size="small" title="数据库/表" extra={
              <Button size="small" icon={<ReloadOutlined />} onClick={fetchMysqlTree}>刷新</Button>
            }>
              <Tree
                treeData={mysqlTree}
                onSelect={handleMysqlTreeSelect}
                defaultExpandAll
                style={{ height: 'calc(100vh - 300px)', overflow: 'auto' }}
              />
            </Card>
          </Col>
          <Col span={18}>
            <Card size="small" title={
              selectedMysqlTable 
                ? `${selectedMysqlTable.db}.${selectedMysqlTable.table} 字段列表`
                : '请选择左侧表'
            } extra={
              selectedMysqlTable && (
                <Button 
                  size="small" 
                  type="primary" 
                  icon={<PlusOutlined />}
                  onClick={() => {
                    setEditingMysqlColumn(null);
                    mysqlForm.resetFields();
                    setMysqlColumnModal(true);
                  }}
                >
                  新增字段
                </Button>
              )
            }>
              <Table
                dataSource={mysqlColumns}
                rowKey="id"
                columns={mysqlColumnsTable}
                size="small"
                pagination={mysqlPagination}
                loading={loading}
                locale={{ emptyText: '请选择左侧表查看字段' }}
              />
            </Card>
          </Col>
        </Row>
      )
    },
    {
      key: 'neo4j',
      label: (
        <Space>
          <BranchesOutlined />
          Neo4j图数据库
        </Space>
      ),
      children: (
        <Row gutter={16}>
          <Col span={6}>
            <Card size="small" title="节点/关系" extra={
              <Space>
                <Button 
                  size="small" 
                  icon={<PlusOutlined />}
                  onClick={() => {
                    if (!selectedNeo4jLabel) {
                      message.warning('请先点击左侧树中的节点或关系分类');
                      return;
                    }
                    neo4jForm.resetFields();
                    setNeo4jLabelModal(true);
                  }}
                >
                  新增
                </Button>
                <Button size="small" icon={<ReloadOutlined />} onClick={fetchNeo4jTree}>刷新</Button>
              </Space>
            }>
              <Tree
                treeData={neo4jTree}
                onSelect={handleNeo4jTreeSelect}
                defaultExpandAll
                style={{ height: 'calc(100vh - 300px)', overflow: 'auto' }}
              />
            </Card>
          </Col>
          <Col span={18}>
            <Card size="small" title={
              selectedNeo4jLabel 
                ? `${selectedNeo4jLabel.type === 'node' ? '节点' : '关系'}: ${selectedNeo4jLabel.name} 属性列表`
                : '请选择左侧标签'
            } extra={
              selectedNeo4jLabel && (
                <Space>
                  <Button 
                    size="small" 
                    type="primary" 
                    icon={<PlusOutlined />}
                    onClick={() => {
                      setEditingNeo4jProperty(null);
                      neo4jForm.resetFields();
                      setNeo4jPropertyModal(true);
                    }}
                  >
                    新增属性
                  </Button>
                  <Popconfirm
                    title="确定删除此标签？"
                    description="删除后不可恢复，将删除所有相关属性"
                    onConfirm={() => {
                      if (selectedNeo4jLabel) {
                        handleDeleteNeo4jLabel(selectedNeo4jLabel.type, selectedNeo4jLabel.name);
                      }
                    }}
                    okText="确定"
                    cancelText="取消"
                  >
                    <Button size="small" danger icon={<DeleteOutlined />}>删除标签</Button>
                  </Popconfirm>
                </Space>
              )
            }>
              <Table
                dataSource={neo4jProperties}
                rowKey="id"
                columns={neo4jPropertiesTable}
                size="small"
                pagination={neo4jPagination}
                loading={loading}
                locale={{ emptyText: '请选择左侧标签查看属性' }}
              />
            </Card>
          </Col>
        </Row>
      )
    },
    {
      key: 'chroma',
      label: (
        <Space>
          <CloudServerOutlined />
          Chroma向量数据库
        </Space>
      ),
      children: (
        <Row gutter={16}>
          <Col span={6}>
            <Card size="small" title="集合" extra={
              <Button size="small" icon={<ReloadOutlined />} onClick={fetchChromaTree}>刷新</Button>
            }>
              <Tree
                treeData={chromaTree}
                onSelect={(keys) => {
                  if (keys.length > 0) {
                    const key = keys[0] as string;
                    if (key.startsWith('collection_')) {
                      const collectionName = key.replace('collection_', '');
                      setSelectedChromaCollection(collectionName);
                      fetchChromaMetadata(collectionName);
                    }
                  }
                }}
                defaultExpandAll
                style={{ height: 'calc(100vh - 300px)', overflow: 'auto' }}
              />
            </Card>
          </Col>
          <Col span={18}>
            <Card size="small" title="Chroma集合元数据">
              <Table
                dataSource={chromaMetadata}
                rowKey="id"
                columns={chromaMetadataTable}
                size="small"
                pagination={chromaPagination}
                locale={{ emptyText: '请选择左侧集合查看元数据' }}
              />
            </Card>
          </Col>
        </Row>
      )
    },
    {
      key: 'minio',
      label: (
        <Space>
          <FileOutlined />
          MinIO文件存储
        </Space>
      ),
      children: (
        <Row gutter={16}>
          <Col span={6}>
            <Card size="small" title="存储桶" extra={
              <Button size="small" icon={<ReloadOutlined />} onClick={fetchMinioTree}>刷新</Button>
            }>
              <Tree
                treeData={minioTree}
                onSelect={(keys) => {
                  if (keys.length > 0) {
                    const key = keys[0] as string;
                    if (key.startsWith('bucket_')) {
                      const bucketName = key.replace('bucket_', '');
                      setSelectedMinioBucket(bucketName);
                      fetchMinioMetadata(bucketName);
                    }
                  }
                }}
                defaultExpandAll
                style={{ height: 'calc(100vh - 300px)', overflow: 'auto' }}
              />
            </Card>
          </Col>
          <Col span={18}>
            <Card size="small" title="MinIO存储桶元数据">
              <Table
                dataSource={minioMetadata}
                rowKey="id"
                columns={minioMetadataTable}
                size="small"
                pagination={minioPagination}
                locale={{ emptyText: '请选择左侧存储桶查看元数据' }}
              />
            </Card>
          </Col>
        </Row>
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
            <DatabaseOutlined style={{ fontSize: 20 }} />
            <Title level={4} style={{ margin: 0 }}>数据库管理</Title>
          </Space>
        }
        extra={
          <Popconfirm
            title="从实际数据库同步元数据？"
            description="将扫描 MySQL / Neo4j / MinIO / Chroma 的实际结构，新增缺失项、更新类型、删除已不存在的项。手工填写的描述/显示名会尽量保留。"
            onConfirm={handleSyncMetadata}
            okText="开始同步"
            cancelText="取消"
          >
            <Button type="primary" icon={<SyncOutlined />} loading={syncing}>
              同步元数据
            </Button>
          </Popconfirm>
        }
      >
        <Tabs activeKey={activeTab} onChange={setActiveTab} items={tabItems} />
      </Card>

      <Modal
        title={editingMysqlColumn ? '编辑字段' : '新增字段'}
        open={mysqlColumnModal}
        onOk={handleSaveMysqlColumn}
        onCancel={() => {
          setMysqlColumnModal(false);
          setEditingMysqlColumn(null);
          mysqlForm.resetFields();
        }}
        width={600}
      >
        <Form form={mysqlForm} layout="vertical">
          {editingMysqlColumn ? (
            <>
              <Row gutter={16}>
                <Col span={12}>
                  <Form.Item label="字段名">
                    <Input value={editingMysqlColumn.column_name} disabled />
                  </Form.Item>
                </Col>
                <Col span={12}>
                  <Form.Item label="字段类型">
                    <Input value={editingMysqlColumn.column_type} disabled />
                  </Form.Item>
                </Col>
              </Row>
              <Row gutter={16}>
                <Col span={12}>
                  <Form.Item label="数据类型">
                    <Input value={editingMysqlColumn.data_type} disabled />
                  </Form.Item>
                </Col>
                <Col span={12}>
                  <Form.Item label="可空">
                    <Input value={editingMysqlColumn.is_nullable ? '是' : '否'} disabled />
                  </Form.Item>
                </Col>
              </Row>
              <Row gutter={16}>
                <Col span={12}>
                  <Form.Item label="键">
                    <Input value={editingMysqlColumn.column_key || '-'} disabled />
                  </Form.Item>
                </Col>
                <Col span={12}>
                  <Form.Item label="默认值">
                    <Input value={editingMysqlColumn.column_default || '-'} disabled />
                  </Form.Item>
                </Col>
              </Row>
              <Form.Item name="description" label="描述">
                <Input placeholder="字段描述（可选）" />
              </Form.Item>
              <Form.Item name="status" label="状态" rules={[{ required: true }]}>
                <Select placeholder="选择状态">
                  <Select.Option value="正常">正常</Select.Option>
                  <Select.Option value="废弃">废弃</Select.Option>
                  <Select.Option value="待审核">待审核</Select.Option>
                </Select>
              </Form.Item>
            </>
          ) : (
            <>
              <Form.Item name="column_name" label="字段名" rules={[{ required: true }]}>
                <Input placeholder="输入字段名" />
              </Form.Item>
              <Form.Item name="column_type" label="字段类型" rules={[{ required: true }]}>
                <Select placeholder="选择字段类型">
                  <Select.Option value="INT">INT</Select.Option>
                  <Select.Option value="BIGINT">BIGINT</Select.Option>
                  <Select.Option value="VARCHAR(255)">VARCHAR(255)</Select.Option>
                  <Select.Option value="VARCHAR(500)">VARCHAR(500)</Select.Option>
                  <Select.Option value="TEXT">TEXT</Select.Option>
                  <Select.Option value="LONGTEXT">LONGTEXT</Select.Option>
                  <Select.Option value="DATETIME">DATETIME</Select.Option>
                  <Select.Option value="TIMESTAMP">TIMESTAMP</Select.Option>
                  <Select.Option value="BOOLEAN">BOOLEAN</Select.Option>
                  <Select.Option value="JSON">JSON</Select.Option>
                  <Select.Option value="DECIMAL(10,2)">DECIMAL(10,2)</Select.Option>
                </Select>
              </Form.Item>
              <Row gutter={16}>
                <Col span={12}>
                  <Form.Item name="is_nullable" label="允许为空" valuePropName="checked">
                    <Switch checkedChildren="是" unCheckedChildren="否" />
                  </Form.Item>
                </Col>
                <Col span={12}>
                  <Form.Item name="column_default" label="默认值">
                    <Input placeholder="默认值（可选）" />
                  </Form.Item>
                </Col>
              </Row>
              <Form.Item name="description" label="描述">
                <Input placeholder="字段描述（可选）" />
              </Form.Item>
              <Form.Item name="status" label="状态" rules={[{ required: true }]}>
                <Select placeholder="选择状态">
                  <Select.Option value="正常">正常</Select.Option>
                  <Select.Option value="废弃">废弃</Select.Option>
                  <Select.Option value="待审核">待审核</Select.Option>
                </Select>
              </Form.Item>
            </>
          )}
        </Form>
      </Modal>

      <Modal
        title="新增标签（节点或关系）"
        open={neo4jLabelModal}
        onOk={() => {
          const type = selectedNeo4jLabel?.type || 'node';
          handleCreateNeo4jLabel(type as 'node' | 'relation');
        }}
        onCancel={() => {
          setNeo4jLabelModal(false);
          neo4jForm.resetFields();
        }}
        width={500}
      >
        <Form form={neo4jForm} layout="vertical">
          <Form.Item name="label_name" label="标签名称" rules={[{ required: true }]}>
            <Input placeholder="输入标签名称（如：Person, Company, WORKS_AT）" />
          </Form.Item>
          <Alert 
            message="提示" 
            description="新增的标签会自动添加Id属性作为唯一标识。节点标签会在Neo4j中创建临时节点，关系标签仅记录元数据。" 
            type="info" 
            showIcon 
          />
        </Form>
      </Modal>

      <Modal
        title={editingNeo4jProperty ? '编辑属性' : '新增属性'}
        open={neo4jPropertyModal}
        onOk={handleSaveNeo4jProperty}
        onCancel={() => {
          setNeo4jPropertyModal(false);
          setEditingNeo4jProperty(null);
          neo4jForm.resetFields();
        }}
        width={600}
      >
        <Form form={neo4jForm} layout="vertical">
          {editingNeo4jProperty ? (
            <>
              <Row gutter={16}>
                <Col span={12}>
                  <Form.Item label="属性名">
                    <Input value={editingNeo4jProperty.property_name} disabled />
                  </Form.Item>
                </Col>
                <Col span={12}>
                  <Form.Item label="属性类型">
                    <Input value={editingNeo4jProperty.property_type} disabled />
                  </Form.Item>
                </Col>
              </Row>
              <Row gutter={16}>
                <Col span={12}>
                  <Form.Item label="显示名">
                    <Input value={editingNeo4jProperty.display_name} disabled />
                  </Form.Item>
                </Col>
                <Col span={12}>
                  <Form.Item label="必填">
                    <Input value={editingNeo4jProperty.is_required ? '是' : '否'} disabled />
                  </Form.Item>
                </Col>
              </Row>
              <Row gutter={16}>
                <Col span={12}>
                  <Form.Item label="索引">
                    <Input value={editingNeo4jProperty.is_indexed ? '是' : '否'} disabled />
                  </Form.Item>
                </Col>
                <Col span={12}>
                  <Form.Item label="唯一">
                    <Input value={editingNeo4jProperty.is_unique ? '是' : '否'} disabled />
                  </Form.Item>
                </Col>
              </Row>
              <Form.Item name="description" label="描述">
                <Input placeholder="属性描述（可选）" />
              </Form.Item>
              <Form.Item name="status" label="状态" rules={[{ required: true }]}>
                <Select placeholder="选择状态">
                  <Select.Option value="正常">正常</Select.Option>
                  <Select.Option value="废弃">废弃</Select.Option>
                  <Select.Option value="待审核">待审核</Select.Option>
                </Select>
              </Form.Item>
            </>
          ) : (
            <>
              <Form.Item name="property_name" label="属性名" rules={[{ required: true }]}>
                <Input placeholder="输入属性名" />
              </Form.Item>
              <Form.Item name="property_type" label="属性类型" rules={[{ required: true }]}>
                <Select placeholder="选择属性类型">
                  <Select.Option value="STRING">STRING</Select.Option>
                  <Select.Option value="INTEGER">INTEGER</Select.Option>
                  <Select.Option value="FLOAT">FLOAT</Select.Option>
                  <Select.Option value="BOOLEAN">BOOLEAN</Select.Option>
                  <Select.Option value="DATE">DATE</Select.Option>
                  <Select.Option value="DATETIME">DATETIME</Select.Option>
                  <Select.Option value="LIST">LIST</Select.Option>
                </Select>
              </Form.Item>
              <Form.Item name="display_name" label="显示名">
                <Input placeholder="显示名（可选）" />
              </Form.Item>
              <Row gutter={16}>
                <Col span={8}>
                  <Form.Item name="is_required" label="是否必填" valuePropName="checked">
                    <Switch checkedChildren="是" unCheckedChildren="否" />
                  </Form.Item>
                </Col>
                <Col span={8}>
                  <Form.Item name="is_indexed" label="是否索引" valuePropName="checked">
                    <Switch checkedChildren="是" unCheckedChildren="否" />
                  </Form.Item>
                </Col>
                <Col span={8}>
                  <Form.Item name="is_unique" label="是否唯一" valuePropName="checked">
                    <Switch checkedChildren="是" unCheckedChildren="否" />
                  </Form.Item>
                </Col>
              </Row>
              <Form.Item name="description" label="描述">
                <Input placeholder="属性描述（可选）" />
              </Form.Item>
              <Form.Item name="status" label="状态" rules={[{ required: true }]}>
                <Select placeholder="选择状态">
                  <Select.Option value="正常">正常</Select.Option>
                  <Select.Option value="废弃">废弃</Select.Option>
                  <Select.Option value="待审核">待审核</Select.Option>
                </Select>
              </Form.Item>
            </>
          )}
        </Form>
      </Modal>
    </div>
  );
}