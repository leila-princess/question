"use client";

import React, { useEffect, useState } from "react";
import { Table, Checkbox, Radio, Button, Empty, Spin, Badge, Tooltip, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import { ClearOutlined, CheckSquareOutlined } from "@ant-design/icons";
import { ENVIRONMENT_ORDER, ENVIRONMENT_MAP } from "@/constants/environments";
import EnvironmentHeaderHelp from "@/components/ThreeColumnLayout/EnvironmentHeaderHelp";
import EnvironmentFiveElementsModal from "@/components/ThreeColumnLayout/EnvironmentFiveElementsModal";
import { ensureEnvironmentFiveElementsLoaded } from "@/components/ThreeColumnLayout/environmentFiveElements";

const { Text } = Typography;

// ==================== Types ====================

export interface ProjectNode {
  id: string;
  name: string;
  type?: string;
  children?: ProjectNode[];
  _env?: string;
}

export interface IndicatorNode {
  id: string;
  name: string;
  type?: string;
  children?: IndicatorNode[];
  description?: string;
  unit?: string;
}

export interface EnvironmentTableRow {
  equipmentTypeId: string;
  equipmentTypeName: string;
  projectsByEnv: Record<string, ProjectNode[]>;
  indicators: IndicatorNode[];
}
export interface selectedEquipmentInfo{
  key: string;
  id: string;
  name: string;
  equipmentTypeId: string;
  equipmentTypeName: string;
  categoryId?: string;
  categoryName?: string;
}

export interface SelectedProjectEntry {
  projectId: string;
  equipmentTypeId: string;
  categoryId?: string;
  nodeData?: Record<string, any>;
}

export interface SelectedIndicatorEntry {
  indicatorId: string;
  equipmentTypeId: string;
  categoryId?: string;
  nodeData?: Record<string, any>;
}

export interface EnvironmentTableProps {
  mode?: "jqcx" | "ssyq" | "config";
  dataSource: EnvironmentTableRow[];
  selectedProjects: SelectedProjectEntry[];
  selectedIndicators: SelectedIndicatorEntry[];
  onProjectSelect: (entry: SelectedProjectEntry, checked: boolean) => void;
  onIndicatorSelect: (entry: SelectedIndicatorEntry, checked: boolean) => void;
  singleSelect?: boolean;
  showSelectAll?: boolean;
  onSelectAll?: () => void;
  onClearAll?: () => void;
  loading?: boolean;
  scrollY?: string | number;
  emptyText?: string;
  className?: string;
  style?: React.CSSProperties;
  /** 检索命中：装备名称 / 项目 / 考核指标 单项弱高亮（与选中态独立，不高亮整行） */
  highlightEquipmentRowIds?: string[];
  highlightProjectIds?: string[];
  highlightIndicatorIds?: string[];
  /** 为 false 时不渲染表体（仅保留固定表头），用于左侧未选装备/设备 */
  showTableBody?: boolean;
  /** 为 false 时只读展示，不显示单选/复选框（如图谱预览） */
  showSelection?: boolean;
}

// ==================== Component ====================

const EnvironmentTable: React.FC<EnvironmentTableProps> = ({
  mode = "jqcx",
  dataSource,
  selectedProjects,
  selectedIndicators,
  onProjectSelect,
  onIndicatorSelect,
  singleSelect = true,
  showSelectAll = false,
  onSelectAll,
  onClearAll,
  loading = false,
  scrollY = "calc(100vh - 280px)",
  emptyText = "当前装备暂无数据",
  className,
  style,
  highlightEquipmentRowIds = [],
  highlightProjectIds = [],
  highlightIndicatorIds = [],
  showTableBody = true,
  showSelection = true,
}) => {
  const [fiveElementsEnv, setFiveElementsEnv] = useState<string | null>(null);

  useEffect(() => {
    if (typeof document === "undefined") return;
    const id = "znwd-env-table-hit-styles";
    if (document.getElementById(id)) return;
    const el = document.createElement("style");
    el.id = id;
    el.textContent = `
      .html-table-wrapper table { border-collapse: collapse; width: 100%; }
      .html-table-wrapper th, .html-table-wrapper td { border: 1px solid #d9d9d9; padding: 6px 8px; }
      .html-table-wrapper th { background: #fafafa; font-weight: bold; }
      .environment-table-panel .environment-data-table .ant-table-thead > tr > th {
        position: sticky;
        top: 0;
        z-index: 3;
        background: #fafafa;
      }
      .environment-table-panel .environment-data-table .ant-table-body {
        flex: 1;
        min-height: 0;
      }
    `;
    document.head.appendChild(el);
  }, []);

  useEffect(() => {
    void ensureEnvironmentFiveElementsLoaded().catch(() => {});
  }, []);

  const hiEq = new Set(highlightEquipmentRowIds);
  const hiProj = new Set(highlightProjectIds);
  const hiInd = new Set(highlightIndicatorIds);
  const hitBg = { background: "rgba(255, 193, 7, 0.14)", borderRadius: 4 };
  const columns: ColumnsType<EnvironmentTableRow> = [
    {
      title: "装备类型",
      key: "equipmentType",
      width: 120,
      fixed: "left" as const,
      render: (_: any, record: EnvironmentTableRow) => (
        <Text
          strong
          style={{
            fontSize: 13,
            ...(hiEq.has(record.equipmentTypeId)
              ? { ...hitBg, padding: "2px 6px", display: "inline-block" }
              : {}),
          }}
        >
          {record.equipmentTypeName}
        </Text>
      ),
    },
  ];

  ENVIRONMENT_ORDER.forEach((envName: string) => {
    columns.push({
      title: (
        <span style={{ display: "inline-flex", alignItems: "center", gap: 4, maxWidth: "100%" }}>
          <Tooltip title={envName}>
            <Text style={{ fontSize: 12 }} ellipsis>
              {envName}
            </Text>
          </Tooltip>
          <EnvironmentHeaderHelp envName={envName} onOpen={setFiveElementsEnv} />
        </span>
      ),
      key: envName,
      width: 160,
      render: (_: any, record: EnvironmentTableRow) => {
        const projects = record.projectsByEnv[envName] || [];
        if (projects.length === 0) {
          return <Text style={{ fontSize: 12, color: "#d9d9d9" }}>—</Text>;
        }

        const categories = projects.filter((p) => p.type === "category");
        const childProjects = projects.filter((p) => p.type !== "category");

        const handleCategoryCheck = (cat: ProjectNode, checked: boolean) => {
          const childIds = (cat.children || []).map((c) => c.id);
          if (singleSelect) {
            selectedProjects
              .filter((p) => p.equipmentTypeId === record.equipmentTypeId)
              .forEach((entry) => onProjectSelect(entry, false));
            const newEntries: SelectedProjectEntry[] = [];
            newEntries.push({ projectId: cat.id, equipmentTypeId: record.equipmentTypeId, categoryId: cat.id, nodeData: cat });
            childIds.forEach((cid) => {
              const childNode = cat.children?.find((c) => c.id === cid);
              newEntries.push({ projectId: cid, equipmentTypeId: record.equipmentTypeId, categoryId: cat.id, nodeData: childNode });
            });
            newEntries.forEach((entry) => onProjectSelect(entry, true));
          } else if (checked) {
            const newEntries: SelectedProjectEntry[] = [];
            if (!selectedProjects.some((p) => p.projectId === cat.id && p.equipmentTypeId === record.equipmentTypeId)) {
              newEntries.push({ projectId: cat.id, equipmentTypeId: record.equipmentTypeId, categoryId: cat.id, nodeData: cat });
            }
            childIds.forEach((cid) => {
              if (!selectedProjects.some((p) => p.projectId === cid && p.equipmentTypeId === record.equipmentTypeId)) {
                const childNode = cat.children?.find((c) => c.id === cid);
                newEntries.push({ projectId: cid, equipmentTypeId: record.equipmentTypeId, categoryId: cat.id, nodeData: childNode });
              }
            });
            newEntries.forEach((entry) => onProjectSelect(entry, true));
          } else {
            const idsToRemove = new Set([cat.id, ...childIds]);
            selectedProjects
              .filter((p) => p.equipmentTypeId === record.equipmentTypeId && idsToRemove.has(p.projectId))
              .forEach((entry) => onProjectSelect(entry, false));
          }
        };

        const handleChildCheck = (child: ProjectNode, categoryId: string, checked: boolean) => {
          if (singleSelect) {
            selectedProjects
              .filter((p) => p.equipmentTypeId === record.equipmentTypeId)
              .forEach((entry) => onProjectSelect(entry, false));
          }
          const entry: SelectedProjectEntry = {
            projectId: child.id,
            equipmentTypeId: record.equipmentTypeId,
            categoryId,
            nodeData: child,
          };
          onProjectSelect(entry, singleSelect ? true : checked);
        };

        const SelectionComponent = singleSelect ? Radio : Checkbox;

        return (
          <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
            {categories.map((cat) => {
              const children = cat.children || [];
              const catChecked = selectedProjects.some(
                (p) => p.projectId === cat.id && p.equipmentTypeId === record.equipmentTypeId
              );
              const allChildrenChecked = children.length > 0 && children.every((child) =>
                selectedProjects.some((p) => p.projectId === child.id && p.equipmentTypeId === record.equipmentTypeId)
              );
              const someChildrenChecked = children.some((child) =>
                selectedProjects.some((p) => p.projectId === child.id && p.equipmentTypeId === record.equipmentTypeId)
              );

              return (
                <div key={cat.id}>
                  <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
                    {showSelection && (singleSelect ? (
                      <SelectionComponent
                        checked={catChecked || allChildrenChecked}
                        onChange={() => {
                          if (!(catChecked || allChildrenChecked)) {
                            handleCategoryCheck(cat, true);
                          }
                        }}
                      />
                    ) : (
                      <SelectionComponent
                        checked={catChecked || allChildrenChecked}
                        indeterminate={!catChecked && !allChildrenChecked && someChildrenChecked}
                        onChange={(e: any) => handleCategoryCheck(cat, e.target.checked)}
                      />
                    ))}
                    <Text style={{ fontSize: 12, fontWeight: "bold", ...(hiProj.has(cat.id) ? hitBg : {}) }}>{cat.name}</Text>
                  </div>
                  {children.map((child) => {
                    const childChecked = selectedProjects.some(
                      (p) => p.projectId === child.id && p.equipmentTypeId === record.equipmentTypeId
                    );
                    return (
                      <div
                        key={child.id}
                        style={{
                          display: "flex",
                          alignItems: "center",
                          gap: 4,
                          marginLeft: 20,
                          ...(hiProj.has(child.id) ? hitBg : {}),
                        }}
                      >
                        {showSelection && (
                        <SelectionComponent
                          checked={childChecked}
                          onChange={() => {
                            if (singleSelect) {
                              if (!childChecked) {
                                handleChildCheck(child, cat.id, true);
                              }
                            } else {
                              handleChildCheck(child, cat.id, !childChecked);
                            }
                          }}
                        />
                        )}
                        <Text style={{ fontSize: 11 }}>{child.name}</Text>
                      </div>
                    );
                  })}
                </div>
              );
            })}
            {childProjects
              .filter((cp) => !categories.some((cat) => (cat.children || []).some((c) => c.id === cp.id)))
              .map((project) => {
                const checked = selectedProjects.some(
                  (p) => p.projectId === project.id && p.equipmentTypeId === record.equipmentTypeId
                );
                return (
                  <div key={project.id} style={{ display: "flex", alignItems: "center", gap: 4, ...(hiProj.has(project.id) ? hitBg : {}) }}>
                    {showSelection && (
                    <SelectionComponent
                      checked={checked}
                      onChange={() => {
                        const entry: SelectedProjectEntry = {
                          projectId: project.id,
                          equipmentTypeId: record.equipmentTypeId,
                          nodeData: project,
                        };
                        if (singleSelect) {
                          if (!checked) {
                            selectedProjects
                              .filter((p) => p.equipmentTypeId === record.equipmentTypeId)
                              .forEach((e) => onProjectSelect(e, false));
                            onProjectSelect(entry, true);
                          }
                        } else {
                          onProjectSelect(entry, !checked);
                        }
                      }}
                    />
                    )}
                    <Text style={{ fontSize: 11 }}>{project.name}</Text>
                  </div>
                );
              })}
          </div>
        );
      },
    });
  });

  columns.push({
    title: <Text style={{ fontSize: 12 }}>考核指标</Text>,
    key: "indicators",
    fixed: "right" as const,
    width: 180,
    render: (_: any, record: EnvironmentTableRow) => {
      if (record.indicators.length === 0) {
        return <Text style={{ fontSize: 12, color: "#d9d9d9" }}>—</Text>;
      }

      const SelectionComponent = singleSelect ? Radio : Checkbox;
      const categories = record.indicators.filter((i) => i.type === "category");
      const leafOrLegacy = record.indicators.filter((i) => i.type !== "category");

      const handleCategoryCheck = (cat: IndicatorNode, checked: boolean) => {
        const childIds = (cat.children || []).map((c) => c.id);
        if (singleSelect) {
          selectedIndicators
            .filter((i) => i.equipmentTypeId === record.equipmentTypeId)
            .forEach((entry) => onIndicatorSelect(entry, false));
          const newEntries: SelectedIndicatorEntry[] = [];
          newEntries.push({ indicatorId: cat.id, equipmentTypeId: record.equipmentTypeId, nodeData: cat as any });
          childIds.forEach((cid) => {
            const childNode = cat.children?.find((c) => c.id === cid);
            newEntries.push({
              indicatorId: cid,
              equipmentTypeId: record.equipmentTypeId,
              categoryId: cat.id,
              nodeData: childNode as any,
            });
          });
          newEntries.forEach((entry) => onIndicatorSelect(entry, true));
        } else if (checked) {
          const newEntries: SelectedIndicatorEntry[] = [];
          if (!selectedIndicators.some((i) => i.indicatorId === cat.id && i.equipmentTypeId === record.equipmentTypeId)) {
            newEntries.push({ indicatorId: cat.id, equipmentTypeId: record.equipmentTypeId, nodeData: cat as any });
          }
          childIds.forEach((cid) => {
            if (!selectedIndicators.some((i) => i.indicatorId === cid && i.equipmentTypeId === record.equipmentTypeId)) {
              const childNode = cat.children?.find((c) => c.id === cid);
              newEntries.push({
                indicatorId: cid,
                equipmentTypeId: record.equipmentTypeId,
                categoryId: cat.id,
                nodeData: childNode as any,
              });
            }
          });
          newEntries.forEach((entry) => onIndicatorSelect(entry, true));
        } else {
          const idsToRemove = new Set([cat.id, ...childIds]);
          selectedIndicators
            .filter((i) => i.equipmentTypeId === record.equipmentTypeId && idsToRemove.has(i.indicatorId))
            .forEach((entry) => onIndicatorSelect(entry, false));
        }
      };

      return (
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          {categories.map((cat) => {
            const children = cat.children || [];
            const catChecked = selectedIndicators.some(
              (i) => i.indicatorId === cat.id && i.equipmentTypeId === record.equipmentTypeId
            );
            const allChildrenChecked = children.length > 0 && children.every((child) =>
              selectedIndicators.some((i) => i.indicatorId === child.id && i.equipmentTypeId === record.equipmentTypeId)
            );
            const someChildrenChecked = children.some((child) =>
              selectedIndicators.some((i) => i.indicatorId === child.id && i.equipmentTypeId === record.equipmentTypeId)
            );
            return (
              <div key={cat.id}>
                <div style={{ display: "flex", alignItems: "center", gap: 4, ...(hiInd.has(cat.id) ? hitBg : {}) }}>
                  {showSelection && (singleSelect ? (
                    <SelectionComponent
                      checked={catChecked || allChildrenChecked}
                      onChange={() => {
                        if (!(catChecked || allChildrenChecked)) {
                          handleCategoryCheck(cat, true);
                        }
                      }}
                    />
                  ) : (
                    <SelectionComponent
                      checked={catChecked || allChildrenChecked}
                      indeterminate={!catChecked && !allChildrenChecked && someChildrenChecked}
                      onChange={(e: any) => handleCategoryCheck(cat, e.target.checked)}
                    />
                  ))}
                  <Text style={{ fontSize: 12, fontWeight: "bold" }}>{cat.name}</Text>
                </div>
                {children.map((indicator) => {
                  const checked = selectedIndicators.some(
                    (i) => i.indicatorId === indicator.id && i.equipmentTypeId === record.equipmentTypeId
                  );
                  return (
                    <div key={indicator.id} style={{ display: "flex", alignItems: "center", gap: 4, marginLeft: 20, ...(hiInd.has(indicator.id) ? hitBg : {}) }}>
                      {showSelection && (
                      <SelectionComponent
                        checked={checked}
                        onChange={() => {
                          const entry: SelectedIndicatorEntry = {
                            indicatorId: indicator.id,
                            equipmentTypeId: record.equipmentTypeId,
                            categoryId: cat.id,
                            nodeData: indicator,
                          };
                          if (singleSelect) {
                            if (!checked) {
                              selectedIndicators
                                .filter((i) => i.equipmentTypeId === record.equipmentTypeId)
                                .forEach((e) => onIndicatorSelect(e, false));
                              onIndicatorSelect(entry, true);
                            }
                          } else {
                            onIndicatorSelect(entry, !checked);
                          }
                        }}
                      />
                      )}
                      <Text style={{ fontSize: 12 }}>{indicator.name}</Text>
                    </div>
                  );
                })}
              </div>
            );
          })}
          {leafOrLegacy
            .filter((i) => !categories.some((cat) => (cat.children || []).some((c) => c.id === i.id)))
            .map((indicator) => {
            const checked = selectedIndicators.some(
              (i) => i.indicatorId === indicator.id && i.equipmentTypeId === record.equipmentTypeId
            );
            return (
              <div key={indicator.id} style={{ display: "flex", alignItems: "center", gap: 4, ...(hiInd.has(indicator.id) ? hitBg : {}) }}>
                {showSelection && (
                <SelectionComponent
                  checked={checked}
                  onChange={() => {
                    const entry: SelectedIndicatorEntry = {
                      indicatorId: indicator.id,
                      equipmentTypeId: record.equipmentTypeId,
                      categoryId: categories.find((cat) =>
                        (cat.children || []).some((c) => c.id === indicator.id),
                      )?.id,
                      nodeData: indicator,
                    };
                    if (singleSelect) {
                      if (!checked) {
                        selectedIndicators
                          .filter((i) => i.equipmentTypeId === record.equipmentTypeId)
                          .forEach((e) => onIndicatorSelect(e, false));
                        onIndicatorSelect(entry, true);
                      }
                    } else {
                      onIndicatorSelect(entry, !checked);
                    }
                  }}
                />
                )}
                <Text style={{ fontSize: 12 }}>{indicator.name}</Text>
              </div>
            );
          })}
        </div>
      );
    },
  });

  const orderLength = ENVIRONMENT_ORDER.length;
  const tableScrollX = 120 + orderLength * 160 + 180;
  const hasBodyRows = showTableBody && dataSource.length > 0;

  return (
    <div
      className={`environment-table-panel${className ? ` ${className}` : ""}`}
      style={{
        display: "flex",
        flexDirection: "column",
        flex: 1,
        minHeight: 0,
        minWidth: 0,
        height: "100%",
        ...style,
      }}
    >
      {showSelectAll && (
        <div style={{ padding: "8px 12px", borderBottom: "1px solid #f0f0f0", display: "flex", gap: 8 }}>
          <Button size="small" icon={<CheckSquareOutlined />} onClick={onSelectAll}>
            一键全选
          </Button>
          <Button size="small" icon={<ClearOutlined />} onClick={onClearAll}>
            清除全选
          </Button>
          <Badge
            count={selectedProjects.length + selectedIndicators.length}
            style={{ backgroundColor: "#8b5cf6", marginLeft: "auto" }}
            showZero
          />
        </div>
      )}
      <Table
        className="environment-data-table"
        columns={columns}
        dataSource={hasBodyRows ? dataSource : []}
        rowKey={(record) => record.equipmentTypeId}
        rowClassName={() => ""}
        size="small"
        pagination={false}
        loading={loading && showTableBody}
        sticky
        scroll={{
          x: tableScrollX,
          ...(hasBodyRows ? { y: scrollY } : {}),
        }}
        locale={{
          emptyText: (
            <div
              style={{
                display: "flex",
                justifyContent: "center",
                alignItems: "center",
                minHeight: 120,
                padding: "32px 16px",
              }}
            >
              {loading && showTableBody ? (
                <Spin description="加载数据中..." />
              ) : (
                <Empty description={emptyText} image={Empty.PRESENTED_IMAGE_SIMPLE} />
              )}
            </div>
          ),
        }}
      />
      <EnvironmentFiveElementsModal
        open={!!fiveElementsEnv}
        envName={fiveElementsEnv}
        onClose={() => setFiveElementsEnv(null)}
      />
    </div>
  );
};

export default EnvironmentTable;
