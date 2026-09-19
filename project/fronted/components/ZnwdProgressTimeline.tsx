'use client';

import React from 'react';
import { Steps, Typography } from 'antd';
import { CheckCircleOutlined, LoadingOutlined } from '@ant-design/icons';
import { humanizeStepLabel, humanizeStepMessage } from '@/app/GLCX/ZNWD/znwdUserCopy';

const { Text } = Typography;

export interface ZnwdTimelineStep {
  type: string;
  status?: string;
  message?: string;
  phase?: string;
  templateId?: string;
  candidateCount?: number;
  bindingCount?: number;
  zvecUsed?: boolean;
  subSteps?: { id?: string; purpose?: string; status?: string }[];
}

const TEMPLATE_LABELS: Record<string, string> = {
  equip_to_projects: '装备 → 试验项目',
  equip_to_indicators: '装备 → 考核指标',
  env_to_projects: '环境 → 试验项目',
  project_to_methods: '项目 → 试验方法',
  indicator_to_methods: '指标 → 试验方法',
  document_lookup: '文档检索',
  standard_chapter: '标准章节',
  composite_project_then_method: '装备 → 项目 → 方法',
  composite_project_and_indicator: '装备 → 项目与指标',
  equip_and_env_to_projects: '装备+环境 → 项目',
};

const PHASE_ORDER = ['routing', 'retrieval', 'guard', 'generating'] as const;

const PHASE_TITLES: Record<string, string> = {
  routing: '理解问题',
  retrieval: '检索',
  guard: '核对',
  generating: '回答',
};

function phaseIndex(type: string): number {
  const i = PHASE_ORDER.indexOf(type as (typeof PHASE_ORDER)[number]);
  return i >= 0 ? i : -1;
}

export function resolveCurrentPhase(steps: ZnwdTimelineStep[], streamPhase?: string): number {
  if (streamPhase === 'answer') return PHASE_ORDER.length;
  if ((steps || []).some((s) => s.type === 'generating' && s.status === 'done')) {
    return PHASE_ORDER.length;
  }
  let max = 0;
  for (const s of steps || []) {
    const idx = phaseIndex(s.type);
    if (idx < 0) continue;
    if (s.status === 'loading') return idx;
    if (s.status === 'done' && idx >= max) max = Math.min(idx + 1, PHASE_ORDER.length - 1);
  }
  return max;
}

export interface ZnwdProgressTimelineProps {
  steps?: ZnwdTimelineStep[];
  statusLine?: string;
  phase?: 'steps' | 'thinking' | 'answer';
  sourcePreviewLine?: string;
}

export default function ZnwdProgressTimeline({
  steps = [],
  statusLine,
  phase,
  sourcePreviewLine,
}: ZnwdProgressTimelineProps) {
  const current = resolveCurrentPhase(steps, phase);
  const allPhasesDone = current >= PHASE_ORDER.length;
  const activeStep = steps.find((s) => s.status === 'loading');
  const statusText = humanizeStepMessage(
    statusLine || activeStep?.message,
    activeStep?.type
  );

  const items = PHASE_ORDER.map((key, idx) => {
    let stepStatus: 'wait' | 'process' | 'finish' = 'wait';
    if (allPhasesDone || idx < current) stepStatus = 'finish';
    else if (idx === current) stepStatus = 'process';

    const doneStep = steps.find((s) => s.type === key && s.status === 'done');
    const loadingStep = steps.find((s) => s.type === key && s.status === 'loading');
    let description = '';
    if (loadingStep?.message) {
      description = humanizeStepMessage(loadingStep.message, key);
    } else if (doneStep?.message) {
      description = humanizeStepMessage(doneStep.message, key);
    } else if (idx === current && statusText) {
      description = statusText;
    }

    return {
      title: PHASE_TITLES[key] || humanizeStepLabel(key),
      status: stepStatus,
      icon:
        stepStatus === 'finish' ? (
          <CheckCircleOutlined style={{ color: '#52c41a' }} />
        ) : stepStatus === 'process' ? (
          <LoadingOutlined />
        ) : undefined,
      description: description ? (
        <Text type="secondary" style={{ fontSize: 12 }}>
          {description}
        </Text>
      ) : null,
    };
  });

  const retrievalStep = steps.find((s) => s.type === 'retrieval');
  const routingStep = steps.find((s) => s.type === 'routing');
  const znwd6Routing = steps.find(
    (s) => s.type === 'routing' && (s.phase === 'znwd6_plan' || s.phase === 'znwd6_probe' || s.templateId)
  );
  const subSteps = retrievalStep?.subSteps?.length
    ? retrievalStep.subSteps
    : routingStep?.subSteps;

  const templateHint = znwd6Routing?.templateId
    ? TEMPLATE_LABELS[znwd6Routing.templateId] || znwd6Routing.templateId
    : '';
  const probeHint =
    znwd6Routing?.candidateCount != null
      ? `候选 ${znwd6Routing.candidateCount} · 绑定 ${znwd6Routing.bindingCount ?? 0}${
          znwd6Routing.zvecUsed ? ' · 向量索引' : ''
        }`
      : '';

  return (
    <div style={{ marginBottom: 12 }}>
      <Steps
        size="small"
        current={allPhasesDone ? PHASE_ORDER.length - 1 : current}
        items={items}
      />
      {templateHint ? (
        <Text type="secondary" style={{ display: 'block', marginTop: 8, fontSize: 12 }}>
          路由模板：{templateHint}
          {probeHint ? `（${probeHint}）` : ''}
        </Text>
      ) : null}
      {subSteps && subSteps.length > 0 ? (
        <div style={{ marginTop: 8, paddingLeft: 4 }}>
          {subSteps.map((sub, i) => (
            <Text key={sub.id || i} type="secondary" style={{ display: 'block', fontSize: 12 }}>
              {sub.status === 'done' ? '✓' : '…'}{' '}
              {humanizeStepMessage(sub.purpose, routingStep?.subSteps?.length ? 'routing' : 'retrieval')}
            </Text>
          ))}
        </div>
      ) : null}
      {sourcePreviewLine ? (
        <Text type="secondary" style={{ display: 'block', marginTop: 8, fontSize: 12 }}>
          {sourcePreviewLine}
        </Text>
      ) : null}
    </div>
  );
}
