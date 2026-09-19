'use client';

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Card, Spin, Alert, Empty } from 'antd';
import KnowledgeGraph, { type GraphData } from '@/components/KnowledgeGraph/KnowledgeGraph';
import EnvironmentTable, {
  type EnvironmentTableRow,
  type SelectedProjectEntry,
  type SelectedIndicatorEntry,
} from '@/components/EnvironmentTable';
import {
  enrichGraphWithIndicatorHas,
  type SearchGraphData,
} from '@/utils/enrichGraphWithIndicatorHas';

const nodeColors: Record<string, string> = {
  EquipmentType: '#1890ff',
  Equipment: '#52c41a',
  Environment: '#722ed1',
  TestProject: '#fa8c16',
  Indicator: '#13c2c2',
  File: '#eb2f96',
  TestMethod: '#faad14',
};

const edgeColors: Record<string, string> = {
  HAS: '#52c41a',
  CON: '#13c2c2',
  关联: '#fa8c16',
};

const nodeLabels: Record<string, string> = {
  EquipmentType: '设备类型',
  Equipment: '具体设备',
  Environment: '环境',
  TestProject: '测试项目',
  Indicator: '指标',
  File: '文件',
  TestMethod: '试验方法',
};

const edgeLabels: Record<string, string> = {
  HAS: '包含',
  CON: '关联测试',
  关联: '关联',
};

interface PreviewPayload {
  graphData: SearchGraphData;
  environmentRow: EnvironmentTableRow | null;
}

interface EquipmentGraphPreviewProps {
  equipmentId: string;
  equipmentName?: string;
}

async function fetchEquipmentPreviewGraph(equipmentId: string): Promise<PreviewPayload> {
  const res = await fetch(
    `/api/wjgl1/graph-picker/equipment/${encodeURIComponent(equipmentId)}/preview-graph`,
  );
  const json = await res.json();
  if (!json.success) {
    throw new Error(json.error || '加载图谱失败');
  }
  const data = json.data || {};
  return {
    graphData: data.graphData || { nodes: [], edges: [] },
    environmentRow: data.environmentRow || null,
  };
}

export default function EquipmentGraphPreview({
  equipmentId,
  equipmentName,
}: EquipmentGraphPreviewProps) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [payload, setPayload] = useState<PreviewPayload | null>(null);

  const noopProject = useCallback((_entry: SelectedProjectEntry, _checked: boolean) => {}, []);
  const noopIndicator = useCallback((_entry: SelectedIndicatorEntry, _checked: boolean) => {}, []);

  useEffect(() => {
    if (!equipmentId) {
      setPayload(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    void fetchEquipmentPreviewGraph(equipmentId)
      .then((data) => {
        if (!cancelled) setPayload(data);
      })
      .catch((e: unknown) => {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : '加载图谱失败');
          setPayload(null);
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [equipmentId]);

  const envRows: EnvironmentTableRow[] = useMemo(() => {
    if (!payload?.environmentRow) return [];
    return [payload.environmentRow];
  }, [payload]);

  const graphDataForComponent: GraphData | undefined = useMemo(() => {
    if (!payload?.graphData) return undefined;
    const enriched = enrichGraphWithIndicatorHas(payload.graphData, envRows);
    if (!enriched) return undefined;
    return { nodes: enriched.nodes, edges: enriched.edges };
  }, [payload, envRows]);

  if (!equipmentId) return null;

  return (
    <Card
      size="small"
      title={`关联图谱预览${equipmentName ? ` · ${equipmentName}` : ''}`}
      style={{ marginTop: 12 }}
    >
      {loading && (
        <div style={{ textAlign: 'center', padding: 24 }}>
          <Spin tip="加载设备关联图谱…" />
        </div>
      )}
      {!loading && error && <Alert type="error" message={error} showIcon />}
      {!loading && !error && !graphDataForComponent?.nodes?.length && (
        <Empty description="暂无关联项目/指标" image={Empty.PRESENTED_IMAGE_SIMPLE} />
      )}
      {!loading && !error && graphDataForComponent && graphDataForComponent.nodes.length > 0 && (
        <>
          {envRows.length > 0 && (
            <EnvironmentTable
              mode="ssyq"
              dataSource={envRows}
              selectedProjects={[]}
              selectedIndicators={[]}
              onProjectSelect={noopProject}
              onIndicatorSelect={noopIndicator}
              showSelection={false}
              singleSelect
              showSelectAll={false}
              scrollY={260}
              emptyText="暂无设备-环境数据"
            />
          )}
          <div style={{ marginTop: 12 }}>
            <KnowledgeGraph
              initialData={graphDataForComponent}
              nodeColors={nodeColors}
              edgeColors={edgeColors}
              nodeLabels={nodeLabels}
              edgeLabels={edgeLabels}
              height={420}
              showLegend
              showControls={false}
              showTable={false}
              title="设备关联图谱"
            />
          </div>
        </>
      )}
    </Card>
  );
}
