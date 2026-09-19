"use client";

import React from "react";
import {
  Tag,
  Empty,
  Spin,
  Typography,
  Tooltip,
  Space,
  Drawer,
  Radio,
  Checkbox,
  Divider,
  Row,
  Col,
  Card,
} from "antd";
import {
  LinkOutlined,
  ProjectOutlined,
  DashboardOutlined,
  ExperimentOutlined,
  FileTextOutlined,
} from "@ant-design/icons";

const { Text, Paragraph, Title } = Typography;

/** 图谱 File 节点；打开 Standards 阅读页（左侧原文 + 右侧摘要/问答） */
export interface MethodFile {
  id: string;
  name: string;
  path?: string;
  /** 图库 minIo_id，即 MinIO 对象路径（与后端 File.minIo_id 一致，接口里多为小写键 minio_id） */
  minio_id?: string;
  source_id?: string;
  source_name?: string;
}

// 关联节点接口
export interface RelatedNode {
  id: string;
  name: string;
  rate?: number;
}

export interface MethodNode {
  id: string;
  name: string;
  description?: string; // 方法简介/摘要
  source_name?: string;
  source_id?: string;
  files?: MethodFile[];
  _relationType?: "project-only" | "indicator-only" | "both";
  /** 图库试验方法正文（HTML/Markdown），Neo4j 多为 SYNR，接口常规范为小写 synr */
  synr?: string;
  SYNR?: string;
  SYTJ?: string;
  SYGC?: string;
  JGPD?: string;
  sy?: string;
  label?: string;
  type?: string;

  // 从后端获取的关联信息
  related_projects?: RelatedNode[];
  related_indicators?: RelatedNode[];
  /** 部分接口驼峰 */
  relatedProjects?: RelatedNode[];
  relatedIndicators?: RelatedNode[];
  max_rate?: number;
  imageurls?: string | string[];
}

export interface MethodListProps {
  methods: MethodNode[];
  selectedMethodIds: string[];
  onMethodSelect: (methodId: string, checked: boolean) => void;
  singleSelect?: boolean;
  loading?: boolean;
  emptyText?: string;
  /** 是否在卡片上显示「关联项目 / 关联指标 / 项目+指标」标签 */
  showRelationTag?: boolean;
  className?: string;
  style?: React.CSSProperties;
}

function pickMethodSection(m: MethodNode, key: "SYTJ" | "SYGC" | "JGPD"): string {
  const lower = key.toLowerCase();
  const raw =
    (m as unknown as Record<string, unknown>)[key] ??
    (m as unknown as Record<string, unknown>)[lower] ??
    "";
  return typeof raw === "string" ? raw.trim() : "";
}

function MethodThreeColumnSections({ method }: { method: MethodNode }) {
  const sections = [
    { key: "SYTJ" as const, title: "试验条件", content: pickMethodSection(method, "SYTJ") },
    { key: "SYGC" as const, title: "试验过程", content: pickMethodSection(method, "SYGC") },
    { key: "JGPD" as const, title: "结果判定", content: pickMethodSection(method, "JGPD") },
  ].filter((s) => s.content);
  if (!sections.length) return null;
  return (
    <div style={{ marginBottom: 16 }}>
      <Title level={5} style={{ marginBottom: 8 }}>
        方法要点
      </Title>
      <Row gutter={12}>
        {sections.map((s) => (
          <Col key={s.key} xs={24} md={sections.length === 1 ? 24 : 8}>
            <Card size="small" title={s.title} style={{ height: "100%" }}>
              <div
                className="method-list-drawer-synr"
                style={{ fontSize: 13, lineHeight: 1.6, maxHeight: 280, overflow: "auto" }}
                dangerouslySetInnerHTML={{ __html: sanitizeMethodHtml(s.content) }}
              />
            </Card>
          </Col>
        ))}
      </Row>
    </div>
  );
}

function pickSynrHtml(m: MethodNode): string {
  const raw = m.synr ?? m.SYNR ?? "";
  return typeof raw === "string" ? raw : "";
}

/** 去掉 script/style，降低 innerHTML 注入风险（内容仍可能含其它 HTML） */
function sanitizeMethodHtml(html: string): string {
  if (!html) return "";
  return html
    .replace(/<script\b[^<]*(?:(?!<\/script>)<[^<]*)*<\/script>/gi, "")
    .replace(/<style\b[^<]*(?:(?!<\/style>)<[^<]*)*<\/style>/gi, "");
}

function relatedProjectsOf(m: MethodNode): RelatedNode[] {
  const a = m.related_projects;
  const b = m.relatedProjects;
  if (Array.isArray(a) && a.length) return a;
  if (Array.isArray(b) && b.length) return b;
  return Array.isArray(a) ? a : Array.isArray(b) ? b : [];
}

function relatedIndicatorsOf(m: MethodNode): RelatedNode[] {
  const a = m.related_indicators;
  const b = m.relatedIndicators;
  if (Array.isArray(a) && a.length) return a;
  if (Array.isArray(b) && b.length) return b;
  return Array.isArray(a) ? a : Array.isArray(b) ? b : [];
}

// 格式化关联度：支持 0~1 小数或已写入的百分数（如 85）
const formatRate = (rate?: number | null): string => {
  if (rate === undefined || rate === null || Number.isNaN(Number(rate))) return "";
  const n = Number(rate);
  if (n > 1) return `${Math.round(n)}%`;
  return `${Math.round(n * 100)}%`;
};

// 获取关联度颜色（0 为有效低相关，不与「无数据」混淆）
const getRateColor = (rate?: number | null): string => {
  if (rate === undefined || rate === null || Number.isNaN(Number(rate))) return "#8c8c8c";
  const n = Number(rate);
  const x = n > 1 ? Math.min(n / 100, 1) : n;
  if (x >= 0.8) return "#52c41a";
  if (x >= 0.6) return "#1890ff";
  if (x >= 0.4) return "#faad14";
  return "#ff4d4f";
};

function effectiveMaxRate(method: MethodNode): number | undefined {
  const fromField = method.max_rate;
  if (fromField !== undefined && fromField !== null && Number.isFinite(Number(fromField))) {
    return Number(fromField);
  }
  const nums: number[] = [];
  for (const p of relatedProjectsOf(method)) {
    if (p.rate !== undefined && p.rate !== null && Number.isFinite(Number(p.rate))) {
      nums.push(Number(p.rate));
    }
  }
  for (const i of relatedIndicatorsOf(method)) {
    if (i.rate !== undefined && i.rate !== null && Number.isFinite(Number(i.rate))) {
      nums.push(Number(i.rate));
    }
  }
  if (!nums.length) return undefined;
  return Math.max(...nums);
}

const RELATION_TYPE_CONFIG = {
  "project-only": { label: "关联项目", color: "#8b5cf6" },
  "indicator-only": { label: "关联指标", color: "#f59e0b" },
  both: { label: "项目+指标", color: "#10b981" },
} as const;

type RelationTypeKey = keyof typeof RELATION_TYPE_CONFIG;

/** 单个项目或指标：名称 + 关联度（与 JQCX /methods 返回的 related_* 一致） */
function RelatedEntityTag({
  item,
  kind,
}: {
  item: RelatedNode;
  kind: "project" | "indicator";
}) {
  const Icon = kind === "project" ? ProjectOutlined : DashboardOutlined;
  const color = kind === "project" ? "cyan" : "green";
  const idLabel = kind === "project" ? "项目" : "指标";
  const r = item.rate;
  const hasR = r !== undefined && r !== null && Number.isFinite(Number(r));
  return (
    <Tooltip title={`${idLabel}ID: ${item.id}${hasR ? ` · 关系关联度 rate=${r}` : ""}`}>
      <Tag color={color} style={{ fontSize: 12, margin: 0 }} onClick={(e) => e.stopPropagation()}>
        <Icon style={{ marginRight: 4 }} />
        {item.name}
        {hasR ? (
          <span style={{ marginLeft: 6, color: getRateColor(r), fontWeight: 500 }}>
            ({formatRate(Number(r))})
          </span>
        ) : (
          <span style={{ marginLeft: 6, color: "#bfbfbf", fontWeight: 400 }}>（无关联度数据）</span>
        )}
      </Tag>
    </Tooltip>
  );
}

function DrawerRelatedRow({ item, kind }: { item: RelatedNode; kind: "project" | "indicator" }) {
  const r = item.rate;
  const hasR = r !== undefined && r !== null && Number.isFinite(Number(r));
  const label = kind === "project" ? "项目" : "指标";
  return (
    <div
      style={{
        display: "flex",
        alignItems: "flex-start",
        gap: 10,
        padding: "8px 0",
        borderBottom: "1px solid #f0f0f0",
      }}
    >
      <Tag color={kind === "project" ? "cyan" : "green"} style={{ margin: 0, flexShrink: 0 }}>
        {label}
      </Tag>
      <div style={{ flex: 1, minWidth: 0 }}>
        <Text strong style={{ display: "block", fontSize: 13 }}>
          {item.name || item.id}
        </Text>
        <Text type="secondary" copyable style={{ fontSize: 12 }}>
          {item.id}
        </Text>
        <div style={{ marginTop: 4 }}>
          <Text type="secondary" style={{ fontSize: 12 }}>
            关联度：
          </Text>
          {hasR ? (
            <Text style={{ fontSize: 12, marginLeft: 6, color: getRateColor(r), fontWeight: 600 }}>
              {formatRate(Number(r))}
            </Text>
          ) : (
            <Text style={{ fontSize: 12, marginLeft: 6, color: "#bfbfbf" }}>无数据</Text>
          )}
        </div>
      </div>
    </div>
  );
}

function resolveRelationType(
  method: MethodNode,
  hasProjects: boolean,
  hasIndicators: boolean,
): RelationTypeKey | undefined {
  if (method._relationType) return method._relationType;
  if (hasProjects && hasIndicators) return "both";
  if (hasProjects) return "project-only";
  if (hasIndicators) return "indicator-only";
  return undefined;
}

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
  const [drawerOpen, setDrawerOpen] = React.useState(false);
  const [detailMethod, setDetailMethod] = React.useState<MethodNode | null>(null);

  const openDetail = React.useCallback((m: MethodNode) => {
    setDetailMethod(m);
    setDrawerOpen(true);
  }, []);

  const closeDetail = React.useCallback(() => {
    setDrawerOpen(false);
  }, []);

  // 按相关度排序（使用后端返回的 max_rate）
  const sortedMethods = React.useMemo(() => {
    return [...methods].sort((a, b) => {
      const rateA = effectiveMaxRate(a) ?? 0;
      const rateB = effectiveMaxRate(b) ?? 0;
      if (rateA !== rateB) return rateB - rateA;
      return (a.name || "").localeCompare(b.name || "");
    });
  }, [methods]);

  const drawerSynrHtml = detailMethod ? sanitizeMethodHtml(pickSynrHtml(detailMethod)) : "";
  const drawerProjects = detailMethod ? relatedProjectsOf(detailMethod) : [];
  const drawerIndicators = detailMethod ? relatedIndicatorsOf(detailMethod) : [];

  const rootShell: React.CSSProperties = {
    display: "flex",
    flexDirection: "column",
    flex: 1,
    minHeight: 0,
    overflow: "hidden",
    ...style,
  };

  if (loading) {
    return (
      <div className={className} style={rootShell}>
        <div
          style={{
            flex: 1,
            minHeight: 0,
            overflow: "auto",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            padding: 40,
          }}
        >
          <Spin description="正在查询关联方法..." />
        </div>
      </div>
    );
  }

  if (sortedMethods.length === 0) {
    return (
      <div className={className} style={rootShell}>
        <div
          style={{
            flex: 1,
            minHeight: 0,
            overflow: "auto",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            padding: 16,
          }}
        >
          <Empty description={emptyText} image={Empty.PRESENTED_IMAGE_SIMPLE} />
        </div>
      </div>
    );
  }

  return (
    <div className={className} style={rootShell}>
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          gap: 12,
          flex: 1,
          minHeight: 0,
          overflow: "auto",
        }}
      >
      <div
        style={{
          padding: "6px 12px",
          backgroundColor: "#f0f5ff",
          borderRadius: 6,
          fontSize: 12,
          color: "#1890ff",
          display: "flex",
          alignItems: "center",
          gap: 8,
          flexShrink: 0,
        }}
      >
        <ExperimentOutlined />
        <span>共 {sortedMethods.length} 个方法，已按相关度排序；点击卡片右侧区域查看方法正文</span>
      </div>

      {sortedMethods.map((method) => {
        const isSelected = selectedMethodIds.includes(method.id);
        const rp = relatedProjectsOf(method);
        const ri = relatedIndicatorsOf(method);
        const hasProjects = rp.length > 0;
        const hasIndicators = ri.length > 0;
        const relationKey = resolveRelationType(method, hasProjects, hasIndicators);
        const relationCfg = relationKey ? RELATION_TYPE_CONFIG[relationKey] : undefined;
        const cardMax = effectiveMaxRate(method);
        const showCardMaxRate = cardMax !== undefined && cardMax !== null && Number.isFinite(cardMax);

        const onToggleSelect = (checked: boolean) => {
          onMethodSelect(method.id, checked);
        };

        return (
          <div
            key={method.id}
            style={{
              border: isSelected ? "2px solid #3b82f6" : "1px solid #e5e7eb",
              borderRadius: 8,
              padding: "10px 12px",
              display: "flex",
              gap: 10,
              alignItems: "flex-start",
              background: isSelected ? "rgba(59, 130, 246, 0.04)" : "#fff",
              transition: "all 0.2s",
            }}
          >
            <div
              onClick={(e) => e.stopPropagation()}
              style={{ paddingTop: 2, flexShrink: 0 }}
              onMouseDown={(e) => e.stopPropagation()}
            >
              {singleSelect ? (
                <Radio
                  checked={isSelected}
                  onChange={() => {
                    if (!isSelected) onMethodSelect(method.id, true);
                  }}
                />
              ) : (
                <Checkbox checked={isSelected} onChange={(e) => onToggleSelect(e.target.checked)} />
              )}
            </div>

            <div
              role="button"
              tabIndex={0}
              onClick={() => openDetail(method)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  openDetail(method);
                }
              }}
              style={{
                flex: 1,
                minWidth: 0,
                cursor: "pointer",
                outline: "none",
              }}
            >
              <div style={{ marginBottom: 8, display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                <Text strong style={{ fontSize: 14 }}>
                  {method.name}
                </Text>
                <Tooltip title="查看方法正文（HTML）与关联详情">
                  <Tag icon={<FileTextOutlined />} color="blue" style={{ margin: 0, fontSize: 11, cursor: "pointer" }}>
                    详情
                  </Tag>
                </Tooltip>
              </div>
              <Space size="small" wrap style={{ marginTop: 2 }}>
                {showRelationTag && relationCfg && (
                  <Tag color={relationCfg.color} style={{ margin: 0, fontSize: 11 }}>
                    {relationCfg.label}
                  </Tag>
                )}
                {showCardMaxRate && (
                  <Tag color={getRateColor(cardMax)} style={{ margin: 0, fontSize: 11 }}>
                    综合相关度 {formatRate(cardMax)}
                  </Tag>
                )}
              </Space>

              {method.description && (
                <Paragraph
                  type="secondary"
                  style={{ fontSize: 12, margin: "8px 0", lineHeight: 1.5 }}
                  ellipsis={{ rows: 2 }}
                >
                  {method.description}
                </Paragraph>
              )}

              {hasProjects && (
                <div style={{ marginBottom: 10 }}>
                  <Text type="secondary" style={{ fontSize: 11, display: "block", marginBottom: 6 }}>
                    关联项目
                  </Text>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                    {rp.map((project) => (
                      <RelatedEntityTag key={project.id} item={project} kind="project" />
                    ))}
                  </div>
                </div>
              )}

              {hasIndicators && (
                <div style={{ marginBottom: 10 }}>
                  <Text type="secondary" style={{ fontSize: 11, display: "block", marginBottom: 6 }}>
                    关联指标
                  </Text>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                    {ri.map((indicator) => (
                      <RelatedEntityTag key={indicator.id} item={indicator} kind="indicator" />
                    ))}
                  </div>
                </div>
              )}

              {!hasProjects && !hasIndicators && (
                <div style={{ marginBottom: 10, padding: "6px 10px", background: "#fff1f0", borderRadius: 4 }}>
                  <Text type="danger" style={{ fontSize: 12 }}>
                    暂无关联的项目或指标
                  </Text>
                </div>
              )}

              {method.files && method.files.length > 0 ? (
                <div style={{ marginTop: 8, paddingTop: 8, borderTop: "1px dashed #e5e7eb" }}>
                  <Text type="secondary" style={{ fontSize: 11, display: "block", marginBottom: 6 }}>
                    来源文件
                  </Text>
                  <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                    {method.files.map((file) => {
                      const fileId = file.id;
                      const label = file.name || fileId;
                      const minioHint = file.minio_id;
                      const href = `/Standards?source_id=${encodeURIComponent(fileId)}&source_name=${encodeURIComponent(label)}`;
                      const inner = (
                        <>
                          <LinkOutlined style={{ marginRight: 4 }} />
                          {label}
                        </>
                      );
                      return minioHint ? (
                        <Tooltip key={fileId} title="点击查看标准原文">
                          <a
                            href={href}
                            target="_blank"
                            rel="noopener noreferrer"
                            style={{ fontSize: 12, color: "#3b82f6", textDecoration: "none" }}
                            onClick={(e) => e.stopPropagation()}
                          >
                            {inner}
                          </a>
                        </Tooltip>
                      ) : (
                        <a
                          key={fileId}
                          href={href}
                          target="_blank"
                          rel="noopener noreferrer"
                          style={{ fontSize: 12, color: "#3b82f6", textDecoration: "none" }}
                          onClick={(e) => e.stopPropagation()}
                        >
                          {inner}
                        </a>
                      );
                    })}
                  </div>
                </div>
              ) : (
                <div style={{ marginTop: 8, paddingTop: 8, borderTop: "1px dashed #e5e7eb" }}>
                  <Text type="secondary" style={{ fontSize: 11 }}>
                    暂无关联的标准文件
                  </Text>
                </div>
              )}
            </div>
          </div>
        );
      })}

      </div>

      <Drawer
        title={
          <span style={{ fontWeight: 600, fontSize: 15 }}>
            {detailMethod?.name || "方法详情"}
          </span>
        }
        placement="right"
        size={560}
        open={drawerOpen}
        onClose={closeDetail}
        destroyOnClose
        styles={{ body: { paddingTop: 12 } }}
      >
        {detailMethod && (
          <>
            <Title level={5} style={{ marginTop: 0, marginBottom: 8 }}>
              方法名称
            </Title>
            <Text>{detailMethod.name}</Text>

            <Divider style={{ margin: "16px 0" }} />

            <Divider style={{ margin: "16px 0" }} />

            <MethodThreeColumnSections method={detailMethod} />

            <Title level={5} style={{ marginBottom: 8 }}>
              方法正文
            </Title>
            {drawerSynrHtml ? (
              <div
                className="method-list-drawer-synr"
                style={{
                  border: "1px solid #f0f0f0",
                  borderRadius: 8,
                  padding: 12,
                  maxHeight: "min(60vh, 520px)",
                  overflow: "auto",
                  background: "#fafafa",
                  fontSize: 14,
                  lineHeight: 1.6,
                }}
                dangerouslySetInnerHTML={{ __html: drawerSynrHtml }}
              />
            ) : (
              <Text type="secondary">暂无方法正文</Text>
            )}

            <Divider style={{ margin: "16px 0" }} />

            <Title level={5} style={{ marginBottom: 8 }}>
              关联项目
            </Title>
            {drawerProjects.length > 0 ? (
              drawerProjects.map((p) => <DrawerRelatedRow key={p.id} item={p} kind="project" />)
            ) : (
              <Text type="secondary">无</Text>
            )}

            <Divider style={{ margin: "16px 0" }} />

            <Title level={5} style={{ marginBottom: 8 }}>
              关联指标
            </Title>
            {drawerIndicators.length > 0 ? (
              drawerIndicators.map((i) => <DrawerRelatedRow key={i.id} item={i} kind="indicator" />)
            ) : (
              <Text type="secondary">无</Text>
            )}
          </>
        )}
      </Drawer>

      <style
        dangerouslySetInnerHTML={{
          __html: `
.method-list-drawer-synr table { border-collapse: collapse; width: 100%; margin: 8px 0; }
.method-list-drawer-synr th, .method-list-drawer-synr td { border: 1px solid #d9d9d9; padding: 6px 8px; }
.method-list-drawer-synr img { max-width: 100%; height: auto; }
`,
        }}
      />
    </div>
  );
};

export default MethodList;
