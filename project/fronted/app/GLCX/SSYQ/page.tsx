"use client";

import React, { useState, useEffect } from "react";
import {
  Card,
  Row,
  Col,
  Input,
  Button,
  Tag,
  Empty,
  Spin,
  Badge,
  App,
  Space,
} from "antd";
import {
  SearchOutlined,
  ReloadOutlined,
  ExperimentOutlined,
  FileTextOutlined,
  ClearOutlined,
  LeftOutlined,
  RightOutlined,
} from "@ant-design/icons";
import KnowledgeGraph, { GraphData } from "@/components/KnowledgeGraph/KnowledgeGraph";
import EnvironmentTable, {
  type EnvironmentTableRow,
  type SelectedProjectEntry,
  type SelectedIndicatorEntry,
} from "@/components/EnvironmentTable";
import MethodList, { type MethodNode } from "@/components/MethodList/index";
import {
  fetchSearch,
  fetchMethods,
  buildSearchCategoryStats,
  buildSearchHighlightIds,
  formatSearchStatsMessage,
  type SearchGraphData,
  type SearchCategoryStats,
  type SearchHighlightIds,
} from "./api";
import { fetchIndicators } from "../JQCX/api";
import { enrichGraphWithIndicatorHas } from "@/utils/enrichGraphWithIndicatorHas";

const nodeColors = {
  EquipmentType: '#1890ff',
  Equipment: '#52c41a',
  Environment: '#722ed1',
  TestProject: '#fa8c16',
  Indicator: '#13c2c2',
  File: '#eb2f96',
  TestMethod: '#faad14',
};

const edgeColors = {
  HAS: '#52c41a',
  CON: '#13c2c2',
  '关联': '#fa8c16',
};

const nodeLabels = {
  EquipmentType: '设备类型',
  Equipment: '具体设备',
  Environment: '环境',
  TestProject: '测试项目',
  Indicator: '指标',
  File: '文件',
  TestMethod: '试验方法',
};

const edgeLabels = {
  HAS: '包含',
  CON: '关联测试',
  '关联': '关联',
};

const SSYQPage: React.FC = () => {
  const { message } = App.useApp();
  const [searchKeyword, setSearchKeyword] = useState('');
  const [searchLoading, setSearchLoading] = useState(false);
  const [tableRows, setTableRows] = useState<EnvironmentTableRow[]>([]);
  const [graphData, setGraphData] = useState<SearchGraphData | null>(null);
  const [graphKey, setGraphKey] = useState(0);

  const [selectedProjects, setSelectedProjects] = useState<SelectedProjectEntry[]>([]);
  const [selectedIndicators, setSelectedIndicators] = useState<SelectedIndicatorEntry[]>([]);

  const [filteredMethods, setFilteredMethods] = useState<MethodNode[]>([]);
  const [selectedMethodIds, setSelectedMethodIds] = useState<string[]>([]);
  const [methodsLoading, setMethodsLoading] = useState(false);

  const [methodCollapsed, setMethodCollapsed] = useState(false);

  /** 必须用 state：ref 变化不会触发重绘，会导致「已搜索但图谱区不出现」或布局与状态不一致 */
  const [hasSearched, setHasSearched] = useState(false);
  const [searchStats, setSearchStats] = useState<SearchCategoryStats | null>(null);
  const [searchHighlights, setSearchHighlights] = useState<SearchHighlightIds | null>(null);

  const handleSearch = async () => {
    if (!searchKeyword.trim()) {
      message.warning('请输入搜索关键词');
      return;
    }
    setSearchLoading(true);
    setSelectedProjects([]);
    setSelectedIndicators([]);
    setFilteredMethods([]);
    setSelectedMethodIds([]);
    try {
      const result = await fetchSearch(searchKeyword.trim());
      setHasSearched(true);
      const stats = buildSearchCategoryStats(result.searchResults);
      setSearchStats(stats);
      setSearchHighlights(buildSearchHighlightIds(result.searchResults));

      const envRows: EnvironmentTableRow[] = [];
      const seenEqIds = new Set<string>();

      for (const row of result.tableRows || []) {
        if (row.type === 'equipment' && row.equipmentId && !seenEqIds.has(row.equipmentId)) {
          seenEqIds.add(row.equipmentId);
          envRows.push({
            equipmentTypeId: row.equipmentId,
            equipmentTypeName: row.equipmentName || row.equipmentId,
            projectsByEnv: row.projectsByEnv || {},
            indicators: row.indicators || [],
          });
        } else if ((row.type === 'project' || row.type === 'indicator') && row.projectsByEnv && row.equipments) {
          for (const eq of row.equipments) {
            if (!eq.id || seenEqIds.has(eq.id)) continue;
            seenEqIds.add(eq.id);
            envRows.push({
              equipmentTypeId: eq.id,
              equipmentTypeName: eq.name || eq.id,
              projectsByEnv: row.projectsByEnv || {},
              indicators: row.indicators || [],
            });
          }
        }
      }

      const enrichedRows = await Promise.all(
        envRows.map(async (row) => {
          const indicators = await fetchIndicators(row.equipmentTypeId);
          return {
            ...row,
            indicators: indicators.length > 0 ? indicators : row.indicators,
          };
        })
      );
      setTableRows(enrichedRows);
      setGraphData(enrichGraphWithIndicatorHas(result.graphData || null, enrichedRows));
      setGraphKey((prev) => prev + 1);
      const totalNodes = result.graphData?.nodes?.length || 0;
      const statsMsg = formatSearchStatsMessage(stats);
      if (stats.equipments + stats.projects + stats.indicators === 0 && totalNodes === 0) {
        message.info('未找到匹配的数据');
      } else {
        message.success(statsMsg);
      }
    } catch (err: any) {
      message.error(`搜索失败: ${err.message}`);
    } finally {
      setSearchLoading(false);
    }
  };

  const handleReset = () => {
    setSearchKeyword('');
    setTableRows([]);
    setGraphData(null);
    setGraphKey(0);
    setHasSearched(false);
    setSearchStats(null);
    setSearchHighlights(null);
    setSelectedProjects([]);
    setSelectedIndicators([]);
    setFilteredMethods([]);
    setSelectedMethodIds([]);
  };

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
        message.error("方法查询失败");
        console.error(error);
      } finally {
        setMethodsLoading(false);
      }
    }, 300);

    return () => clearTimeout(timer);
  }, [selectedProjects, selectedIndicators]);

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

  const graphDataForComponent: GraphData | undefined = graphData
    ? { nodes: graphData.nodes, edges: graphData.edges }
    : undefined;

  const graphNodeCount = graphData?.nodes?.length ?? 0;

  const renderSearchStats = () => {
    if (!hasSearched || !searchStats) return null;
    const { equipments, projects, indicators } = searchStats;
    const hasAny = equipments > 0 || projects > 0 || indicators > 0;
    return (
      <div
        style={{
          padding: '10px 16px',
          borderBottom: '1px solid #f0f0f0',
          background: 'linear-gradient(90deg, #f8faff 0%, #faf5ff 100%)',
        }}
      >
        <Space wrap size={[8, 8]} align="center">
          <span style={{ color: '#595959', fontWeight: 500 }}>检索统计</span>
          {equipments > 0 && (
            <Tag color="green" style={{ margin: 0 }}>
              相关装备 {equipments}
            </Tag>
          )}
          {projects > 0 && (
            <Tag color="orange" style={{ margin: 0 }}>
              相关项目 {projects}
            </Tag>
          )}
          {indicators > 0 && (
            <Tag color="cyan" style={{ margin: 0 }}>
              相关指标 {indicators}
            </Tag>
          )}
          {!hasAny && (
            <Tag color="default" style={{ margin: 0 }}>
              未匹配到装备 / 项目 / 指标
            </Tag>
          )}
          {graphNodeCount > 0 && (
            <Tag color="purple" style={{ margin: 0 }}>
              关联图谱节点 {graphNodeCount}
            </Tag>
          )}
        </Space>
      </div>
    );
  };

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        width: "100%",
        minHeight: "calc(100vh - 88px)",
        boxSizing: "border-box",
        background: "linear-gradient(135deg, #f0f7ff 0%, #e6f0ff 100%)",
        padding: "16px",
        gap: 12,
      }}
    >
      <Card style={{ flexShrink: 0 }}>
        <Row gutter={16} align="middle">
          <Col flex="auto">
            <Input.Search
              placeholder="输入设备名称、项目名称、指标名称进行搜索"
              value={searchKeyword}
              onChange={(e) => setSearchKeyword(e.target.value)}
              onSearch={handleSearch}
              size="large"
              enterButton={<Button type="primary" icon={<SearchOutlined />}>检索</Button>}
            />
          </Col>
          <Col>
            <Button onClick={handleReset} icon={<ReloadOutlined />} loading={searchLoading}>
              重置
            </Button>
          </Col>
        </Row>
      </Card>

      <Card
        size="small"
        title={
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <ExperimentOutlined style={{ color: "#8b5cf6" }} />
            <span>搜索结果</span>
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
        style={{
          flex: "1 1 auto",
          minHeight: 200,
          display: "flex",
          flexDirection: "column",
          overflow: "hidden",
        }}
        styles={{
          body: {
            flex: 1,
            minHeight: 0,
            overflow: "hidden",
            padding: 0,
            display: "flex",
            flexDirection: "column",
          },
        }}
      >
        {renderSearchStats()}
        {searchLoading ? (
          <div style={{ display: "flex", justifyContent: "center", padding: 60 }}>
            <Spin description="正在搜索..." size="large" />
          </div>
        ) : tableRows.length === 0 ? (
          <div style={{ display: "flex", justifyContent: "center", padding: 60 }}>
            <Empty
              description={
                hasSearched
                  ? graphNodeCount > 0
                    ? "暂无表格化环境数据，可在下方关联图谱中查看节点"
                    : "未找到匹配数据，请尝试其他关键词"
                  : "输入关键词后显示搜索结果"
              }
              image={Empty.PRESENTED_IMAGE_SIMPLE}
            />
          </div>
        ) : (
          <div style={{ flex: 1, minHeight: 0, overflow: "hidden" }}>
            <EnvironmentTable
              mode="ssyq"
              dataSource={tableRows}
              selectedProjects={selectedProjects}
              selectedIndicators={selectedIndicators}
              onProjectSelect={handleProjectSelect}
              onIndicatorSelect={handleIndicatorSelect}
              singleSelect={true}
              loading={false}
              emptyText="当前设备暂无数据"
              scrollY="calc(100vh - 300px)"
              highlightEquipmentRowIds={searchHighlights?.equipmentIds}
              highlightProjectIds={searchHighlights?.projectIds}
              highlightIndicatorIds={searchHighlights?.indicatorIds}
            />
          </div>
        )}
      </Card>

      {hasSearched && (
        <div
          style={{
            flex: "1 1 auto",
            minHeight: 360,
            display: "flex",
            gap: 12,
            overflow: "hidden",
          }}
        >
          <div
            style={{
              flex: 1,
              minWidth: 300,
              minHeight: 360,
              overflow: "hidden",
              display: "flex",
              flexDirection: "column",
            }}
          >
            <KnowledgeGraph
              key={graphKey}
              initialData={graphDataForComponent}
              nodeColors={nodeColors}
              edgeColors={edgeColors}
              nodeLabels={nodeLabels}
              edgeLabels={edgeLabels}
              height={480}
              showLegend={true}
              showControls={true}
              title="试验关联图谱"
              inlineExploreMode={true}
            />
          </div>

          {!methodCollapsed ? (
            <div
              style={{
                width: 360,
                minWidth: 360,
                display: "flex",
                flexDirection: "column",
                flexShrink: 0,
                alignSelf: "stretch",
                minHeight: 0,
                height: "100%",
              }}
            >
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
                  <Space size={4}>
                    <Button
                      type="text"
                      size="small"
                      icon={<LeftOutlined />}
                      onClick={() => setMethodCollapsed(true)}
                      title="收起方法栏"
                      aria-label="收起方法栏"
                    />
                    {selectedMethodIds.length > 0 ? (
                      <Tag color="blue" style={{ margin: 0 }}>已选 {selectedMethodIds.length}</Tag>
                    ) : null}
                  </Space>
                }
                style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column", overflow: "hidden" }}
                styles={{
                  body: {
                    padding: "12px",
                    flex: 1,
                    minHeight: 0,
                    overflowY: "auto",
                  },
                }}
              >
                {selectedProjects.length === 0 && selectedIndicators.length === 0 ? (
                  <Empty
                    description={<div style={{ fontSize: 13, color: "#999" }}>选择项目和/或指标后自动显示关联方法</div>}
                    image={Empty.PRESENTED_IMAGE_SIMPLE}
                  />
                ) : (
                  <MethodList
                    methods={filteredMethods}
                    selectedMethodIds={selectedMethodIds}
                    onMethodSelect={(methodId, checked) => {
                      if (checked) {
                        setSelectedMethodIds((prev) => [...prev, methodId]);
                      } else {
                        setSelectedMethodIds((prev) => prev.filter((id) => id !== methodId));
                      }
                    }}
                    singleSelect={true}
                    loading={methodsLoading}
                    emptyText="未找到匹配的试验方法"
                  />
                )}
              </Card>
            </div>
          ) : (
            <div
              style={{
                width: 40,
                minWidth: 40,
                flexShrink: 0,
                alignSelf: "stretch",
                minHeight: 0,
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                paddingTop: 10,
                background: "#fff",
                border: "1px solid #f0f0f0",
                borderRadius: 8,
                boxSizing: "border-box",
              }}
            >
              <Button
                type="text"
                size="small"
                icon={<RightOutlined />}
                onClick={() => setMethodCollapsed(false)}
                title="展开方法栏"
                aria-label="展开方法栏"
              />
            </div>
          )}
        </div>
      )}
    </div>
  );
};

const SSYQPageWithApp: React.FC = () => (
  <App>
    <SSYQPage />
  </App>
);

export default SSYQPageWithApp;
