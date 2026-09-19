'use client';

import React, { useState, useEffect, useMemo, useCallback, useRef, useLayoutEffect } from 'react';
import { useSearchParams } from 'next/navigation';
import { Layout, Tree, Typography, Divider, Spin, Empty, Button, Space, message, Upload, Modal, Table, Tabs, Drawer, Select, Input, Tag, Alert, Form, Card } from 'antd';
import {
  AppstoreOutlined, EnvironmentOutlined, ExperimentOutlined,
  BarChartOutlined, FileTextOutlined,
  ThunderboltOutlined, ReloadOutlined, MenuFoldOutlined, MenuUnfoldOutlined,
  UploadOutlined, EyeOutlined, DatabaseOutlined, DeleteOutlined, EditOutlined, SearchOutlined,
} from '@ant-design/icons';
import MixedContent from '@/components/ThreeColumnLayout/MixedContent';
import MathAwareTextArea from '@/components/MathAwareTextArea';
import EquipmentTypePanel from './components/EquipmentTypePanel';
import EquipmentPanel from './components/EquipmentPanel';
import EnvironmentPanel from './components/EnvironmentPanel';
import ProjectPanel from './components/ProjectPanel';
import IndicatorPanel from './components/IndicatorPanel';
import MethodPanel from './components/MethodPanel';
import ParsedNodeQuickEditModal, { type ParsedQuickEditKind } from './components/ParsedNodeQuickEditModal';
import { fetchNodes, fetchEquipmentTree, type NodeType, type GraphNode, type EquipmentTreeNode } from './api';
import {
  buildFlglSidebarTreeData,
  collectTreeKeys,
  equipmentIdsFromTreeSelection,
  filterEquipmentTreeByKeyword,
  findEquipmentTreePath,
  selectedEquipmentIdFromCategory,
  selectedEquipmentTypeIdFromCategory,
  selectedLevel1FromCategory,
  withSearchSelectability,
} from './utils/equipmentTreeUtils';

/** 将树节点滚到侧栏滚动容器顶部（定位装备时用） */
function scrollEquipmentTreeNodeToTop(container: HTMLElement, nodeKey: string, paddingTop = 8): boolean {
  const esc = typeof CSS !== 'undefined' && CSS.escape ? CSS.escape(nodeKey) : nodeKey.replace(/"/g, '\\"');
  const content = container.querySelector(`[data-key="${esc}"]`) as HTMLElement | null;
  const treenode = (content?.closest('.ant-tree-treenode') || container.querySelector('.ant-tree-treenode-selected')) as HTMLElement | null;
  if (!treenode) return false;
  const containerRect = container.getBoundingClientRect();
  const nodeRect = treenode.getBoundingClientRect();
  container.scrollTop += nodeRect.top - containerRect.top - paddingTop;
  return true;
}

const DIRECT_BACKEND = process.env.NEXT_PUBLIC_BACKEND_URL || 'http://localhost:5000';

const { Sider, Content, Header } = Layout;
const { Title, Text } = Typography;

function treeTipForTab(tab: string): string {
  if (tab === 'Environment') return '环境管理与设备树无关';
  return '设备树按一级设备类别 → 二级设备类型 → 三级设备完整展示';
}

// 定义一个常量数组，包含了系统中所有节点类型的信息，包括唯一键、显示标签和对应的图标组件
const NODE_TYPES: Array<{ key: string; label: string; icon: React.ReactNode }> = [
  { key: 'EquipmentType', label: '设备类型', icon: <AppstoreOutlined /> },
  { key: 'Equipment', label: '设备', icon: <ThunderboltOutlined /> },
  { key: 'Environment', label: '环境', icon: <EnvironmentOutlined /> },
  { key: 'TestProject', label: '项目', icon: <ExperimentOutlined /> },
  { key: 'Indicator', label: '指标', icon: <BarChartOutlined /> },
  { key: 'TestMethod', label: '方法', icon: <FileTextOutlined /> },
  // { key: 'File', label: '文件' },
];

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
  const searchParams = useSearchParams();
  const autoOpen = searchParams.get('autoOpen') === '1';
  const defaultTab = searchParams.get('tab');

  // 定义组件的状态变量，存储和管理界面中会变化的数据
  const [selectedType, setSelectedType] = useState<string>(defaultTab === 'method' ? 'TestMethod' : 'EquipmentType');
  const [selectedCategory, setSelectedCategory] = useState<string | null>(null);
  const [treeLoading, setTreeLoading] = useState(false);
  const [allNodes, setAllNodes] = useState<GraphNode[]>([]);
  const [categoryNodes, setCategoryNodes] = useState<GraphNode[]>([]);
  const [equipmentTree, setEquipmentTree] = useState<EquipmentTreeNode[]>([]);
  const [selectedProjectEnvironment, setSelectedProjectEnvironment] = useState<string | null>(null);
  const [environmentOptions, setEnvironmentOptions] = useState<GraphNode[]>([]);
  const [treeCollapsed, setTreeCollapsed] = useState(false);
  const [treeExpandedKeys, setTreeExpandedKeys] = useState<React.Key[]>([]);
  const [treeExpandedInited, setTreeExpandedInited] = useState(false);
  const [equipmentTreeSearch, setEquipmentTreeSearch] = useState('');
  const equipmentTreeScrollRef = useRef<HTMLDivElement>(null);
  /** 列表点击定位时待滚动的装备 key；定位期间不触发「清空检索后全展开」 */
  const pendingTreeScrollKeyRef = useRef<string | null>(null);
  const [uploading, setUploading] = useState(false);
  
  // 环境编辑抽屉状态
  const [envEditDrawerVisible, setEnvEditDrawerVisible] = useState(false);
  const [envEditRecord, setEnvEditRecord] = useState<any>(null);
  const [envEditForm] = Form.useForm();
  const [envFiveElements, setEnvFiveElements] = useState<any>(null);
  const [envFiveLoading, setEnvFiveLoading] = useState(false);
  
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
  /** 解析结果表格内联编辑（设备类型/设备/项目/指标） */
  const [quickEdit, setQuickEdit] = useState<{ kind: ParsedQuickEditKind; record: Record<string, unknown> } | null>(null);

  useEffect(() => {
    loadTreeData();
  }, [selectedType]);

  const treeData = useMemo(() => buildFlglSidebarTreeData(equipmentTree), [equipmentTree]);
  const filteredTreeData = useMemo(() => {
    const filtered = filterEquipmentTreeByKeyword(treeData, equipmentTreeSearch);
    return withSearchSelectability(filtered, equipmentTreeSearch);
  }, [treeData, equipmentTreeSearch]);

  const locateEquipmentInTree = useCallback((equipmentId: string) => {
    const path = findEquipmentTreePath(equipmentTree, equipmentId);
    if (!path) {
      message.warning('左侧设备树中未找到该装备');
      return;
    }
    pendingTreeScrollKeyRef.current = path.equipmentKey;
    setTreeCollapsed(false);
    setEquipmentTreeSearch('');
    setTreeExpandedKeys([path.level1Key, path.eqtypeKey]);
    setSelectedCategory(path.equipmentKey);
  }, [equipmentTree]);

  useLayoutEffect(() => {
    const targetKey = pendingTreeScrollKeyRef.current;
    if (!targetKey || selectedCategory !== targetKey) return;

    const container = equipmentTreeScrollRef.current;
    if (!container) return;

    let cancelled = false;
    const run = (attempt = 0) => {
      if (cancelled) return;
      const ok = scrollEquipmentTreeNodeToTop(container, targetKey);
      if (ok) {
        pendingTreeScrollKeyRef.current = null;
        return;
      }
      if (attempt < 12) {
        requestAnimationFrame(() => run(attempt + 1));
      } else {
        pendingTreeScrollKeyRef.current = null;
      }
    };
    requestAnimationFrame(() => run());
    return () => { cancelled = true; };
  }, [selectedCategory, treeExpandedKeys, filteredTreeData, treeCollapsed]);
  const allTreeKeys = useMemo(() => collectTreeKeys(treeData), [treeData]);
  const filteredTreeKeys = useMemo(() => collectTreeKeys(filteredTreeData), [filteredTreeData]);

  // 首次加载完成后默认全部展开；后续刷新保留用户手动折叠状态
  useEffect(() => {
    if (treeData.length > 0 && !treeExpandedInited) {
      setTreeExpandedKeys(allTreeKeys);
      setTreeExpandedInited(true);
    }
  }, [treeData, allTreeKeys, treeExpandedInited]);

  // 检索时展开所有命中分支，清空检索后恢复全展开（若用户尚未手动折叠过）
  useEffect(() => {
    const kw = equipmentTreeSearch.trim();
    if (kw) {
      setTreeExpandedKeys(filteredTreeKeys);
    } else if (treeExpandedInited && !pendingTreeScrollKeyRef.current) {
      setTreeExpandedKeys(allTreeKeys);
    }
  }, [equipmentTreeSearch, filteredTreeKeys, allTreeKeys, treeExpandedInited]);

  useEffect(() => {
    if (selectedType !== 'TestProject') {
      setSelectedProjectEnvironment(null);
    }
    // 切换到 Environment tab 时自动收起设备树，其他 tab 展开
    setTreeCollapsed(selectedType === 'Environment');
  }, [selectedType]);

  // 左侧始终是设备树；右侧顶部 Tab 决定当前表格/表单内容
  const loadTreeData = async () => {
    setTreeLoading(true);
    try {
      const [nodes, eqTree, eqTypes] = await Promise.all([
        fetchNodes(selectedType as NodeType).catch(() => []),
        fetchEquipmentTree().catch(() => []),
        fetchNodes('EquipmentType').catch(() => []),
      ]);
      const list = Array.isArray(nodes) ? nodes : [];
      const equipmentTreeList = Array.isArray(eqTree) ? eqTree : [];
      const equipmentTypeList = Array.isArray(eqTypes) ? eqTypes : [];

      setAllNodes(list);
      setEquipmentTree(equipmentTreeList);
      setCategoryNodes(equipmentTypeList);

      if (selectedType === 'TestProject') {
        const envNodes = await fetchNodes('Environment').catch(() => []);
        setEnvironmentOptions(Array.isArray(envNodes) ? envNodes : []);
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

  const parseResponseJsonSafely = async (response: Response, fallback: string) => {
    const text = await response.text();
    try {
      return JSON.parse(text);
    } catch {
      const preview = text.replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 300);
      throw new Error(preview || `${fallback}（HTTP ${response.status}）`);
    }
  };

  // 上传处理函数
  const handleFileUpload = async (file: File) => {
    setUploading(true);
    const hideLoading = message.loading('正在上传并解析文件...', 0);

    try {
      const formData = new FormData();
      formData.append('file', file);

      const response = await fetch(`${DIRECT_BACKEND}/api/FLGL/upload`, {
        method: 'POST',
        body: formData,
      });

      const result = await parseResponseJsonSafely(response, '上传接口返回了非 JSON 内容');
      if (!response.ok) {
        throw new Error(result.message || result.error || `上传请求失败（HTTP ${response.status}）`);
      }
      
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
      const response = await fetch(`${DIRECT_BACKEND}/api/FLGL/parse-result/${taskId}`);
      const result = await parseResponseJsonSafely(response, '解析结果接口返回了非 JSON 内容');
      if (!response.ok) {
        throw new Error(result.message || result.error || `获取解析结果失败（HTTP ${response.status}）`);
      }
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

  // 获取环境五要素数据
  const fetchEnvFiveElements = async (envName: string) => {
    setEnvFiveLoading(true);
    try {
      const res = await fetch(`/api/GLCX/JQCX?path=/environment-settings-all`);
      const json = await res.json();
      if (json.success && json.data) {
        const found = json.data.find((item: any) => item.env_name === envName);
        setEnvFiveElements(found || null);
      } else {
        setEnvFiveElements(null);
      }
    } catch {
      setEnvFiveElements(null);
    } finally {
      setEnvFiveLoading(false);
    }
  };

  // 打开环境编辑抽屉
  const handleOpenEnvEditDrawer = (record: any) => {
    setEnvEditRecord(record);
    envEditForm.setFieldsValue({
      name: record.name,
      type: record.type,
      class: record.class,
      combination: record.combination,
      limit: record.limit,
      request: record.request,
      description: record.description,
    });
    setEnvEditDrawerVisible(true);
    fetchEnvFiveElements(record.name);
  };

  // 保存环境编辑
  const handleSaveEnvEdit = async () => {
    try {
      const values = await envEditForm.validateFields();
      await handleUpdateNode('environment', envEditRecord.id, values);
      setEnvEditDrawerVisible(false);
      setEnvEditRecord(null);
    } catch {
      // validation failed
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

  const openQuickEdit = useCallback((kind: ParsedQuickEditKind, record: Record<string, unknown>) => {
    setQuickEdit({ kind, record });
  }, []);

  const handleQuickEditSave = useCallback(
    async (values: { name: string; description?: string; unit?: string; standard_value?: string }) => {
      if (!quickEdit) return;
      const { kind, record } = quickEdit;
      const nodeId = String(record.id ?? '');
      const nodeTypeMap: Record<ParsedQuickEditKind, string> = {
        equipment_type: 'equipment_type',
        equipment: 'equipment',
        test_project: 'test_project',
        indicator: 'indicator',
      };
      const payload =
        kind === 'equipment_type'
          ? { name: values.name }
          : kind === 'indicator'
            ? {
                name: values.name,
                unit: values.unit,
                standard_value: values.standard_value,
                description: values.description,
              }
            : { name: values.name, description: values.description };
      await handleUpdateNode(nodeTypeMap[kind], nodeId, payload);
      setQuickEdit(null);
    },
    [quickEdit],
  );

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
    const selectedEquipmentId = selectedEquipmentIdFromCategory(selectedCategory);
    const selectedEquipmentTypeId = selectedEquipmentTypeIdFromCategory(selectedCategory);
    const selectedLevel1 = selectedLevel1FromCategory(selectedCategory);
    const selectedEquipmentIds = equipmentIdsFromTreeSelection(selectedCategory, equipmentTree);
    const props = {
      allNodes,
      selectedCategory,
      selectedEquipmentId,
      selectedEquipmentIds,
      selectedEquipmentTypeId,
      selectedLevel1,
      selectedProjectEnvironment,
      categoryNodes,
      equipmentTree,
      onRefresh: loadTreeData,
      onLocateEquipment: locateEquipmentInTree,
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
        return <MethodPanel {...props} autoOpen={autoOpen} />;
      default:
        return <Empty description="请选择节点类型" />;
    }
  };

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
          <Button size="small" icon={<EditOutlined />} onClick={() => openQuickEdit('equipment_type', record)}>编辑</Button>
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
          <Button size="small" icon={<EditOutlined />} onClick={() => openQuickEdit('equipment', record)}>编辑</Button>
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
            <Button size="small" icon={<EditOutlined />} onClick={() => handleOpenEnvEditDrawer(record)}>编辑</Button>
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
          <Button size="small" icon={<EditOutlined />} onClick={() => openQuickEdit('test_project', record)}>编辑</Button>
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
          <Button size="small" icon={<EditOutlined />} onClick={() => openQuickEdit('indicator', record)}>编辑</Button>
          <Button size="small" danger icon={<DeleteOutlined />} onClick={() => handleDeleteNode('indicator', record.id)}>删除</Button>
        </Space>
      ),
    },
  ];

  const REL_TYPE_LABEL: Record<string, string> = {
    CON: '关联',
    HAS: '包含',
    BELONGS_TO: '属于',
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
                { value: 'CON', label: '关联（CON）' },
                { value: 'HAS', label: '包含（HAS）' },
                { value: 'BELONGS_TO', label: '属于（BELONGS_TO）' },
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
        {/* 左侧固定设备树 */}
        <Sider
          width={treeCollapsed ? 48 : 300}
          theme="light"
          collapsible
          collapsed={treeCollapsed}
          onCollapse={setTreeCollapsed}
          collapsedWidth={48}
          trigger={null}
          style={{ borderRight: '1px solid #e8e8e8', background: '#fff' }}
        >
          <div style={{ padding: '12px 8px', display: 'flex', alignItems: 'center', justifyContent: treeCollapsed ? 'center' : 'space-between' }}>
            {!treeCollapsed && <Title level={5} style={{ margin: 0, fontSize: 14 }}>设备树</Title>}
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
              <div
                ref={equipmentTreeScrollRef}
                style={{ padding: '0 8px', overflow: 'auto', maxHeight: 'calc(100vh - 100px)' }}
              >
                {treeLoading ? (
                  <div style={{ textAlign: 'center', padding: 40 }}><Spin size="small" /></div>
                ) : treeData.length === 0 ? (
                  <Empty description="无设备树数据" image={Empty.PRESENTED_IMAGE_SIMPLE} style={{ marginTop: 40 }} />
                ) : (
                  <>
                    <Input
                      allowClear
                      placeholder="按名称检索设备"
                      prefix={<SearchOutlined style={{ color: '#bfbfbf' }} />}
                      value={equipmentTreeSearch}
                      onChange={(e) => setEquipmentTreeSearch(e.target.value)}
                      style={{ marginBottom: 8 }}
                    />
                    <Text type="secondary" style={{ display: 'block', marginBottom: 8, fontSize: 12 }}>
                      {treeTipForTab(selectedType)}
                    </Text>
                    {filteredTreeData.length === 0 ? (
                      <Empty description="无匹配设备" image={Empty.PRESENTED_IMAGE_SIMPLE} style={{ marginTop: 24 }} />
                    ) : (
                      <Tree
                        showLine
                        expandedKeys={treeExpandedKeys}
                        onExpand={(keys) => setTreeExpandedKeys(keys)}
                        treeData={filteredTreeData}
                        selectedKeys={selectedCategory ? [selectedCategory] : []}
                        onSelect={(keys) => {
                          const key = String(keys[0] || '');
                          if (!key) return;
                          if (equipmentTreeSearch.trim() && !key.startsWith('equipment_')) return;
                          setSelectedCategory(key);
                        }}
                        style={{ background: 'transparent' }}
                      />
                    )}
                  </>
                )}
                {selectedType === 'TestProject' && !treeLoading && (
                  <div style={{ marginTop: 16 }}>
                    <Divider style={{ margin: '12px 0' }} />
                    <Title level={5} style={{ fontSize: 14, marginBottom: 8 }}>关联环境分类</Title>
                    <Text type="secondary" style={{ display: 'block', marginBottom: 8, fontSize: 12 }}>
                      项目表格按设备 + 环境共同过滤
                    </Text>
                    <Select
                      allowClear
                      showSearch
                      placeholder="选择环境"
                      style={{ width: '100%' }}
                      value={selectedProjectEnvironment || undefined}
                      optionFilterProp="label"
                      options={environmentOptions.map((env) => ({ label: env.name || env.id, value: env.id }))}
                      onChange={(v) => setSelectedProjectEnvironment((v as string) || null)}
                    />
                  </div>
                )}
              </div>
            </>
          )}
        </Sider>

        <Content style={{ padding: 16, overflow: 'auto' }}>
          <div style={{ background: '#fff', borderRadius: 8, padding: '8px 12px 0', marginBottom: 12 }}>
            <Tabs
              activeKey={selectedType}
              onChange={(key) => { setSelectedType(key); }}
              items={NODE_TYPES.map((t) => ({
                key: t.key,
                label: (
                  <span>
                    {t.icon} {t.label}
                  </span>
                ),
              }))}
            />
          </div>
          {selectedType === 'Environment' && (
            <Alert type="info" showIcon message="环境管理与设备树无关；可直接维护环境数据。" style={{ marginBottom: 12 }} />
          )}
          {renderContent()}
        </Content>
      </Layout>

      {/* 环境编辑抽屉 */}
      <Drawer
        title={`编辑环境 - ${envEditRecord?.name || ''}`}
        placement="right"
        size={800}
        open={envEditDrawerVisible}
        onClose={() => {
          setEnvEditDrawerVisible(false);
          setEnvEditRecord(null);
          setEnvFiveElements(null);
        }}
        extra={
          <Space>
            <Button onClick={() => {
              setEnvEditDrawerVisible(false);
              setEnvEditRecord(null);
              setEnvFiveElements(null);
            }}>取消</Button>
            <Button type="primary" onClick={handleSaveEnvEdit}>保存</Button>
          </Space>
        }
      >
        {envEditRecord && (
          <div style={{ display: 'flex', gap: 16 }}>
            {/* 左侧：编辑表单 */}
            <Card title="基本信息" style={{ flex: 1 }}>
              <Form
                form={envEditForm}
                layout="vertical"
                initialValues={envEditRecord}
              >
                <Form.Item label="环境名称" name="name" rules={[{ required: true, message: '请输入环境名称' }]}>
                  <Input />
                </Form.Item>
                <Form.Item label="类型" name="type">
                  <Input />
                </Form.Item>
                <Form.Item label="类别" name="class">
                  <Input />
                </Form.Item>
                <Form.Item label="组合条件" name="combination">
                  <Input />
                </Form.Item>
                <Form.Item label="限制条件" name="limit">
                  <MathAwareTextArea rows={3} />
                </Form.Item>
                <Form.Item label="试验要求" name="request">
                  <MathAwareTextArea rows={3} />
                </Form.Item>
                <Form.Item label="描述" name="description">
                  <MathAwareTextArea rows={3} />
                </Form.Item>
              </Form>
            </Card>

            {/* 右侧：五要素展示 */}
            <Card
              title="环境五要素"
              style={{ flex: 1 }}
              extra={envFiveLoading ? <Spin size="small" /> : null}
            >
              {envFiveElements ? (
                <Tabs
                  tabPosition="left"
                  size="small"
                  items={[
                    { key: 'type', label: '分类', children: <div style={{ maxHeight: 400, overflow: 'auto' }}><MixedContent content={envFiveElements.type || ''} /></div> },
                    { key: 'class', label: '分级', children: <div style={{ maxHeight: 400, overflow: 'auto' }}><MixedContent content={envFiveElements.class || ''} /></div> },
                    { key: 'combination', label: '组合', children: <div style={{ maxHeight: 400, overflow: 'auto' }}><MixedContent content={envFiveElements.combination || ''} /></div> },
                    { key: 'limit', label: '约束', children: <div style={{ maxHeight: 400, overflow: 'auto' }}><MixedContent content={envFiveElements.limit || ''} /></div> },
                    { key: 'request', label: '要求', children: <div style={{ maxHeight: 400, overflow: 'auto' }}><MixedContent content={envFiveElements.request || ''} /></div> },
                  ]}
                />
              ) : (
                <div style={{ textAlign: 'center', padding: 40, color: '#999' }}>
                  {envFiveLoading ? '加载中...' : '暂无五要素数据'}
                </div>
              )}
            </Card>
          </div>
        )}
      </Drawer>

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
        styles={{ body: { maxHeight: '70vh', overflow: 'auto' } }}
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

      <ParsedNodeQuickEditModal
        open={!!quickEdit}
        kind={quickEdit?.kind ?? 'equipment'}
        record={quickEdit?.record ?? null}
        onCancel={() => setQuickEdit(null)}
        onSave={handleQuickEditSave}
      />
    </Layout>
  );
}