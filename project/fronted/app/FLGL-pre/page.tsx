'use client';

import React, { useState, useEffect, useMemo } from 'react';
import { Layout, Tree, Typography, Divider, Spin, Empty, Button, Space, message, Upload, Modal, Table, Tabs, Drawer, Popconfirm, Select, Input, Tag } from 'antd';
import {
  AppstoreOutlined, EnvironmentOutlined, ExperimentOutlined,
  BarChartOutlined, FileTextOutlined, FileOutlined,
  ThunderboltOutlined, ReloadOutlined, MenuFoldOutlined, MenuUnfoldOutlined,
  UploadOutlined, EyeOutlined, DatabaseOutlined, DeleteOutlined, EditOutlined
} from '@ant-design/icons';
import type { DataNode } from 'antd/es/tree';
import EquipmentTypePanel from './components/EquipmentTypePanel';
import EquipmentPanel from './components/EquipmentPanel';
import EnvironmentPanel from './components/EnvironmentPanel';
import ProjectPanel from './components/ProjectPanel';
import IndicatorPanel from './components/IndicatorPanel';
import MethodPanel from './components/MethodPanel';
import FilePanel from './components/FilePanel';
import { fetchNodes, fetchMethodCategoryTree, type NodeType, type GraphNode } from './api';
import { ENVIRONMENT_ORDER } from '@/constants/environments';

const { Sider, Content, Header } = Layout;
const { Title, Text } = Typography;

const INDICATOR_CATS = [
  { key: '距离', keywords: ['距离', 'range', 'distance'] },
  { key: '时间', keywords: ['时间', 'time', 'duration', '周期'] },
  { key: '速度', keywords: ['速度', 'speed', 'velocity'] },
  { key: '高度', keywords: ['高度', 'altitude', 'height', '高程'] },
];

function classifyIndicator(name: string): string {
  if (!name) return '其他';
  const lower = name.toLowerCase();
  for (const cat of INDICATOR_CATS) {
    if (cat.keywords.some(kw => lower.includes(kw))) return cat.key;
  }
  return '其他';
}

// 定义一个常量数组，包含了系统中所有节点类型的信息，包括唯一键、显示标签和对应的图标组件
const NODE_TYPES: Array<{ key: string; label: string; icon: React.ReactNode }> = [
  { key: 'EquipmentType', label: '设备类型', icon: <AppstoreOutlined /> },
  { key: 'Equipment', label: '设备', icon: <ThunderboltOutlined /> },
  { key: 'Environment', label: '环境', icon: <EnvironmentOutlined /> },
  { key: 'TestProject', label: '项目', icon: <ExperimentOutlined /> },
  { key: 'Indicator', label: '指标', icon: <BarChartOutlined /> },
  { key: 'TestMethod', label: '方法', icon: <FileTextOutlined /> },
  // { key: 'File', label: '文件', icon: <FileOutlined /> },
];

const NO_SIDEBAR_TYPES = new Set(['Environment']);

function flglNodeTypeLabel(key: string): string {
  return NODE_TYPES.find((n) => n.key === key)?.label || (key === 'standard' ? '标准' : key);
}

// 定义数据结构类型
interface ParsedData {
  task_id: string;
  equipment_types: any[];
  equipments: any[];
  environments: any[];
  test_projects: any[];
  indicators: any[];
  relationships: any[];
}

export default function FLGLPage() {
  // 定义组件的状态变量，存储和管理界面中会变化的数据
  const [selectedType, setSelectedType] = useState<string>('EquipmentType');
  const [selectedCategory, setSelectedCategory] = useState<string | null>(null);
  const [treeData, setTreeData] = useState<DataNode[]>([]);
  const [treeLoading, setTreeLoading] = useState(false);
  const [allNodes, setAllNodes] = useState<GraphNode[]>([]);
  const [categoryNodes, setCategoryNodes] = useState<GraphNode[]>([]);
  const [methodCategoryMap, setMethodCategoryMap] = useState<Record<string, string[]>>({});
  const [typeCollapsed, setTypeCollapsed] = useState(false);
  const [treeCollapsed, setTreeCollapsed] = useState(false);
  const [uploading, setUploading] = useState(false);
  
  // 新增状态
  const [resultDrawerVisible, setResultDrawerVisible] = useState(false);
  const [parsedData, setParsedData] = useState<ParsedData | null>(null);
  const [currentTaskId, setCurrentTaskId] = useState<string | null>(null);
  const [importing, setImporting] = useState(false);
  /** Neo4j 名称冲突：待用户选「替换/跳过/重命名」后二次提交 */
  const [importConflictOpen, setImportConflictOpen] = useState(false);
  const [importConflicts, setImportConflicts] = useState<{
    database_conflicts: any[];
    in_file_conflicts: any[];
  } | null>(null);
  const [importResolutionState, setImportResolutionState] = useState<Record<string, { action: string; newName: string }>>(
    {}
  );
  const [activeTab, setActiveTab] = useState('equipment_types');
  /** 文档解析结果-关系 tab 中按关系类型（语义）筛选 */
  const [relTypeFilter, setRelTypeFilter] = useState<string>('all');

  useEffect(() => {
    loadTreeData();
  }, [selectedType]);

  // 根据当前选中节点类型，从后端获取数据，然后动态构建左侧分类树的展示结构
  const loadTreeData = async () => {
    setTreeLoading(true);
    if (selectedType !== 'TestMethod') {
      setMethodCategoryMap({});
    }
    try {
      const nodes = await fetchNodes(selectedType as NodeType);
      const list = Array.isArray(nodes) ? nodes : [];
      setAllNodes(list);

      if (selectedType === 'EquipmentType') {
        const types = Array.from(new Set(list.map(n => n.type).filter(Boolean))) as string[];
        const tree: DataNode[] = types.map(t => ({
          title: `${t} (${list.filter(n => n.type === t).length})`,
          key: t,
          // children: list.filter(n => n.type === t).map(n => ({
          //   title: n.name || n.id,
          //   key: n.id,
          // })),
        }));
        setTreeData(tree);
      } else if (selectedType === 'Equipment') {
        const eqTypes = await fetchNodes('EquipmentType');
        const typeList = Array.isArray(eqTypes) ? eqTypes : [];
        setCategoryNodes(typeList);
        const types = Array.from(new Set(typeList.map(n => n.type).filter(Boolean))) as string[];
        const tree: DataNode[] = types.map(t => {
          const typeNodes = typeList.filter(n => n.type === t);
          return {
            title: t,
            key: `type_${t}`,
            children: typeNodes.map(n => ({
              title: n.name || n.id,
              key: `eqtype_${n.id}`,
            })),
          };
        });
        setTreeData(tree);
      } else if (selectedType === 'TestProject') {
        const order = [...ENVIRONMENT_ORDER];
        const orderSet = new Set(order);
        const byCat: Record<string, GraphNode[]> = {};
        for (const cat of order) byCat[cat] = [];
        const otherList: GraphNode[] = [];
        for (const p of list) {
          const lbl = String((p as { label?: string }).label || '').trim();
          const environmentMatch = order.find(env => lbl.includes(env) || env.includes(lbl) || lbl.includes(env.replace('环境', '')));
          if (environmentMatch) {
            byCat[environmentMatch].push(p);
          } else {
            otherList.push(p);
          }
        }
        const tree: DataNode[] = [
          ...order.map((cat) => ({
            title: `${cat} (${byCat[cat].length})`,
            key: cat,
            // children: byCat[cat].map((n) => ({ title: n.name || n.id, key: n.id })),
          })),
          {
            title: `其他 (${otherList.length})`,
            key: '其他',
            // children: otherList.map((n) => ({ title: n.name || n.id, key: n.id })),
          },
        ];
        setCategoryNodes([
          ...order.map((name) => ({ id: name, name } as GraphNode)),
          { id: '其他', name: '其他' } as GraphNode,
        ]);
        setTreeData(tree);
      } else if (selectedType === 'Indicator') {
        const tree: DataNode[] = [...INDICATOR_CATS.map(cat => {
          const items = list.filter(n => classifyIndicator(n.name || '') === cat.key);
          // (${items.length}) (${list.filter(n => classifyIndicator(n.name || '') === '其他').length})
          return {
            title: `${cat.key}`,
            key: cat.key,
            // children: items.map(n => ({ title: n.name || n.id, key: n.id })),
          };
        }), {
          title: `其他`,
          key: '其他',
          // children: list.filter(n => classifyIndicator(n.name || '') === '其他').map(n => ({ title: n.name || n.id, key: n.id })),
        }];
        setTreeData(tree);
      } else if (selectedType === 'TestMethod') {
        try {
          const mcat = await fetchMethodCategoryTree();
          setMethodCategoryMap(mcat.method_category_keys || {});
          setTreeData(
            (mcat.categories || []).map((c) => ({
              title: `${c.title}（${c.count}）`,
              key: c.key,
            })),
          );
        } catch {
          setMethodCategoryMap({});
          setTreeData([]);
          message.warning('方法分类树加载失败，可刷新重试');
        }
        setCategoryNodes([]);
      } else if (selectedType === 'File') {
        const types = Array.from(new Set(list.map(n => n.type).filter(Boolean))) as string[];
        const tree: DataNode[] = types.map(t => ({
          title: `${t} (${list.filter(n => n.type === t).length})`,
          key: t,
          // children: list.filter(n => n.type === t).map(n => ({ title: n.name || n.id, key: n.id })),
        }));
        setTreeData(tree);
      } else {
        setTreeData([]);
        setSelectedCategory(null);
      }
    } catch (err: any) {
      message.error(`加载失败: ${err.message}`);
    } finally {
      setTreeLoading(false);
    }
  };

  const handleRefresh = () => {
    loadTreeData();
    message.success('已刷新');
  };

  // 上传处理函数
  const handleFileUpload = async (file: File) => {
    setUploading(true);
    const hideLoading = message.loading('正在上传并解析文件...', 0);

    try {
      const formData = new FormData();
      formData.append('file', file);

      const response = await fetch('/api/FLGL/upload', {
        method: 'POST',
        body: formData,
      });

      const result = await response.json();
      
      if (result.success) {
        if (result.data.is_duplicate) {
          message.warning(`文件已上传过，正在加载之前的解析结果...（上次上传时间：${result.data.upload_time}）`);
        } else {
          message.success(result.message || '上传成功');
        }

        setCurrentTaskId(result.data.task_id);
        // 自动打开查看窗口
        await loadParseResult(result.data.task_id);
        await loadTreeData();
      } else {
        message.error(result.message || '上传失败');
      }
    } catch (err: any) {
      message.error(`上传失败: ${err.message}`);
    } finally {
      setUploading(false);
      hideLoading();
    }

    return false;
  };

  // 加载解析结果
  const loadParseResult = async (taskId: string) => {
    try {
      const response = await fetch(`/api/FLGL/parse-result/${taskId}`);
      const result = await response.json();
      debugger
      if (result.success) {
        setParsedData(result.data);
        setResultDrawerVisible(true);
      } else {
        message.error(result.message || '获取解析结果失败');
      }
    } catch (err: any) {
      message.error(`获取解析结果失败: ${err.message}`);
    }
  };

  // 查看已上传文件的解析结果
  const handleViewResult = () => {
    if (!currentTaskId) {
      message.warning('请先上传文件');
      return;
    }
    loadParseResult(currentTaskId);
  };

  const importConflictRows = useMemo(() => {
    if (!importConflicts) return [] as { row: any; src: 'db' | 'file' }[];
    return [
      ...(importConflicts.database_conflicts || []).map((row) => ({ row, src: 'db' as const })),
      ...(importConflicts.in_file_conflicts || []).map((row) => ({ row, src: 'file' as const })),
    ];
  }, [importConflicts]);

  // 导入到 Neo4j：先预检重名，再据用户选择提交 resolutions
  const runNeo4jImportRequest = async (resolutions: { conflict_id: string; action: string; new_name?: string }[]) => {
    if (!currentTaskId) return;
    setImporting(true);
    const hide = message.loading(
      resolutions.length > 0 ? '正在按您的选择导入 Neo4j...' : '正在导入到 Neo4j...',
      0
    );
    try {
      const response = await fetch(`/api/FLGL/import-to-neo4j/${currentTaskId}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(resolutions.length > 0 ? { resolutions } : {}),
      });
      const result = await response.json();
      if (!response.ok) {
        const err = new Error((result as any).message || `请求失败 (${response.status})`);
        message.error(err.message);
        throw err;
      }
      if (result.name_conflicts) {
        if (resolutions.length > 0) {
          setImportConflicts(result.data);
          const next: Record<string, { action: string; newName: string }> = {};
          for (const c of result.data?.database_conflicts || []) {
            next[c.conflict_id] = { action: 'skip', newName: '' };
          }
          for (const c of result.data?.in_file_conflicts || []) {
            next[c.conflict_id] = { action: 'skip', newName: '' };
          }
          setImportResolutionState(next);
          message.error('与上次预检结果不一致，请按新列表重新选择。');
          throw new Error('conflict list changed');
        }
        setImportConflicts(result.data);
        const next: Record<string, { action: string; newName: string }> = {};
        for (const c of result.data?.database_conflicts || []) {
          next[c.conflict_id] = { action: 'skip', newName: '' };
        }
        for (const c of result.data?.in_file_conflicts || []) {
          next[c.conflict_id] = { action: 'skip', newName: '' };
        }
        setImportResolutionState(next);
        setImportConflictOpen(true);
        message.info('存在名称重复的节点，请在弹窗中逐项选择后确认导入。');
        return;
      }
      if (result.success) {
        message.success(
          `导入成功！共创建 ${result.data?.nodes_created ?? 0} 个节点，${result.data?.relationships_created ?? 0} 个关系`
        );
        setImportConflictOpen(false);
        setImportConflicts(null);
        setImportResolutionState({});
        await loadTreeData();
        return;
      }
      const err = new Error((result as any).message || '导入失败');
      message.error(err.message);
      throw err;
    } catch (e: any) {
      if (e?.message === 'conflict list changed') {
        throw e;
      }
      if (e instanceof TypeError || (e as Error).name === 'TypeError') {
        message.error('网络异常，请检查连接后重试。');
      }
      throw e;
    } finally {
      setImporting(false);
      hide();
    }
  };

  const handleImportToNeo4j = () => {
    if (!currentTaskId) {
      message.warning('请先上传文件');
      return;
    }
    Modal.confirm({
      title: '确认导入',
      content: '将先检查与图库中同类型节点重名，若有冲突会提示您选择替换、跳过或重命名；无冲突则直接导入。',
      onOk: () => runNeo4jImportRequest([]),
    });
  };

  const submitImportWithResolutions = () => {
    const resolutions: { conflict_id: string; action: string; new_name?: string }[] = [];
    for (const { row } of importConflictRows) {
      const st = importResolutionState[row.conflict_id] ?? { action: 'skip', newName: '' };
      const action = st.action;
      if (action === 'rename' && !st.newName?.trim()) {
        message.error('请为所有「重命名」行填写新名称，或改选其它操作。');
        return Promise.reject();
      }
      const item: { conflict_id: string; action: string; new_name?: string } = {
        conflict_id: row.conflict_id,
        action: action as string,
      };
      if (action === 'rename') {
        item.new_name = st.newName.trim();
      }
      resolutions.push(item);
    }
    if (resolutions.length !== importConflictRows.length) {
      message.error('行数据不完整，请关闭后重新打开导入。');
      return Promise.reject();
    }
    return runNeo4jImportRequest(resolutions);
  };

  // 保存编辑后的数据
  const handleSaveEditedData = async (editedData: any) => {
    if (!currentTaskId) return;

    try {
      const response = await fetch('/api/FLGL/save-edited', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          task_id: currentTaskId,
          edited_data: editedData,
        }),
      });

      const result = await response.json();

      if (result.success) {
        message.success('数据保存成功');
        // 重新加载解析结果
        await loadParseResult(currentTaskId);
      } else {
        message.error(result.message || '保存失败');
      }
    } catch (err: any) {
      message.error(`保存失败: ${err.message}`);
    }
  };

  // 更新单个节点
  const handleUpdateNode = async (nodeType: string, nodeId: string, updatedData: any) => {
    try {
      const response = await fetch('/api/FLGL/update-node', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          task_id: currentTaskId,
          node_type: nodeType,
          node_id: nodeId,
          updated_data: updatedData,
        }),
      });

      const result = await response.json();

      if (result.success) {
        message.success('节点更新成功');
        setParsedData(result.data);
      } else {
        message.error(result.message || '更新失败');
      }
    } catch (err: any) {
      message.error(`更新失败: ${err.message}`);
    }
  };

  // 删除节点
  const handleDeleteNode = async (nodeType: string, nodeId: string) => {
    Modal.confirm({
      title: '确认删除',
      content: '确定要删除这个节点吗？删除后相关的关联关系也会被删除。',
      onOk: async () => {
        try {
          const response = await fetch('/api/FLGL/delete-node', {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
            },
            body: JSON.stringify({
              task_id: currentTaskId,
              node_type: nodeType,
              node_id: nodeId,
            }),
          });

          const result = await response.json();

          if (result.success) {
            message.success('节点删除成功');
            setParsedData(result.data);
          } else {
            message.error(result.message || '删除失败');
          }
        } catch (err: any) {
          message.error(`删除失败: ${err.message}`);
        }
      },
    });
  };

  const renderContent = () => {
    const props = {
      allNodes,
      selectedCategory,
      categoryNodes,
      onRefresh: loadTreeData,
      methodCategoryMap: selectedType === 'TestMethod' ? methodCategoryMap : undefined,
    };

    switch (selectedType) {
      case 'EquipmentType':
        return <EquipmentTypePanel {...props} />;
      case 'Equipment':
        return <EquipmentPanel {...props} />;
      case 'Environment':
        return <EnvironmentPanel {...props} />;
      case 'TestProject':
        return <ProjectPanel {...props} />;
      case 'Indicator':
        return <IndicatorPanel {...props} />;
      case 'TestMethod':
        return <MethodPanel {...props} />;
      // case 'File':
      //   return <FilePanel {...props} />;
      default:
        return <Empty description="请选择节点类型" />;
    }
  };

  const currentType = NODE_TYPES.find(t => t.key === selectedType);
  const showTreeSidebar = !NO_SIDEBAR_TYPES.has(selectedType);

  // 定义表格列
  const equipmentTypeColumns = [
    { title: 'ID', dataIndex: 'id', key: 'id', width: 100 },
    { title: '名称', dataIndex: 'name', key: 'name' },
    { title: '一级设备类别', dataIndex: 'type', key: 'level1', width: 160, ellipsis: true },
    { title: '描述', dataIndex: 'description', key: 'description' },
    {
      title: '操作',
      key: 'action',
      width: 150,
      render: (_: any, record: any) => (
        <Space>
          <Button size="small" icon={<EditOutlined />} onClick={() => {
            // 编辑逻辑
            Modal.confirm({
              title: '编辑设备类型',
              content: (
                <input
                  id="edit-name"
                  defaultValue={record.name}
                  className="ant-input"
                  style={{ marginTop: 10 }}
                />
              ),
              onOk: () => {
                const newName = (document.getElementById('edit-name') as HTMLInputElement).value;
                handleUpdateNode('equipment_type', record.id, { name: newName });
              },
            });
          }}>编辑</Button>
          <Button size="small" danger icon={<DeleteOutlined />} onClick={() => handleDeleteNode('equipment_type', record.id)}>删除</Button>
        </Space>
      ),
    },
  ];

  const equipmentColumns = [
    { title: 'ID', dataIndex: 'id', key: 'id', width: 100 },
    { title: '名称', dataIndex: 'name', key: 'name' },
    { title: '一级设备类别', dataIndex: 'type', key: 'type', width: 160, ellipsis: true },
    { title: '描述', dataIndex: 'description', key: 'description' },
    {
      title: '操作',
      key: 'action',
      width: 150,
      render: (_: any, record: any) => (
        <Space>
          <Button size="small" icon={<EditOutlined />} onClick={() => {
            Modal.confirm({
              title: '编辑设备',
              content: (
                <div>
                  <div>名称: <input id="edit-name" defaultValue={record.name} className="ant-input" /></div>
                  <div style={{ marginTop: 10 }}>描述: <textarea id="edit-desc" defaultValue={record.description} className="ant-input" /></div>
                </div>
              ),
              onOk: () => {
                const newName = (document.getElementById('edit-name') as HTMLInputElement).value;
                const newDesc = (document.getElementById('edit-desc') as HTMLTextAreaElement).value;
                handleUpdateNode('equipment', record.id, { name: newName, description: newDesc });
              },
            });
          }}>编辑</Button>
          <Button size="small" danger icon={<DeleteOutlined />} onClick={() => handleDeleteNode('equipment', record.id)}>删除</Button>
        </Space>
      ),
    },
  ];
  const environmentColumns = [
      { title: 'ID', dataIndex: 'id', key: 'id', width: 100 },
      { title: '名称', dataIndex: 'name', key: 'name' },
      { title: '类型', dataIndex: 'type', key: 'type', width: 120 },
      { title: '类别', dataIndex: 'class', key: 'class', width: 120 },
      { title: '组合条件', dataIndex: 'combination', key: 'combination' },
      { title: '限制条件', dataIndex: 'limit', key: 'limit' },
      { title: '要求', dataIndex: 'request', key: 'request' },
      
      {
        title: '操作',
        key: 'action',
        width: 150,
        render: (_: any, record: any) => (
          <Space>
            <Button size="small" icon={<EditOutlined />} onClick={() => {
              Modal.confirm({
                title: '编辑环境',
                content: (
                  <div>
                    <div>名称: <input id="edit-name" defaultValue={record.name} className="ant-input" /></div>
                    <div style={{ marginTop: 10 }}>类型: <input id="edit-type" defaultValue={record.type} className="ant-input" /></div>
                    <div style={{ marginTop: 10 }}>类别: <input id="edit-class" defaultValue={record.class} className="ant-input" /></div>
                    <div style={{ marginTop: 10 }}>组合条件: <input id="edit-combination" defaultValue={record.combination} className="ant-input" /></div>
                    <div style={{ marginTop: 10 }}>限制条件: <input id="edit-limit" defaultValue={record.limit} className="ant-input" /></div>
                    <div style={{ marginTop: 10 }}>要求: <textarea id="edit-request" defaultValue={record.request} className="ant-input" /></div>
                    <div style={{ marginTop: 10 }}>描述: <textarea id="edit-desc" defaultValue={record.description} className="ant-input" /></div>
                  </div>
                ),
                onOk: () => {
                  const newName = (document.getElementById('edit-name') as HTMLInputElement)?.value;
                  const newType = (document.getElementById('edit-type') as HTMLInputElement)?.value;
                  const newClass = (document.getElementById('edit-class') as HTMLInputElement)?.value;
                  const newCombination = (document.getElementById('edit-combination') as HTMLInputElement)?.value;
                  const newLimit = (document.getElementById('edit-limit') as HTMLInputElement)?.value;
                  const newRequest = (document.getElementById('edit-request') as HTMLTextAreaElement)?.value;
                  const newDesc = (document.getElementById('edit-desc') as HTMLTextAreaElement)?.value;
                  handleUpdateNode('environment', record.id, { 
                    name: newName, 
                    type: newType,
                    class: newClass,
                    combination: newCombination,
                    limit: newLimit,
                    request: newRequest,
                    description: newDesc 
                  });
                },
              });
            }}>编辑</Button>
            <Button size="small" danger icon={<DeleteOutlined />} onClick={() => handleDeleteNode('environment', record.id)}>删除</Button>
          </Space>
        ),
      },
    ];

  const testProjectColumns = [
    { title: 'ID', dataIndex: 'id', key: 'id', width: 100 },
    { title: '名称', dataIndex: 'name', key: 'name' },
    // { title: '所属设备', dataIndex: 'related_device', key: 'related_device' },
    { title: '描述', dataIndex: 'description', key: 'description' },
    {
      title: '操作',
      key: 'action',
      width: 150,
      render: (_: any, record: any) => (
        <Space>
          <Button size="small" icon={<EditOutlined />} onClick={() => {
            Modal.confirm({
              title: '编辑测试项目',
              content: (
                <div>
                  <div>名称: <input id="edit-name" defaultValue={record.name} className="ant-input" /></div>
                  <div style={{ marginTop: 10 }}>描述: <textarea id="edit-desc" defaultValue={record.description} className="ant-input" /></div>
                </div>
              ),
              onOk: () => {
                const newName = (document.getElementById('edit-name') as HTMLInputElement).value;
                const newDesc = (document.getElementById('edit-desc') as HTMLTextAreaElement).value;
                handleUpdateNode('test_project', record.id, { name: newName, description: newDesc });
              },
            });
          }}>编辑</Button>
          <Button size="small" danger icon={<DeleteOutlined />} onClick={() => handleDeleteNode('test_project', record.id)}>删除</Button>
        </Space>
      ),
    },
  ];

  const indicatorColumns = [
    { title: 'ID', dataIndex: 'id', key: 'id', width: 100 },
    { title: '名称', dataIndex: 'name', key: 'name' },
    // { title: '所属测试项目', dataIndex: 'related_test_project', key: 'related_test_project' },
    // { title: '单位', dataIndex: 'unit', key: 'unit' },
    // { title: '标准值', dataIndex: 'standard_value', key: 'standard_value' },
    { title: '描述', dataIndex: 'description', key: 'description' },
    {
      title: '操作',
      key: 'action',
      width: 150,
      render: (_: any, record: any) => (
        <Space>
          <Button size="small" icon={<EditOutlined />} onClick={() => {
            Modal.confirm({
              title: '编辑指标',
              content: (
                <div>
                  <div>名称: <input id="edit-name" defaultValue={record.name} className="ant-input" /></div>
                  <div style={{ marginTop: 10 }}>单位: <input id="edit-unit" defaultValue={record.unit} className="ant-input" /></div>
                  <div style={{ marginTop: 10 }}>标准值: <input id="edit-standard" defaultValue={record.standard_value} className="ant-input" /></div>
                  <div style={{ marginTop: 10 }}>描述: <textarea id="edit-desc" defaultValue={record.description} className="ant-input" /></div>
                </div>
              ),
              onOk: () => {
                const newName = (document.getElementById('edit-name') as HTMLInputElement).value;
                const newUnit = (document.getElementById('edit-unit') as HTMLInputElement).value;
                const newStandard = (document.getElementById('edit-standard') as HTMLInputElement).value;
                const newDesc = (document.getElementById('edit-desc') as HTMLTextAreaElement).value;
                handleUpdateNode('indicator', record.id, { 
                  name: newName, 
                  unit: newUnit, 
                  standard_value: newStandard,
                  description: newDesc 
                });
              },
            });
          }}>编辑</Button>
          <Button size="small" danger icon={<DeleteOutlined />} onClick={() => handleDeleteNode('indicator', record.id)}>删除</Button>
        </Space>
      ),
    },
  ];

  const REL_TYPE_LABEL: Record<string, string> = {
    CON: '关联',
    HAS: '包含',
    BELONG_TO: '属于',
  };

  const relationshipColumns = [
    { title: '源类型', dataIndex: 'from_type', key: 'from_type', width: 120 },
    { title: '源名称', dataIndex: 'from_name', key: 'from_name' },
    {
      title: '关系',
      dataIndex: 'relation',
      key: 'relation',
      width: 100,
      render: (_: unknown, record: { relation?: string; type?: string }) => {
        const t = record?.relation || record?.type;
        return (t && REL_TYPE_LABEL[t as string]) || t || '—';
      },
    },
    { title: '目标类型', dataIndex: 'to_type', key: 'to_type', width: 120 },
    { title: '目标名称', dataIndex: 'to_name', key: 'to_name' },
  ];

  const filteredRelationships = useMemo(() => {
    const list = parsedData?.relationships || [];
    if (relTypeFilter === 'all') return list;
    return list.filter((r: { relation?: string; type?: string }) => (r.relation || r.type) === relTypeFilter);
  }, [parsedData?.relationships, relTypeFilter]);

  const tabItems = useMemo(
    () => [
    {
      key: 'equipment_types',
      label: `设备类型 (${parsedData?.equipment_types.length || 0})`,
      children: <Table columns={equipmentTypeColumns} dataSource={parsedData?.equipment_types || []} rowKey="id" size="small" pagination={{ pageSize: 10 }} />,
    },
    {
      key: 'equipments',
      label: `设备 (${parsedData?.equipments.length || 0})`,
      children: <Table columns={equipmentColumns} dataSource={parsedData?.equipments || []} rowKey="id" size="small" pagination={{ pageSize: 10 }} />,
    },
    {
          key: 'environments',  // 新增环境 Tab
          label: `环境 (${parsedData?.environments?.length || 0})`,
          children: <Table columns={environmentColumns} dataSource={parsedData?.environments || []} rowKey="id" size="small" pagination={{ pageSize: 10 }} />,
        },
    {
      key: 'test_projects',
      label: `测试项目 (${parsedData?.test_projects.length || 0})`,
      children: <Table columns={testProjectColumns} dataSource={parsedData?.test_projects || []} rowKey="id" size="small" pagination={{ pageSize: 10 }} />,
    },
    {
      key: 'indicators',
      label: `指标 (${parsedData?.indicators.length || 0})`,
      children: <Table columns={indicatorColumns} dataSource={parsedData?.indicators || []} rowKey="id" size="small" pagination={{ pageSize: 10 }} />,
    },
    {
      key: 'relationships',
      label: `关系 (${parsedData?.relationships.length || 0})`,
      children: (
        <div>
          <Space style={{ marginBottom: 12 }} wrap>
            <Text type="secondary">按关系类型筛选</Text>
            <Select
              value={relTypeFilter}
              onChange={setRelTypeFilter}
              style={{ width: 180 }}
              options={[
                { value: 'all', label: '全部' },
                { value: 'CON', label: '关联 (CON)' },
                { value: 'HAS', label: '包含 (HAS)' },
                { value: 'BELONG_TO', label: '属于 (BELONG_TO)' },
              ]}
            />
          </Space>
          <Table
            columns={relationshipColumns}
            dataSource={filteredRelationships}
            rowKey={(record, index) => `${(record as { from_id?: string }).from_id}-${(record as { to_id?: string }).to_id}-${(record as { relation?: string }).relation}-${index}`}
            size="small"
            pagination={{ pageSize: 10 }}
          />
        </div>
      ),
    },
  ],
    [parsedData, filteredRelationships, relTypeFilter],
  );

  useEffect(() => {
    if (parsedData?.task_id) setRelTypeFilter('all');
  }, [parsedData?.task_id]);

  return (
    <Layout style={{ minHeight: '100vh', background: '#f0f2f5' }}>
      {/* 顶部工具栏 */}
      <Header style={{ background: '#fff', padding: '0 24px', borderBottom: '1px solid #e8e8e8', height: 64 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', height: '100%' }}>
          <div style={{ display: 'flex', alignItems: 'center' }}>
            <Title level={4} style={{ margin: 0, marginRight: 24 }}>分类管理系统</Title>
            <Space size="middle">
              <Upload
                beforeUpload={handleFileUpload}
                showUploadList={false}
                accept=".doc,.docx"
              >
                <Button 
                  icon={<UploadOutlined />} 
                  loading={uploading}
                  type="primary"
                >
                  上传Word文档
                </Button>
              </Upload>
              
              <Button 
                icon={<EyeOutlined />} 
                onClick={handleViewResult}
                disabled={!currentTaskId}
              >
                查看解析结果
              </Button>
              
              <Button 
                icon={<DatabaseOutlined />} 
                onClick={handleImportToNeo4j}
                loading={importing}
                disabled={!currentTaskId}
                type="default"
              >
                导入Neo4j
              </Button>
              
              <Button 
                icon={<ReloadOutlined />} 
                onClick={handleRefresh}
              >
                刷新
              </Button>
            </Space>
          </div>
          
          <div>
            <Text type="secondary">
              {currentTaskId ? `当前任务: ${currentTaskId}` : '未上传文件'}
            </Text>
          </div>
        </div>
      </Header>

      <Layout>
        {/* 类型选择栏 */}
        <Sider
          width={typeCollapsed ? 48 : 200}
          theme="light"
          collapsible
          collapsed={typeCollapsed}
          onCollapse={setTypeCollapsed}
          collapsedWidth={48}
          trigger={null}
          style={{ borderRight: '1px solid #e8e8e8', background: '#fff' }}
        >
          <div style={{ padding: '12px 8px', display: 'flex', alignItems: 'center', justifyContent: typeCollapsed ? 'center' : 'space-between' }}>
            {!typeCollapsed && <Title level={5} style={{ margin: 0, fontSize: 14 }}>节点类型</Title>}
            <Button
              type="text"
              size="small"
              icon={typeCollapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
              onClick={() => setTypeCollapsed(!typeCollapsed)}
              style={{ color: '#999' }}
            />
          </div>

          <Divider style={{ margin: '4px 0' }} />
          <div style={{ padding: '0 4px' }}>
            {NODE_TYPES.map(t => (
              <div
                key={t.key}
                onClick={() => { setSelectedType(t.key); setSelectedCategory(null); }}
                title={t.label}
                style={{
                  padding: typeCollapsed ? '8px 0' : '8px 12px',
                  cursor: 'pointer', borderRadius: 6, marginBottom: 4,
                  background: selectedType === t.key ? '#e6f0ff' : 'transparent',
                  color: selectedType === t.key ? '#235cff' : 'inherit',
                  fontWeight: selectedType === t.key ? 600 : 400,
                  display: 'flex', alignItems: 'center', justifyContent: typeCollapsed ? 'center' : 'flex-start',
                  gap: 8, transition: 'all 0.2s',
                }}
              >
                {t.icon}
                {!typeCollapsed && <span style={{ fontSize: 14 }}>{t.label}</span>}
              </div>
            ))}
          </div>
        </Sider>

        {/* 分类树栏 */}
        {showTreeSidebar && (
          <Sider
            width={treeCollapsed ? 48 : 220}
            theme="light"
            collapsible
            collapsed={treeCollapsed}
            onCollapse={setTreeCollapsed}
            collapsedWidth={48}
            trigger={null}
            style={{ borderRight: '1px solid #e8e8e8', background: '#fff' }}
          >
            <div style={{ padding: '12px 8px', display: 'flex', alignItems: 'center', justifyContent: treeCollapsed ? 'center' : 'space-between' }}>
              {!treeCollapsed && <Title level={5} style={{ margin: 0, fontSize: 14 }}>{currentType?.label}</Title>}
              <Button
                type="text" size="small"
                icon={treeCollapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
                onClick={() => setTreeCollapsed(!treeCollapsed)}
                style={{ color: '#999' }}
              />
            </div>
            {!treeCollapsed && (
              <>
                <Divider style={{ margin: '4px 0' }} />
                <div style={{ padding: '0 8px', overflow: 'auto', maxHeight: 'calc(100vh - 100px)' }}>
                  {treeLoading ? (
                    <div style={{ textAlign: 'center', padding: 40 }}><Spin size="small" /></div>
                  ) : treeData.length === 0 ? (
                    <Empty description="无分类" image={Empty.PRESENTED_IMAGE_SIMPLE} style={{ marginTop: 40 }} />
                  ) : (
                    <Tree
                      showLine
                      defaultExpandAll
                      treeData={treeData}
                      selectedKeys={selectedCategory ? [selectedCategory] : []}
                      onSelect={(keys) => setSelectedCategory(keys[0] as string || null)}
                      style={{ background: 'transparent' }}
                    />
                  )}
                </div>
              </>
            )}
          </Sider>
        )}

        <Content style={{ padding: 16, overflow: 'auto' }}>
          {renderContent()}
        </Content>
      </Layout>

      {/* 解析结果查看抽屉 */}
      <Drawer
        title="文档解析结果"
        placement="right"
        size="large"
        open={resultDrawerVisible}
        onClose={() => setResultDrawerVisible(false)}
        extra={
          <Space>
            <Button onClick={() => setResultDrawerVisible(false)}>关闭</Button>
          </Space>
        }
      >
        {parsedData && (
          <Tabs activeKey={activeTab} onChange={setActiveTab} items={tabItems} />
        )}
      </Drawer>

      <Modal
        title="存在重复节点名称，请选择导入策略"
        open={importConflictOpen}
        width={960}
        onCancel={() => { setImportConflictOpen(false); }}
        onOk={submitImportWithResolutions}
        okText="按选择导入"
        destroyOnHidden
        confirmLoading={importing}
        bodyStyle={{ maxHeight: '70vh', overflow: 'auto' }}
      >
        <p style={{ marginBottom: 12, color: '#666' }}>
          与图库中已有节点（同类型且同名）在「待导入」与图库中的 Id 不一致时，可选<strong>替换图库</strong>（会删除原节点及关联关系后写入新数据）；
          本批文件内多行同名则选<strong>跳过</strong>不导入或<strong>重命名</strong>以区分。
        </p>
        <Table
          size="small"
          pagination={false}
          scroll={{ x: 880 }}
          rowKey="conflict_id"
          dataSource={importConflictRows.map(({ row, src }) => ({ ...row, _src: src }))}
          columns={[
            {
              title: '来源',
              width: 100,
              dataIndex: '_src',
              render: (s: 'db' | 'file') =>
                s === 'db' ? <Tag color="blue">图库已有重名</Tag> : <Tag>本批文件重名</Tag>,
            },
            { title: '类型', dataIndex: 'node_type', width: 100, render: (t) => flglNodeTypeLabel(t) },
            { title: '名称', dataIndex: 'import_name', width: 120, ellipsis: true },
            { title: '待导入 Id', dataIndex: 'import_id', width: 100, ellipsis: true },
            {
              title: '说明',
              width: 220,
              render: (_: any, r: any) => {
                if (r._src === 'db' && r.existing?.length) {
                  return <Text type="secondary">图库: {r.existing.map((e: any) => e.id).join(', ')}</Text>;
                }
                if (r._src === 'file' && r.peer_import_ids?.length) {
                  return <Text type="secondary">同批 Id: {r.peer_import_ids.join(', ')}</Text>;
                }
                return null;
              },
            },
            {
              title: '操作',
              width: 130,
              render: (_: any, r: any) => (
                <Select
                  style={{ width: 120 }}
                  value={importResolutionState[r.conflict_id]?.action ?? 'skip'}
                  onChange={(v) =>
                    setImportResolutionState((p) => ({
                      ...p,
                      [r.conflict_id]: { action: v, newName: p[r.conflict_id]?.newName || '' },
                    }))
                  }
                  options={
                    r._src === 'db'
                      ? [
                          { value: 'replace', label: '替换图库' },
                          { value: 'skip', label: '跳过' },
                          { value: 'rename', label: '重命名' },
                        ]
                      : [
                          { value: 'skip', label: '跳过' },
                          { value: 'rename', label: '重命名' },
                        ]
                  }
                />
              ),
            },
            {
              title: '新名称',
              width: 180,
              render: (_: any, r: any) =>
                importResolutionState[r.conflict_id]?.action === 'rename' ? (
                  <Input
                    size="small"
                    placeholder="重命名后的名称"
                    value={importResolutionState[r.conflict_id]?.newName || ''}
                    onChange={(e) =>
                      setImportResolutionState((p) => ({
                        ...p,
                        [r.conflict_id]: {
                          action: 'rename',
                          newName: e.target.value,
                        },
                      }))
                    }
                  />
                ) : (
                  <Text type="secondary">—</Text>
                ),
            },
          ]}
        />
      </Modal>
    </Layout>
  );
}

export const dynamic = 'force-dynamic';