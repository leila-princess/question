"use client";

import React from "react";
import { Card, Tag, Empty, Spin, Typography, Badge } from "antd";
import { FileTextOutlined, LinkOutlined } from "@ant-design/icons";

const { Text, Paragraph } = Typography;

export interface MethodFile {
  id: string;
  name: string;
  path?: string;
  source_id?: string;
  source_name?: string;
}

export interface MethodNode {
  id: string;
  name: string;
  type?: string;
  description?: string;
  source_name?: string;
  source_id?: string;
  label?: string;
  files?: MethodFile[];
  _relationType?: "project-only" | "indicator-only" | "both";
  _relevanceScore?: number;
}

export interface MethodListProps {
  methods: MethodNode[];
  selectedMethodIds: string[];
  onMethodSelect: (methodId: string, checked: boolean) => void;
  singleSelect?: boolean;
  loading?: boolean;
  emptyText?: string;
  showRelationTag?: boolean;
  className?: string;
  style?: React.CSSProperties;
}

const RELATION_TYPE_CONFIG = {
  "project-only": { label: "关联项目", color: "#8b5cf6" },
  "indicator-only": { label: "关联指标", color: "#f59e0b" },
  "both": { label: "项目+指标", color: "#10b981" },
};

const MethodList: React.FC<MethodListProps> = ({
  methods,
  selectedMethodIds,
  onMethodSelect,
  singleSelect = true,
  loading = false,
  emptyText = "选择项目和/或指标后自动显示关联方法",
  showRelationTag = true,
  className,
  style,
}) => {
  const sortedMethods = React.useMemo(() => {
    const relationWeight: Record<string, number> = {
      "both": 3,
      "project-only": 2,
      "indicator-only": 1,
    };
    return [...methods].sort((a, b) => {
      const scoreA = a._relevanceScore ?? 0;
      const scoreB = b._relevanceScore ?? 0;
      if (scoreA !== scoreB) return scoreB - scoreA;
      const weightA = relationWeight[a._relationType || "project-only"] ?? 0;
      const weightB = relationWeight[b._relationType || "project-only"] ?? 0;
      if (weightA !== weightB) return weightB - weightA;
      return 0;
    });
  }, [methods]);

  if (loading) {
    return (
      <div style={{ textAlign: "center", padding: 40 }}>
        <Spin description="正在查询关联方法..." />
      </div>
    );
  }

  if (sortedMethods.length === 0) {
    return (
      <Empty description={emptyText} image={Empty.PRESENTED_IMAGE_SIMPLE} />
    );
  }

  return (
    <div
      style={{ display: "flex", flexDirection: "column", gap: 8, overflow: "auto", flex: 1, minHeight: 0, ...style }}
      className={className}
    >
      {sortedMethods.map((method) => {
        const isSelected = selectedMethodIds.includes(method.id);
        const rc = RELATION_TYPE_CONFIG[method._relationType || "project-only"];

        return (
          <div
            key={method.id}
            onClick={() => {
              if (singleSelect) {
                if (!isSelected) {
                  onMethodSelect(method.id, true);
                }
              } else {
                onMethodSelect(method.id, !isSelected);
              }
            }}
            style={{
              border: isSelected ? "2px solid #3b82f6" : "1px solid #e5e7eb",
              borderRadius: 8,
              padding: 12,
              cursor: singleSelect ? (isSelected ? "default" : "pointer") : "pointer",
              background: isSelected ? "rgba(59, 130, 246, 0.05)" : "#fff",
              transition: "all 0.2s",
            }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 6 }}>
              <Text strong style={{ fontSize: 13, flex: 1 }}>{method.name}</Text>
              {showRelationTag && method._relationType && (
                <Tag color={rc.color} style={{ margin: 0, fontSize: 10 }}>
                  {rc.label}
                </Tag>
              )}
            </div>

            {method.type && (
              <Tag color="#3b82f6" style={{ fontSize: 10, marginBottom: 6 }}>
                {method.type}
              </Tag>
            )}

            {method.description && (
              <Paragraph
                type="secondary"
                style={{ fontSize: 12, margin: "4px 0", lineHeight: 1.4 }}
                ellipsis={{ rows: 2 }}
              >
                {method.description}
              </Paragraph>
            )}

            {method.files && method.files.length > 0 && (
              <div style={{ marginBottom: 6, display: "flex", flexDirection: "column", gap: 2 }}>
                {method.files.map((file) => (
                  <a
                    key={file.id}
                    href={`/Standards?source_id=${encodeURIComponent(file.source_id || file.id)}&source_name=${encodeURIComponent(file.source_name || file.name)}`}
                    target="_blank"
                    rel="noopener noreferrer"
                    style={{ fontSize: 11, color: "#3b82f6", textDecoration: "none" }}
                    onClick={(e) => e.stopPropagation()}
                  >
                    <LinkOutlined style={{ marginRight: 4 }} />
                    {file.name}
                  </a>
                ))}
              </div>
            )}

            {method.label && (
              <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
                {method.label.split("、").slice(0, 3).map((tag) => (
                  <Tag key={tag} style={{ fontSize: 10, margin: 0 }}>{tag}</Tag>
                ))}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
};

export default MethodList;
