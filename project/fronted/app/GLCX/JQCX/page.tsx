"use client";

import React, { useState, useEffect, useMemo, useCallback, useRef } from "react";
import {
  Card,
  Typography,
  Tree,
  Input,
  Button,
  Tag,
  Empty,
  message,
  Badge,
  Spin,
  App,
} from "antd";
import {
  SearchOutlined,
  ApartmentOutlined,
  FileTextOutlined,
  ReloadOutlined,
  ClearOutlined,
  ExperimentOutlined,
} from "@ant-design/icons";
import ThreeColumnLayout from "@/components/ThreeColumnLayout";
import EnvironmentTable, {
  type EnvironmentTableRow,
  type SelectedProjectEntry,
  type SelectedIndicatorEntry,
} from "@/components/EnvironmentTable";
import MethodList, { type MethodNode } from "@/components/MethodList/index";
import {
  fetchEquipmentTree,
  fetchProjects,
  fetchIndicators,
  fetchMethods,
  type EquipmentTreeData,
  type ProjectNode,
  type IndicatorNode,
  type EquipmentNode,
} from "./api";

const { Title, Text } = Typography;

interface AntdTreeNode {
  title: string;
  key: string;
  children?: AntdTreeNode[];
  isLeaf?: boolean;
  nodeType?: "category" | "equipmentType" | "equipment";
  equipmentData?: EquipmentNode;
  equipmentTypeId?: string;
}

function AssociationQueryPage() {
  const { message: msg } = App.useApp();

  const [equipmentTreeData, setEquipmentTreeData] = useState<AntdTreeNode[]>([]);
  const [selectedEquipmentKeys, setSelectedEquipmentKeys] = useState<string[]>([]);
  const [equipmentSearchText, setEquipmentSearchText] = useState("");

  const [tableRows, setTableRows] = useState<EnvironmentTableRow[]>([]);
  const [selectedProjects, setSelectedProjects] = useState<SelectedProjectEntry[]>([]);
  const [selectedIndicators, setSelectedIndicators] = useState<SelectedIndicatorEntry[]>([]);
  const [tableLoading, setTableLoading] = useState(false);

  const [filteredMethods, setFilteredMethods] = useState<MethodNode[]>([]);
  const [selectedMethodIds, setSelectedMethodIds] = useState<string[]>([]);
  const [methodsLoading, setMethodsLoading] = useState(false);

  const [loading, setLoading] = useState(false);

  /** 中间栏可滚动区域高度，供 Table scroll.y（像素）使用 */
  const middleScrollRef = useRef<HTMLDivElement>(null);
  const [middleTableScrollY, setMiddleTableScrollY] = useState(360);

  const updateMiddleTableScroll = useCallback(() => {
    const el = middleScrollRef.current;
    if (!el) return;
    const h = el.clientHeight;
    // 预留表头、边框等（无「全选」工具条时约 48~56px）
    setMiddleTableScrollY(Math.max(160, Math.floor(h - 56)));
  }, []);

  useEffect(() => {
    const el = middleScrollRef.current;
    if (!el) return;
    updateMiddleTableScroll();
    const ro = new ResizeObserver(() => updateMiddleTableScroll());
    ro.observe(el);
    return () => ro.disconnect();
  }, [updateMiddleTableScroll, selectedEquipmentKeys, tableRows, tableLoading]);

  useEffect(() => {
    loadEquipmentTree();
  }, []);

  const loadEquipmentTree = useCallback(async () => {
    setLoading(true);
    try {
      const treeData = await fetchEquipmentTree();
      const nodes = buildAntdTree(treeData);
      setEquipmentTreeData(nodes);
    } catch (error) {
      msg.error("加载装备树失败");
      console.error(error);
    } finally {
      setLoading(false);
    }
  }, []);

  function buildAntdTree(data: any[]): AntdTreeNode[] {
    if (!data || data.length === 0) return [];

    const isAntdFormat = data[0]?.key !== undefined && data[0]?.title !== undefined;

    if (isAntdFormat) {
      return data.map((node) => {
        const rawKey = node.key as string;
        let nodeType: AntdTreeNode["nodeType"];
        let key: string;
        let children: any[] | undefined;

        if (rawKey.startsWith("type_")) {
          nodeType = "category";
          key = rawKey;
          children = node.children ? buildAntdTree(node.children) : undefined;
        } else if (rawKey.startsWith("et_")) {
          nodeType = "equipmentType";
          key = rawKey;
          children = node.children?.map((eq: any) => ({
            ...eq,
            title: eq.title || eq.name,
            key: eq.key?.startsWith("eq_") ? eq.key : `eq_${eq.key}`,
            nodeType: "equipment" as const,
            isLeaf: true,
            children: undefined,
          }));
        } else {
          nodeType = "equipment";
          key = rawKey.startsWith("eq_") ? rawKey : `eq_${rawKey}`;
          children = undefined;
        }

        return {
          ...node,
          key,
          title: node.title || node.name,
          nodeType,
          equipmentTypeId: nodeType === "equipmentType" ? rawKey.replace("et_", "") : undefined,
          isLeaf: nodeType === "equipment",
          children,
        };
      });
    }

    return data.map((group) => ({
      title: group.category.name || group.category.type || "未命名分类",
      key: `cat_${group.category.id || group.category.type}`,
      nodeType: "category",
      children: group.types.map((t: { type: { name: any; id: any; }; equipment: any[]; }) => ({
        title: t.type.name,
        key: `et_${t.type.id}`,
        nodeType: "equipmentType",
        equipmentTypeId: t.type.id,
        children: t.equipment.map((eq) => ({
          title: eq.name,
          key: `eq_${eq.id}`,
          nodeType: "equipment",
          isLeaf: true,
          equipmentData: eq,
        })),
      })),
    }));
  }

  useEffect(() => {
    const handleEquipmentChange = async () => {
      const allEquipmentIds: string[] = [];

      for (const key of selectedEquipmentKeys) {
        if (key.startsWith("type_")) {
          const categoryNode = equipmentTreeData.find((cat) => cat.key === key);
          if (categoryNode?.children) {
            for (const child of categoryNode.children) {
              if (child.children) {
                for (const eq of child.children) {
                  if (eq.key.startsWith("eq_")) {
                    allEquipmentIds.push(eq.key.replace("eq_", ""));
                  }
                }
              }
            }
          }
        } else if (key.startsWith("et_")) {
          for (const cat of equipmentTreeData) {
            for (const child of cat.children || []) {
              if (child.key === key) {
                for (const eq of child.children || []) {
                  if (eq.key.startsWith("eq_")) {
                    allEquipmentIds.push(eq.key.replace("eq_", ""));
                  }
                }
              }
            }
          }
        } else if (key.startsWith("eq_")) {
          allEquipmentIds.push(key.replace("eq_", ""));
        }
      }

      const uniqueEqIds = Array.from(new Set(allEquipmentIds));

      if (uniqueEqIds.length === 0) {
        setTableRows([]);
        setSelectedProjects([]);
        setSelectedIndicators([]);
        setFilteredMethods([]);
        return;
      }

      setTableLoading(true);
      try {
        const rows: EnvironmentTableRow[] = [];

        await Promise.all(
          uniqueEqIds.map(async (eqId) => {
            const [projects, indicators] = await Promise.all([
              fetchProjects(eqId),
              fetchIndicators(eqId),
            ]);

            const projectsByEnv: Record<string, ProjectNode[]> = {};

            const categories = projects.filter((p) => p.type === "category");
            categories.forEach((cat) => {
              const childEnvs = new Set<string>();
              if (cat.children && cat.children.length > 0) {
                cat.children.forEach((child) => {
                  const rawEnv = String((child as any)._env || "").trim();
                  const childEnv = rawEnv || "其他环境";
                  childEnvs.add(childEnv);
                  if (!projectsByEnv[childEnv]) projectsByEnv[childEnv] = [];
                  if (!projectsByEnv[childEnv].some((p) => p.id === child.id)) {
                    projectsByEnv[childEnv].push(child);
                  }
                });
              }
              const parentEnv = ((cat as any)._env || "").trim();
              if (parentEnv) {
                parentEnv
                  .split("、")
                  .map((name: string) => name.trim())
                  .filter(Boolean)
                  .forEach((envName: string) => childEnvs.add(envName));
              }
              if (childEnvs.size === 0) {
                childEnvs.add("其他环境");
              }
              childEnvs.forEach((envName) => {
                if (!projectsByEnv[envName]) {
                  projectsByEnv[envName] = [];
                }
                if (!projectsByEnv[envName].some((p) => p.id === cat.id)) {
                  projectsByEnv[envName].unshift(cat);
                }
              });
            });

            const eqNode = findEquipmentNode(equipmentTreeData, eqId);

            rows.push({
              equipmentTypeId: eqId,
              equipmentTypeName: eqNode || eqId,
              projectsByEnv,
              indicators,
            });
          })
        );

        setTableRows(rows);
        setSelectedProjects([]);
        setSelectedIndicators([]);
      } catch (error) {
        msg.error("加载数据失败");
        console.error(error);
      } finally {
        setTableLoading(false);
      }
    };

    handleEquipmentChange();
  }, [selectedEquipmentKeys, equipmentTreeData]);

  useEffect(() => {
    if (selectedProjects.length === 0 && selectedIndicators.length === 0) {
      setFilteredMethods([]);
      return;
    }

    const timer = setTimeout(async () => {
      setMethodsLoading(true);
      try {
        const methods = await fetchMethods({
          projectIds: selectedProjects.map((p) => p.projectId),
          indicatorIds: selectedIndicators.map((i) => i.indicatorId),
        });
        setFilteredMethods(methods);
      } catch (error) {
        msg.error("方法查询失败");
        console.error(error);
      } finally {
        setMethodsLoading(false);
      }
    }, 300);

    return () => clearTimeout(timer);
  }, [selectedProjects, selectedIndicators]);

  function findEquipmentNode(tree: AntdTreeNode[], eqId: string): string | null {
    for (const cat of tree) {
      for (const child of (cat.children || [])) {
        for (const eq of (child.children || [])) {
          if (eq.key === `eq_${eqId}`) return eq.title;
        }
      }
    }
    return null;
  }

  const handleEquipmentCheck = (checkedKeys: any) => {
    let keys = Array.isArray(checkedKeys) ? checkedKeys : checkedKeys.checked || [];
    // 关键词筛选时：父子勾选独立，选子级不自动勾选父级（仅精确查询此处）
    if (equipmentSearchText.trim()) {
      keys = keys.filter((k: string) => String(k).startsWith("eq_"));
    }
    setSelectedEquipmentKeys(keys as string[]);
  };

  const handleResetAll = () => {
    setSelectedEquipmentKeys([]);
    setSelectedProjects([]);
    setSelectedIndicators([]);
    setSelectedMethodIds([]);
    setFilteredMethods([]);
    setEquipmentSearchText("");
    msg.success("已重置所有选择");
  };

  const handleProjectSelect = (entry: SelectedProjectEntry, checked: boolean) => {
    if (checked) {
      setSelectedProjects((prev) => [...prev, entry]);
    } else {
      setSelectedProjects((prev) =>
        prev.filter(
          (p) => !(p.projectId === entry.projectId && p.equipmentTypeId === entry.equipmentTypeId)
        )
      );
    }
  };

  const handleIndicatorSelect = (entry: SelectedIndicatorEntry, checked: boolean) => {
    if (checked) {
      setSelectedIndicators((prev) => [...prev, entry]);
    } else {
      setSelectedIndicators((prev) =>
        prev.filter(
          (i) => !(i.indicatorId === entry.indicatorId && i.equipmentTypeId === entry.equipmentTypeId)
        )
      );
    }
  };

  const handleMethodSelect = (methodId: string, checked: boolean) => {
    if (checked) {
      setSelectedMethodIds((prev) => [...prev, methodId]);
    } else {
      setSelectedMethodIds((prev) => prev.filter((id) => id !== methodId));
    }
  };

  const filteredTreeData = useMemo(() => {
    if (!equipmentSearchText) return equipmentTreeData;
    const keyword = equipmentSearchText.toLowerCase();
    const filterNodes = (nodes: AntdTreeNode[]): AntdTreeNode[] => {
      return nodes
        .map((node) => {
          if (node.isLeaf) {
            if (node.title.toLowerCase().includes(keyword)) return node;
            return null;
          }
          const filteredChildren = node.children ? filterNodes(node.children) : [];
          if (filteredChildren.length > 0) {
            return { ...node, children: filteredChildren };
          }
          if (node.title.toLowerCase().includes(keyword)) return node;
          return null;
        })
        .filter(Boolean) as AntdTreeNode[];
    };    
    return filterNodes(equipmentTreeData);
  }, [equipmentTreeData, equipmentSearchText]);

  const renderEquipmentPanel = () => (
    <Card
      size="small"
      title={
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <ApartmentOutlined style={{ color: "#3b82f6" }} />
          <span>装备</span>
          <Badge count={selectedEquipmentKeys.length} style={{ backgroundColor: "#3b82f6" }} />
        </div>
      }
      extra={
        <Button
          size="small"
          icon={<ClearOutlined />}
          onClick={() => {
            setSelectedEquipmentKeys([]);
            setEquipmentSearchText("");
          }}
        >
          清除
        </Button>
      }
      style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column", overflow: "hidden" }}
      styles={{
        body: {
          flex: 1,
          minHeight: 0,
          display: "flex",
          flexDirection: "column",
          padding: 12,
          overflow: "hidden",
        },
      }}
    >
      <Input
        placeholder="搜索装备"
        value={equipmentSearchText}
        onChange={(e) => setEquipmentSearchText(e.target.value)}
        prefix={<SearchOutlined />}
        allowClear
        style={{ marginBottom: 12, flexShrink: 0 }}
        size="small"
      />
      <div style={{ flex: 1, minHeight: 0, overflow: "auto" }}>
        {loading ? (
          <Spin />
        ) : filteredTreeData.length > 0 ? (
          <Tree
            checkable
            checkStrictly={Boolean(equipmentSearchText.trim())}
            checkedKeys={selectedEquipmentKeys}
            onCheck={handleEquipmentCheck}
            treeData={filteredTreeData}
            defaultExpandAll
            showLine={{ showLeafIcon: false }}
            style={{ fontSize: 13 }}
          />
        ) : (
          <Empty description="暂无装备数据" image={Empty.PRESENTED_IMAGE_SIMPLE} />
        )}
      </div>
    </Card>
  );

  const renderTablePanel = () => (
    <Card
      size="small"
      title={
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <ExperimentOutlined style={{ color: "#8b5cf6" }} />
          <span>试验项目与考核指标</span>
          <Badge count={selectedProjects.length + selectedIndicators.length} style={{ backgroundColor: "#8b5cf6" }} />
        </div>
      }
      extra={
        (selectedProjects.length > 0 || selectedIndicators.length > 0) && (
          <Button
            size="small"
            onClick={() => {
              setSelectedProjects([]);
              setSelectedIndicators([]);
            }}
            icon={<ClearOutlined />}
          >
            清除选择
          </Button>
        )
      }
      style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column", overflow: "hidden" }}
      styles={{
        body: {
          flex: 1,
          minHeight: 0,
          padding: 0,
          display: "flex",
          flexDirection: "column",
          overflow: "hidden",
        },
      }}
    >
      <div
        ref={middleScrollRef}
        style={{
          flex: 1,
          minHeight: 0,
          minWidth: 0,
          overflow: "hidden",
          display: "flex",
          flexDirection: "column",
        }}
      >
        <EnvironmentTable
          dataSource={tableRows}
          selectedProjects={selectedProjects}
          selectedIndicators={selectedIndicators}
          onProjectSelect={handleProjectSelect}
          onIndicatorSelect={handleIndicatorSelect}
          singleSelect={true}
          loading={tableLoading}
          showTableBody={selectedEquipmentKeys.length > 0}
          emptyText={
            selectedEquipmentKeys.length === 0 ? "请在左侧选择装备" : "当前装备暂无数据"
          }
          scrollY={middleTableScrollY}
          style={{ flex: 1, minHeight: 0 }}
        />
      </div>
    </Card>
  );

  const renderMethodPanel = () => (
    <Card
      size="small"
      title={
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <FileTextOutlined style={{ color: "#6366f1" }} />
          <span>试验方法</span>
          <Badge count={filteredMethods.length} style={{ backgroundColor: "#6366f1" }} />
        </div>
      }
      extra={
        selectedMethodIds.length > 0 && (
          <Tag color="blue" style={{ margin: 0 }}>已选 {selectedMethodIds.length}</Tag>
        )
      }
      style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column", overflow: "hidden" }}
      styles={{
        body: {
          flex: 1,
          minHeight: 0,
          padding: 12,
          display: "flex",
          flexDirection: "column",
          overflow: "hidden",
        },
      }}
    >
      {selectedProjects.length === 0 && selectedIndicators.length === 0 ? (
        <div style={{ flex: 1, minHeight: 0, display: "flex", alignItems: "center", justifyContent: "center" }}>
          <Empty
            description={<div style={{ fontSize: 13, color: "#999" }}>选择试验项目和/或考核指标后自动显示关联试验方法</div>}
            image={Empty.PRESENTED_IMAGE_SIMPLE}
          />
        </div>
      ) : (
        <MethodList
          methods={filteredMethods}
          selectedMethodIds={selectedMethodIds}
          onMethodSelect={handleMethodSelect}
          singleSelect={true}
          loading={methodsLoading}
          emptyText="未找到匹配的试验方法"
          style={{ flex: 1, minHeight: 0 }}
        />
      )}
    </Card>
  );

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        width: "100%",
        height: "100vh",
        maxHeight: "100vh",
        minHeight: 0,
        overflow: "hidden",
        boxSizing: "border-box",
        background: "linear-gradient(135deg, #f0f7ff 0%, #e6f0ff 100%)",
        position: "relative",
      }}
    >
      <div
        style={{
          flex: 1,
          minWidth: 0,
          minHeight: 0,
          padding: 16,
          position: "relative",
          zIndex: 5,
          overflow: "hidden",
          display: "flex",
          flexDirection: "column",
          boxSizing: "border-box",
        }}
      >
        <ThreeColumnLayout
          layoutKey="jqcx"
          style={{ flex: 1, minHeight: 0, minWidth: 0 }}
          header={
            <div style={{ display: "flex", alignItems: "center", gap: 12, flexShrink: 0, marginBottom: 0 }}>
              <Title level={4} style={{ margin: 0, color: "#1a1a2e" }}>
                精确查询
              </Title>
              <Button icon={<ReloadOutlined />} onClick={handleResetAll} size="middle" style={{ marginLeft: "auto" }}>
                重置所有选择
              </Button>
            </div>
          }
          leftPanel={renderEquipmentPanel()}
          middlePanel={renderTablePanel()}
          rightPanel={renderMethodPanel()}
        />
      </div>
    </div>
  );
}

const AssociationQueryPageWithApp: React.FC = () => (
  <App>
    <AssociationQueryPage />
  </App>
);

export default AssociationQueryPageWithApp;
