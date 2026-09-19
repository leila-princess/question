"use client";

import React, { useState, useEffect, useMemo, useCallback, useRef } from "react";
import {
  Modal,
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
} from "antd";
import {
  SearchOutlined,
  ApartmentOutlined,
  FileTextOutlined,
  ClearOutlined,
} from "@ant-design/icons";
import ThreeColumnLayout from "@/components/ThreeColumnLayout";
import EnvironmentTable, {
  type EnvironmentTableRow,
  type selectedEquipmentInfo,
  type SelectedProjectEntry,
  type SelectedIndicatorEntry,
} from "@/components/EnvironmentTable";
import MethodList, { type MethodNode, type RelatedNode } from "@/components/MethodList/index";
import {
  fetchEquipmentTree as jyllmFetchEquipmentTree,
  fetchProjects as jyllmFetchProjects,
  fetchIndicators as jyllmFetchIndicators,
  fetchMethods as jyllmFetchMethods,
} from "../api";
import {
  isGenericEnvironmentName,
  resolveProjectEnvironmentName,
} from "@/lib/resolveProjectEnvironment";
import { collectMethodImageUrls } from "@/lib/methodImageUrls";

const { Text } = Typography;

/** 将 JYGZ 等接口的驼峰字段规范为 MethodList 使用的 MethodNode（与 JQCX 一致） */
function normalizeMethodListItem(raw: Record<string, unknown>): MethodNode {
  const mapRelated = (arr: unknown): RelatedNode[] => {
    if (!Array.isArray(arr)) return [];
    const out: RelatedNode[] = [];
    for (const r of arr) {
      if (!r || typeof r !== "object") continue;
      const o = r as Record<string, unknown>;
      const id = String(o.id ?? "");
      if (!id) continue;
      const name = String(o.name ?? "");
      const rawRate = o.rate ?? o.Rate;
      const node: RelatedNode = { id, name };
      if (rawRate !== undefined && rawRate !== null) {
        const n = Number(rawRate);
        if (Number.isFinite(n)) node.rate = n;
      }
      out.push(node);
    }
    return out;
  };

  const rpFromSnake = mapRelated(raw.related_projects);
  const rp2 = rpFromSnake.length ? rpFromSnake : mapRelated(raw.relatedProjects);
  const riFromSnake = mapRelated(raw.related_indicators);
  const ri2 = riFromSnake.length ? riFromSnake : mapRelated(raw.relatedIndicators);

  let max_rate: number | undefined;
  if (raw.max_rate !== undefined && raw.max_rate !== null) {
    const n = Number(raw.max_rate);
    if (Number.isFinite(n)) max_rate = n;
  }
  if (max_rate === undefined) {
    const rates = [...(rp2 || []), ...(ri2 || [])]
      .map((x) => (x?.rate !== undefined ? Number(x.rate) : NaN))
      .filter((n) => Number.isFinite(n)) as number[];
    if (rates.length) max_rate = Math.max(0, ...rates);
  }

  const rt = raw._relationType ?? raw.relationType;
  const _relationType =
    rt === "project-only" || rt === "indicator-only" || rt === "both"
      ? rt
      : undefined;

  const files = Array.isArray(raw.files) ? (raw.files as MethodNode["files"]) : undefined;

  const synr = String(raw.synr ?? raw.SYNR ?? raw.Sy ?? "");
  const sytj = String(raw.SYTJ ?? raw.sytj ?? "");
  const sygc = String(raw.SYGC ?? raw.sygc ?? "");
  const jgpd = String(raw.JGPD ?? raw.jgpd ?? "");
  const sy = String(raw.sy ?? raw.Sy ?? "");
  const label = raw.label !== undefined ? String(raw.label) : undefined;
  const type = raw.type !== undefined ? String(raw.type) : undefined;

  const imageurls = collectMethodImageUrls(synr, files as Array<Record<string, unknown>> | undefined);

  return {
    id: String(raw.id ?? ""),
    name: String(raw.name ?? ""),
    description: raw.description !== undefined ? String(raw.description) : undefined,
    source_name: raw.source_name !== undefined ? String(raw.source_name) : undefined,
    source_id: raw.source_id !== undefined ? String(raw.source_id) : undefined,
    files,
    imageurls: imageurls.length > 0 ? imageurls : undefined,
    _relationType,
    related_projects: rp2?.length ? rp2 : [],
    related_indicators: ri2?.length ? ri2 : [],
    max_rate,
    synr,
    SYTJ: sytj,
    SYGC: sygc,
    JGPD: jgpd,
    sy,
    ...(label !== undefined && label !== "" ? { label } : {}),
    ...(type !== undefined && type !== "" ? { type } : {}),
  };
}

function pickTopMethodIdForProject(methods: MethodNode[], projectId: string): string | null {
  let bestId: string | null = null;
  let bestRate = -1;
  for (const m of methods) {
    const rp = m.related_projects?.find((r) => r.id === projectId);
    if (!rp) continue;
    const rate =
      rp.rate !== undefined && rp.rate !== null && Number.isFinite(Number(rp.rate))
        ? Number(rp.rate)
        : (m.max_rate ?? 0);
    if (rate > bestRate) {
      bestRate = rate;
      bestId = m.id;
    } else if (rate === bestRate && bestId !== null && m.id.localeCompare(bestId) < 0) {
      bestId = m.id;
    }
  }
  return bestId;
}

function pickTopMethodIdForIndicator(methods: MethodNode[], indicatorId: string): string | null {
  let bestId: string | null = null;
  let bestRate = -1;
  for (const m of methods) {
    const ri = m.related_indicators?.find((r) => r.id === indicatorId);
    if (!ri) continue;
    const rate =
      ri.rate !== undefined && ri.rate !== null && Number.isFinite(Number(ri.rate))
        ? Number(ri.rate)
        : (m.max_rate ?? 0);
    if (rate > bestRate) {
      bestRate = rate;
      bestId = m.id;
    } else if (rate === bestRate && bestId !== null && m.id.localeCompare(bestId) < 0) {
      bestId = m.id;
    }
  }
  return bestId;
}

/** 每个已选项目、每个已选指标各取关联度最高的一条方法（去重） */
function defaultSelectedMethodIds(
  methods: MethodNode[],
  projects: SelectedProjectEntry[],
  indicators: SelectedIndicatorEntry[],
): string[] {
  const picked = new Set<string>();
  for (const p of projects) {
    const id = pickTopMethodIdForProject(methods, p.projectId);
    if (id) picked.add(id);
  }
  for (const i of indicators) {
    const id = pickTopMethodIdForIndicator(methods, i.indicatorId);
    if (id) picked.add(id);
  }
  return Array.from(picked);
}

interface AntdTreeNode {
  title: string;
  key: string;
  children?: AntdTreeNode[];
  isLeaf?: boolean;
  nodeType?: "category" | "equipmentType" | "equipment";
  equipmentTypeId?: string;
}

export interface EquipmentInfo {
  key: string;
  id: string;
  name: string;
  equipmentTypeId: string;
  equipmentTypeName: string;
  categoryId: string;
  categoryName: string;
}

interface ConfigModalProps {
  open: boolean;
  onClose: () => void;
  /** 弹窗内三栏区域高度（与弹窗可视区对齐，两页可分别传入） */
  contentHeight?: string | number;
  /** 可选：基于规则页等传入 JYGZ POST /methods，与默认 JYLLM /methods 二选一 */
  fetchMethods?: (params: { projectIds: string[]; indicatorIds: string[] }) => Promise<unknown[]>;
  onSave: (data: {
    selectedEquipmentKeys: string[];
    selectedEquipmentInfo: EquipmentInfo[];
    selectedProjects: SelectedProjectEntry[];
    selectedIndicators: SelectedIndicatorEntry[];
    selectedMethodIds: string[];
    selectedMethodNodes: any[];
    tableRows: EnvironmentTableRow[];
    envNames: string[];
    /** 试验项目 id → 配置矩阵中的环境列名（用于骨架 #### 2.1.x，不依赖图库） */
    projectEnvironmentById: Record<string, string>;
    deviceDisplayName: string;
    environmentConditionsText: string;
  }) => void;
}

const DEFAULT_CONTENT_HEIGHT = "calc(90vh - 108px)";

export default function ConfigModal({
  open,
  onClose,
  onSave,
  fetchMethods,
  contentHeight = DEFAULT_CONTENT_HEIGHT,
}: ConfigModalProps) {
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

  const [deviceDisplayName, setDeviceDisplayName] = useState("");
  const [environmentConditionsText, setEnvironmentConditionsText] = useState("");

  const [loading, setLoading] = useState(false);

  const middleScrollRef = useRef<HTMLDivElement>(null);
  const [middleTableScrollY, setMiddleTableScrollY] = useState(360);

  const updateMiddleTableScroll = useCallback(() => {
    const el = middleScrollRef.current;
    if (!el) return;
    const h = el.clientHeight;
    setMiddleTableScrollY(Math.max(160, Math.floor(h - 56)));
  }, []);

  useEffect(() => {
    if (!open) return;
    const el = middleScrollRef.current;
    if (!el) return;
    updateMiddleTableScroll();
    const ro = new ResizeObserver(() => updateMiddleTableScroll());
    ro.observe(el);
    return () => ro.disconnect();
  }, [open, updateMiddleTableScroll, selectedEquipmentKeys, tableRows, tableLoading]);

  useEffect(() => {
    if (open) {
      loadEquipmentTree();
    }
  }, [open]);

  const loadEquipmentTree = useCallback(async () => {
    setLoading(true);
    try {
      const treeData = await jyllmFetchEquipmentTree();
      const nodes = buildAntdTree(Array.isArray(treeData) ? treeData : []);
      setEquipmentTreeData(nodes);
    } catch (error) {
      message.error("加载设备树失败");
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
        if (key.startsWith('type_')) {
          const categoryNode = equipmentTreeData.find((cat) => cat.key === key);
          if (categoryNode?.children) {
            for (const child of categoryNode.children) {
              if (child.children) {
                for (const eq of child.children) {
                  if (eq.key.startsWith('eq_')) {
                    allEquipmentIds.push(eq.key.replace('eq_', ''));
                  }
                }
              }
            }
          }
        } else if (key.startsWith('et_')) {
          for (const cat of equipmentTreeData) {
            for (const child of cat.children || []) {
              if (child.key === key) {
                for (const eq of child.children || []) {
                  if (eq.key.startsWith('eq_')) {
                    allEquipmentIds.push(eq.key.replace('eq_', ''));
                  }
                }
              }
            }
          }
        } else if (key.startsWith('eq_')) {
          allEquipmentIds.push(key.replace('eq_', ''));
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
              jyllmFetchProjects([eqId]),
              jyllmFetchIndicators([eqId]),
            ]);

            const projList = Array.isArray(projects) ? projects : [];
            const indList = Array.isArray(indicators) ? indicators : [];

            const projectsByEnv: Record<string, any[]> = {};

            const categories = projList.filter((p: any) => p.type === "category");
            categories.forEach((cat: any) => {
              const children = cat.children || [];
              if (children.length > 0) {
                children.forEach((child: any) => {
                  const envName =
                    resolveProjectEnvironmentName(child, cat) || "其他环境";
                  if (!projectsByEnv[envName]) projectsByEnv[envName] = [];
                  if (!projectsByEnv[envName].some((p: any) => p.id === cat.id)) {
                    projectsByEnv[envName].push(cat);
                  }
                  if (!projectsByEnv[envName].some((p: any) => p.id === child.id)) {
                    projectsByEnv[envName].push(child);
                  }
                });
              } else {
                const envName =
                  resolveProjectEnvironmentName(cat, cat) || "其他环境";
                if (!projectsByEnv[envName]) projectsByEnv[envName] = [];
                projectsByEnv[envName].push(cat);
              }
            });

            const eqNode = findEquipmentNode(equipmentTreeData, eqId);

            rows.push({
              equipmentTypeId: eqId,
              equipmentTypeName: eqNode || eqId,
              projectsByEnv,
              indicators: indList,
            });
          })
        );

        setTableRows(rows);
        selectAllProjectsAndIndicators(rows);
      } catch (error) {
        message.error("加载数据失败");
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
        const doFetch = fetchMethods ?? jyllmFetchMethods;
        const methods = await doFetch({
          projectIds: selectedProjects.map((p) => p.projectId),
          indicatorIds: selectedIndicators.map((i) => i.indicatorId),
        });
        const rawList = Array.isArray(methods) ? methods : [];
        const methodList = rawList.map((m) =>
          normalizeMethodListItem(m && typeof m === "object" ? (m as Record<string, unknown>) : {}),
        );
        setFilteredMethods(methodList);
        setSelectedMethodIds(defaultSelectedMethodIds(methodList, selectedProjects, selectedIndicators));
      } catch (error) {
        message.error("方法查询失败");
        console.error(error);
      } finally {
        setMethodsLoading(false);
      }
    }, 300);

    return () => clearTimeout(timer);
  }, [selectedProjects, selectedIndicators, fetchMethods]);

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

  const selectAllProjectsAndIndicators = (rows: EnvironmentTableRow[]) => {
    const allProjects: SelectedProjectEntry[] = [];
    const allIndicators: SelectedIndicatorEntry[] = [];

    rows.forEach((row) => {
      Object.values(row.projectsByEnv).forEach((projects) => {
        projects.forEach((project: any) => {
          if (!allProjects.some((p) => p.projectId === project.id && p.equipmentTypeId === row.equipmentTypeId)) {
            allProjects.push({
              projectId: project.id,
              equipmentTypeId: row.equipmentTypeId,
              categoryId: project.type === "category" ? project.id : undefined,
              nodeData: project,
            });
          }
        });
      });

      row.indicators.forEach((indicator) => {
        if (indicator.type === "category") {
          const cat = indicator;
          if (!allIndicators.some((i) => i.indicatorId === cat.id && i.equipmentTypeId === row.equipmentTypeId)) {
            allIndicators.push({
              indicatorId: cat.id,
              equipmentTypeId: row.equipmentTypeId,
              categoryId: cat.id,
              nodeData: cat,
            });
          }
          (cat.children || []).forEach((child) => {
            if (!allIndicators.some((i) => i.indicatorId === child.id && i.equipmentTypeId === row.equipmentTypeId)) {
              allIndicators.push({
                indicatorId: child.id,
                equipmentTypeId: row.equipmentTypeId,
                categoryId: cat.id,
                nodeData: child,
              });
            }
          });
          return;
        }
        const isNestedChild = row.indicators.some(
          (cat) =>
            cat.type === "category" &&
            (cat.children || []).some((c) => c.id === indicator.id),
        );
        if (isNestedChild) return;
        if (!allIndicators.some((i) => i.indicatorId === indicator.id && i.equipmentTypeId === row.equipmentTypeId)) {
          allIndicators.push({
            indicatorId: indicator.id,
            equipmentTypeId: row.equipmentTypeId,
            nodeData: indicator,
          });
        }
      });
    });

    setSelectedProjects(allProjects);
    setSelectedIndicators(allIndicators);
  };

  const handleSelectAll = () => {
    selectAllProjectsAndIndicators(tableRows);
  };

  const handleClearAll = () => {
    setSelectedProjects([]);
    setSelectedIndicators([]);
  };

  const handleEquipmentCheck = (checkedKeys: any) => {
    const keys = (Array.isArray(checkedKeys) ? checkedKeys : checkedKeys.checked || []) as string[];
    if (keys.length <= 1) {
      setSelectedEquipmentKeys(keys);
      return;
    }

    const newlyChecked = keys.find((k) => !selectedEquipmentKeys.includes(k));
    const priorityKeys = keys.filter((k) => k.startsWith("eq_"));
    const fallback = newlyChecked || priorityKeys[priorityKeys.length - 1] || keys[keys.length - 1];
    setSelectedEquipmentKeys(fallback ? [fallback] : []);
  };

  const resolveEquipmentTitle = useCallback(
    (eqKey: string): string => {
      for (const cat of equipmentTreeData) {
        for (const type of cat.children || []) {
          for (const eq of type.children || []) {
            if (eq.key === eqKey) return String(eq.title || "");
          }
        }
      }
      return "";
    },
    [equipmentTreeData],
  );

  useEffect(() => {
    const eqKey = selectedEquipmentKeys.find((k) => k.startsWith("eq_"));
    if (!eqKey) {
      setDeviceDisplayName("");
      return;
    }
    const title = resolveEquipmentTitle(eqKey);
    if (title) setDeviceDisplayName(title);
  }, [selectedEquipmentKeys, resolveEquipmentTitle]);

  const handleResetAll = () => {
    setSelectedEquipmentKeys([]);
    setSelectedProjects([]);
    setSelectedIndicators([]);
    setSelectedMethodIds([]);
    setFilteredMethods([]);
    setEquipmentSearchText("");
    setDeviceDisplayName("");
    setEnvironmentConditionsText("");
    message.success("已重置所有选择");
  };

  const handleSave = () => {

    if (selectedEquipmentKeys.length === 0) {
      message.warning("请至少选择一个设备");
      return;
    }
    if (selectedProjects.length === 0 && selectedIndicators.length === 0) {
      message.warning("请至少选择一个项目或指标");
      return;
    }

    const selectedMethodNodes = filteredMethods.filter((m) => selectedMethodIds.includes(m.id));

    /** 环境设置步骤：只传「已选试验项目」所在的环境，不再把表格里全部环境列都带上 */
    const selectedProjIds = new Set(selectedProjects.map((p) => p.projectId).filter(Boolean));
    const envNames = new Set<string>();
    if (selectedProjIds.size > 0) {
      tableRows.forEach((row) => {
        Object.entries(row.projectsByEnv || {}).forEach(([envName, projects]) => {
          const arr = Array.isArray(projects) ? projects : [];
          if (
            arr.some((p: any) => p?.id && selectedProjIds.has(p.id)) &&
            !isGenericEnvironmentName(envName)
          ) {
            envNames.add(envName);
          }
        });
      });
    } else {
      // 仅选指标、未选项目时无法从项目反推环境，仍列出表格中出现的全部环境键（与历史行为一致）
      tableRows.forEach((row) => {
        Object.keys(row.projectsByEnv || {}).forEach((envName) => {
          envNames.add(envName);
        });
      });
    }

    const projectEnvironmentById: Record<string, string> = {};
    if (selectedProjIds.size > 0) {
      tableRows.forEach((row) => {
        Object.entries(row.projectsByEnv || {}).forEach(([envName, projects]) => {
          const arr = Array.isArray(projects) ? projects : [];
          arr.forEach((proj: { id?: string }) => {
            const id = proj?.id;
            if (id && selectedProjIds.has(id) && !projectEnvironmentById[id]) {
              projectEnvironmentById[id] = envName;
            }
          });
        });
      });
    }
    Object.values(projectEnvironmentById).forEach((n) => {
      if (n && !isGenericEnvironmentName(n)) {
        envNames.add(n);
      }
    });

    if (selectedProjIds.size > 0 && envNames.size === 0) {
      message.warning(
        "已选项目未能匹配到有效环境名称（勿仅有「其他环境/未分组」）。请检查图库 Environment 关联或重新勾选项目。"
      );
      return;
    }

    const selectedEquipmentInfo = (() => {
      const info: EquipmentInfo[] = [];
      const addedKeys = new Set<string>();
      
      for (const key of selectedEquipmentKeys) {
        if (key.startsWith('eq_')) {
          if (addedKeys.has(key)) continue;
          for (const cat of equipmentTreeData) {
            for (const type of cat.children || []) {
              for (const eq of type.children || []) {
                if (eq.key === key) {
                  info.push({
                    key: eq.key,
                    id: eq.key.replace('eq_', ''),
                    name: eq.title,
                    equipmentTypeId: type.key.replace('et_', ''),
                    equipmentTypeName: type.title,
                    categoryId: cat.key.replace('cat_', ''),
                    categoryName: cat.title,
                  });
                  addedKeys.add(key);
                }
              }
            }
          }
        } else if (key.startsWith('et_')) {
          for (const cat of equipmentTreeData) {
            for (const type of cat.children || []) {
              if (type.key === key) {
                for (const eq of type.children || []) {
                  if (!addedKeys.has(eq.key)) {
                    info.push({
                      key: eq.key,
                      id: eq.key.replace('eq_', ''),
                      name: eq.title,
                      equipmentTypeId: type.key.replace('et_', ''),
                      equipmentTypeName: type.title,
                      categoryId: cat.key.replace('cat_', ''),
                      categoryName: cat.title,
                    });
                    addedKeys.add(eq.key);
                  }
                }
              }
            }
          }
        } else if (key.startsWith('cat_')) {
          for (const cat of equipmentTreeData) {
            if (cat.key === key) {
              for (const type of cat.children || []) {
                for (const eq of type.children || []) {
                  if (!addedKeys.has(eq.key)) {
                    info.push({
                      key: eq.key,
                      id: eq.key.replace('eq_', ''),
                      name: eq.title,
                      equipmentTypeId: type.key.replace('et_', ''),
                      equipmentTypeName: type.title,
                      categoryId: cat.key.replace('cat_', ''),
                      categoryName: cat.title,
                    });
                    addedKeys.add(eq.key);
                  }
                }
              }
            }
          }
        }
      }
      
      return info;
    })();

    onSave({
      selectedEquipmentKeys,
      selectedEquipmentInfo,
      selectedProjects,
      selectedIndicators,
      selectedMethodIds,
      selectedMethodNodes,
      tableRows,
      envNames: Array.from(envNames),
      projectEnvironmentById,
      deviceDisplayName: deviceDisplayName.trim(),
      environmentConditionsText: environmentConditionsText.trim(),
    });
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
          <span>设备</span>
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
        placeholder="搜索设备"
        value={equipmentSearchText}
        onChange={(e) => setEquipmentSearchText(e.target.value)}
        prefix={<SearchOutlined />}
        allowClear
        style={{ marginBottom: 12, flexShrink: 0 }}
        size="small"
      />
      <div style={{ flex: 1, minHeight: 0, overflowY: "auto", overflowX: "hidden", paddingRight: 4 }}>
        {loading ? (
          <Spin />
        ) : filteredTreeData.length > 0 ? (
          <Tree
            checkable
            checkedKeys={selectedEquipmentKeys}
            onCheck={handleEquipmentCheck}
            treeData={filteredTreeData}
            defaultExpandAll
            showLine={{ showLeafIcon: false }}
            style={{ fontSize: 13 }}
          />
        ) : (
          <Empty description="暂无设备数据" image={Empty.PRESENTED_IMAGE_SIMPLE} />
        )}
      </div>
    </Card>
  );

  const renderTablePanel = () => (
    <Card
      size="small"
      title={
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span>项目与指标</span>
          <Badge count={selectedProjects.length + selectedIndicators.length} style={{ backgroundColor: "#8b5cf6" }} />
        </div>
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
          singleSelect={false}
          showSelectAll={selectedEquipmentKeys.length > 0}
          onSelectAll={handleSelectAll}
          onClearAll={handleClearAll}
          loading={tableLoading}
          showTableBody={selectedEquipmentKeys.length > 0}
          emptyText={
            selectedEquipmentKeys.length === 0 ? "请在左侧选择设备" : "当前设备暂无数据"
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
          <span>方法</span>
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
            description={<div style={{ fontSize: 13, color: "#999" }}>选择项目和/或指标后自动显示关联方法</div>}
            image={Empty.PRESENTED_IMAGE_SIMPLE}
          />
        </div>
      ) : (
        <MethodList
          methods={filteredMethods}
          selectedMethodIds={selectedMethodIds}
          onMethodSelect={handleMethodSelect}
          singleSelect={false}
          loading={methodsLoading}
          emptyText="未找到匹配的试验方法"
          showRelationTag
          style={{ flex: 1, minHeight: 0 }}
        />
      )}
    </Card>
  );

  return (
    <Modal
      title="配置试验参数"
      open={open}
      onCancel={onClose}
      width="90vw"
      style={{ top: 20 }}
      styles={{ body: { overflow: "hidden", padding: "12px 24px" } }}
      footer={[
        <Button key="reset" onClick={handleResetAll}>
          重置所有选择
        </Button>,
        <Button key="cancel" onClick={onClose}>
          取消
        </Button>,
        <Button key="save" type="primary" onClick={handleSave}>
          保存并继续
        </Button>,
      ]}
    >
      <div
        style={{
          height: contentHeight,
          maxHeight: contentHeight,
          background: "#f5f5f5",
          overflow: "hidden",
          display: "flex",
          flexDirection: "column",
          minHeight: 0,
        }}
      >
        <ThreeColumnLayout
          layoutKey="config-modal"
          style={{ flex: 1, minHeight: 0, minWidth: 0 }}
          leftPanel={renderEquipmentPanel()}
          middlePanel={renderTablePanel()}
          rightPanel={renderMethodPanel()}
        />
        <div
          style={{
            marginTop: 12,
            padding: '12px 16px',
            background: '#fff',
            borderRadius: 8,
            border: '1px solid #e5e7eb',
            flexShrink: 0,
          }}
        >
          <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
            <div style={{ flex: '1 1 240px' }}>
              <Text style={{ fontSize: 13, display: 'block', marginBottom: 6 }}>设备名</Text>
              <Input
                placeholder="默认填入所选三级设备名，可修改"
                value={deviceDisplayName}
                onChange={(e) => setDeviceDisplayName(e.target.value)}
              />
            </div>
            <div style={{ flex: '2 1 320px' }}>
              <Text style={{ fontSize: 13, display: 'block', marginBottom: 6 }}>具体环境条件</Text>
              <Input.TextArea
                rows={2}
                placeholder="如：温度 15℃～35℃，湿度 ≤85%，气压 84～107 kPa…"
                value={environmentConditionsText}
                onChange={(e) => setEnvironmentConditionsText(e.target.value)}
              />
            </div>
          </div>
        </div>
      </div>
    </Modal>
  );
}
