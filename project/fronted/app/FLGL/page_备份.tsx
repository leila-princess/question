'use client';

import React, { useState, useEffect } from 'react';
import { Layout, Tree, Typography, Divider, Spin, Empty, Button, Space, message, Upload } from 'antd';
import {
  AppstoreOutlined, EnvironmentOutlined, ExperimentOutlined,
  BarChartOutlined, FileTextOutlined, FileOutlined,
  ThunderboltOutlined, ReloadOutlined, MenuFoldOutlined, MenuUnfoldOutlined,
  UploadOutlined
} from '@ant-design/icons';
import type { DataNode } from 'antd/es/tree';
import EquipmentTypePanel from './components/EquipmentTypePanel';
import EquipmentPanel from './components/EquipmentPanel';
import EnvironmentPanel from './components/EnvironmentPanel';
import ProjectPanel from './components/ProjectPanel';
import IndicatorPanel from './components/IndicatorPanel';
import MethodPanel from './components/MethodPanel';
import FilePanel from './components/FilePanel';
import { fetchNodes, type NodeType, type GraphNode } from './api';

const { Sider, Content } = Layout;
const { Title, Text } = Typography;

const ENVIRONMENTS = ['复杂电磁环境', '复杂地理环境', '复杂气象环境', '复杂水文环境', '复杂水声环境', '高原综合环境', '海上综合环境', '其他环节'];
const ENV_MAP: Record<string, string> = {
  'HJ_0001': '复杂电磁环境', 'HJ_0002': '复杂地理环境', 'HJ_0003': '复杂气象环境',
  'HJ_0004': '复杂水文环境', 'HJ_0005': '复杂水声环境', 'HJ_0006': '高原综合环境',
  'HJ_0007': '海上综合环境', 'HJ_0008': '其他环节',
};

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
  { key: 'File', label: '文件', icon: <FileOutlined /> },
];

const NO_SIDEBAR_TYPES = new Set(['Environment']);

export default function FLGLPage() {
  // 定义组件的状态变量，存储和管理界面中会变化的数据
  const [selectedType, setSelectedType] = useState<string>('EquipmentType');  //当前选中节点类型
  const [selectedCategory, setSelectedCategory] = useState<string | null>(null);  //当前在分类树中选中分类
  const [treeData, setTreeData] = useState<DataNode[]>([]);  //存储分类数的展示数据
  const [treeLoading, setTreeLoading] = useState(false);  //标记分类树是否正在加载数据
  const [allNodes, setAllNodes] = useState<GraphNode[]>([]);  //存储从后端获取的所有节点数据
  const [categoryNodes, setCategoryNodes] = useState<GraphNode[]>([]);  //存储当前分类下的节点数据，主要用于设备分类显示
  const [typeCollapsed, setTypeCollapsed] = useState(false);  //标记类型选择栏是否折叠
  const [treeCollapsed, setTreeCollapsed] = useState(false);  //标记分类树栏是否折叠
  const [uploading, setUploading] = useState(false);  //标记是否正在上传文件

  useEffect(() => {
    loadTreeData();
  }, [selectedType]);  // 当selectedType变化时，自动重新加载分类树数据

  // 根据当前选中节点类型，从后端获取数据，然后动态构建左侧分类树的展示结构
  const loadTreeData = async () => {
    setTreeLoading(true);
    try {
      const nodes = await fetchNodes(selectedType as NodeType);  //调用后端API获取当前选中类型的节点数据
      const list = Array.isArray(nodes) ? nodes : [];  //确保数据是数组格式
      setAllNodes(list);

      if (selectedType === 'EquipmentType') {
        const types = Array.from(new Set(list.map(n => n.type).filter(Boolean))) as string[];
        const tree: DataNode[] = types.map(t => ({
          title: `${t} (${list.filter(n => n.type === t).length})`,
          key: t,
          children: list.filter(n => n.type === t).map(n => ({
            title: n.name || n.id,
            key: n.id,
          })),
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
        const tree: DataNode[] = ENVIRONMENTS.map(env => {
          const projs = list.filter(n => {
            const envId = n.environmentid || '';
            return ENV_MAP[envId] === env;
          });
          return {
            title: `${env} (${projs.length})`,
            key: env,
            children: projs.map(p => ({ title: p.name || p.id, key: p.id })),
          };
        });
        setTreeData(tree);
      } else if (selectedType === 'Indicator') {
        const tree: DataNode[] = [...INDICATOR_CATS.map(cat => {
          const items = list.filter(n => classifyIndicator(n.name || '') === cat.key);
          return {
            title: `${cat.key} (${items.length})`,
            key: cat.key,
            children: items.map(n => ({ title: n.name || n.id, key: n.id })),
          };
        }), {
          title: `其他 (${list.filter(n => classifyIndicator(n.name || '') === '其他').length})`,
          key: '其他',
          children: list.filter(n => classifyIndicator(n.name || '') === '其他').map(n => ({ title: n.name || n.id, key: n.id })),
        }];
        setTreeData(tree);
      } else if (selectedType === 'TestMethod') {
        const envNodes: DataNode[] = ENVIRONMENTS.map(env => ({
          title: env, key: `env_${env}`, children: [],
        }));
        const indNodes = list.filter(n => n.type === '指标' || n.source_name);
        const tree: DataNode[] = [
          ...envNodes,
          {
            title: `指标 (${indNodes.length})`,
            key: 'indicator_category',
            children: indNodes.map(n => ({ title: n.name || n.id, key: n.id })),
          },
        ];
        setTreeData(tree);
      } else if (selectedType === 'File') {
        const types = Array.from(new Set(list.map(n => n.type).filter(Boolean))) as string[];
        const tree: DataNode[] = types.map(t => ({
          title: `${t} (${list.filter(n => n.type === t).length})`,
          key: t,
          children: list.filter(n => n.type === t).map(n => ({ title: n.name || n.id, key: n.id })),
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
      const formData = new FormData();  //创建表单数据对象，专门用于文件上传
      formData.append('file', file);  //将选中的文件添加到表单数据中，字段名为'file'

      const response = await fetch('/api/FLGL/upload', {
        method: 'POST',
        body: formData,
      });

      const result = await response.json();
      
      if (result.success) {
        message.success(result.message || '上传成功');
        await loadTreeData();  // 上传成功后刷新数据
      } else {
        message.error(result.message || '上传失败');
      }
    } catch (err: any) {
        message.error(`上传失败: ${err.message}`);
      } finally {
        setUploading(false);
        hideLoading();
      }

    return false; // 阻止默认的上传行为
  };

  const renderContent = () => {
    const props = {
      allNodes,
      selectedCategory,
      categoryNodes,
      onRefresh: loadTreeData,
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
      case 'File':
        return <FilePanel {...props} />;
      default:
        return <Empty description="请选择节点类型" />;
    }
  };

  const currentType = NODE_TYPES.find(t => t.key === selectedType);
  const showTreeSidebar = !NO_SIDEBAR_TYPES.has(selectedType);

  return (
    <Layout style={{ minHeight: '100vh', background: '#f0f2f5' }}>
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
          <Space size='small'>
            <Upload
              beforeUpload={handleFileUpload}
              showUploadList={false}
              accept=".doc,.docx"
            >
              <Button
               type="text" 
               size="small" 
               icon={<UploadOutlined />}
               title="上传文件导入数据"
               loading={uploading}
              />
            </Upload>
            <Button
              type="text"
              size="small"
              icon={typeCollapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
              onClick={() => setTypeCollapsed(!typeCollapsed)}
              style={{ color: '#999' }}
            />
          </Space>
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
  );
}

export const dynamic = 'force-dynamic';
