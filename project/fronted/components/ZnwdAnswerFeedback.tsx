'use client';

import React, { useMemo, useState } from 'react';
import { Button, Input, Radio, Select, Space, Typography } from 'antd';

const { Text } = Typography;
const { TextArea } = Input;

export interface CorrectionRouteOption {
  id: string;
  routeId: string;
  label: string;
  confidence?: number;
}

export interface CorrectionSlotChip {
  slotKey: string;
  label: string;
  values: string[];
}

export interface CorrectionHints {
  enabled?: boolean;
  feedbackKinds?: { id: string; label: string }[];
  predictedRouteId?: string;
  predictedRouteLabel?: string;
  predictedSlots?: Record<string, string[] | string>;
  slotChips?: CorrectionSlotChip[];
  routeOptions?: CorrectionRouteOption[];
  resolved?: boolean;
  turnId?: string;
  routingEventId?: string;
}

export interface AnswerFeedbackSubmitPayload {
  turnId: string;
  routingEventId?: string;
  originalQuestion: string;
  rating: 'positive' | 'negative';
  feedbackKind?: string;
  predictedRouteId?: string;
  predictedSlots?: Record<string, string[] | string>;
  correctedRouteId?: string;
  correctedSlots?: Record<string, string[]>;
  comment?: string;
  answerPreview?: string;
  rerun?: boolean;
}

interface ZnwdAnswerFeedbackProps {
  hints: CorrectionHints;
  originalQuestion: string;
  answerPreview?: string;
  disabled?: boolean;
  submitted?: boolean;
  onSubmitFeedback: (payload: AnswerFeedbackSubmitPayload) => Promise<void> | void;
  onRerun: (payload: AnswerFeedbackSubmitPayload) => Promise<void> | void;
}

function slotsFromHints(hints: CorrectionHints): Record<string, string[]> {
  const out: Record<string, string[]> = {};
  const src = hints.predictedSlots || {};
  for (const [k, v] of Object.entries(src)) {
    if (k === 'equipment_type_parent') {
      const p = typeof v === 'string' ? v.trim() : Array.isArray(v) ? String(v[0] || '').trim() : '';
      if (p) out.equipment_type_parent = [p];
      continue;
    }
    if (Array.isArray(v)) {
      const vals = v.map((x) => String(x).trim()).filter(Boolean);
      if (vals.length) out[k] = vals;
    } else if (typeof v === 'string' && v.trim()) {
      out[k] = [v.trim()];
    }
  }
  if (Object.keys(out).length === 0 && Array.isArray(hints.slotChips)) {
    for (const chip of hints.slotChips) {
      const vals = (chip.values || []).map((x) => String(x).trim()).filter(Boolean);
      if (vals.length) out[chip.slotKey] = vals;
    }
  }
  return out;
}

function hasAnySlotValues(slots: Record<string, string[]>): boolean {
  return Object.values(slots).some((vals) => Array.isArray(vals) && vals.some((v) => String(v).trim()));
}

export default function ZnwdAnswerFeedback({
  hints,
  originalQuestion,
  answerPreview = '',
  disabled = false,
  submitted = false,
  onSubmitFeedback,
  onRerun,
}: ZnwdAnswerFeedbackProps) {
  const [expanded, setExpanded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [feedbackKind, setFeedbackKind] = useState<string>('');
  const [correctedRouteId, setCorrectedRouteId] = useState<string | undefined>(
    hints.predictedRouteId || undefined
  );
  const [correctedSlots, setCorrectedSlots] = useState<Record<string, string[]>>(() =>
    slotsFromHints(hints)
  );
  const [comment, setComment] = useState('');

  const routeOptions = useMemo(
    () => (Array.isArray(hints.routeOptions) ? hints.routeOptions : []),
    [hints.routeOptions]
  );
  const feedbackKinds = useMemo(
    () => (Array.isArray(hints.feedbackKinds) ? hints.feedbackKinds : []),
    [hints.feedbackKinds]
  );
  const slotChips = useMemo(
    () => (Array.isArray(hints.slotChips) ? hints.slotChips : []),
    [hints.slotChips]
  );

  const slotsIncomplete =
    feedbackKind === 'wrong_slots' && slotChips.length > 0 && !hasAnySlotValues(correctedSlots);

  const updateSlotValues = (slotKey: string, values: string[]) => {
    const cleaned = values.map((v) => String(v).trim()).filter(Boolean);
    setCorrectedSlots((prev) => {
      const next = { ...prev };
      if (cleaned.length) next[slotKey] = cleaned;
      else delete next[slotKey];
      return next;
    });
  };

  if (!hints?.enabled || hints.resolved || submitted) {
    if (submitted || hints.resolved) {
      return (
        <div style={{ marginTop: 8 }}>
          <Text type="secondary" style={{ fontSize: 13 }}>
            感谢您的反馈
          </Text>
        </div>
      );
    }
    return null;
  }

  const basePayload = (): AnswerFeedbackSubmitPayload => {
    const slots: Record<string, string[] | string> = { ...correctedSlots };
    if (slots.equipment_type_parent && Array.isArray(slots.equipment_type_parent)) {
      const p = slots.equipment_type_parent[0];
      if (p) slots.equipment_type_parent = p;
      else delete slots.equipment_type_parent;
    }
    return {
      turnId: hints.turnId || '',
      routingEventId: hints.routingEventId,
      originalQuestion,
      rating: 'negative',
      feedbackKind,
      predictedRouteId: hints.predictedRouteId,
      predictedSlots: hints.predictedSlots,
      correctedRouteId,
      correctedSlots: Object.keys(slots).length ? (slots as Record<string, string[]>) : undefined,
      comment: comment.trim() || undefined,
      answerPreview,
    };
  };

  const handlePositive = async () => {
    if (disabled || busy) return;
    setBusy(true);
    try {
      await onSubmitFeedback({
        turnId: hints.turnId || '',
        routingEventId: hints.routingEventId,
        originalQuestion,
        rating: 'positive',
        predictedRouteId: hints.predictedRouteId,
        predictedSlots: hints.predictedSlots,
        answerPreview,
      });
    } finally {
      setBusy(false);
    }
  };

  const handleExpandNegative = () => {
    if (disabled || busy) return;
    setCorrectedSlots(slotsFromHints(hints));
    setExpanded(true);
  };

  const handleSubmitOnly = async () => {
    if (disabled || busy || !feedbackKind) return;
    setBusy(true);
    try {
      await onSubmitFeedback({ ...basePayload(), rerun: false });
      setExpanded(false);
    } finally {
      setBusy(false);
    }
  };

  const handleRerun = async () => {
    if (disabled || busy || !feedbackKind) return;
    if (!correctedRouteId) return;
    if (slotsIncomplete) return;
    setBusy(true);
    try {
      await onRerun({ ...basePayload(), rerun: true });
    } finally {
      setBusy(false);
    }
  };

  if (!expanded) {
    return (
      <div
        style={{
          marginTop: 12,
          padding: '10px 12px',
          borderRadius: 8,
          border: '1px solid #e8f5e9',
          background: '#f6ffed',
        }}
      >
        <Space wrap>
          <Text style={{ fontSize: 13 }}>这个回答对您有帮助吗？</Text>
          <Button size="small" type="primary" disabled={disabled || busy} onClick={() => void handlePositive()}>
            有帮助
          </Button>
          <Button size="small" disabled={disabled || busy} onClick={handleExpandNegative}>
            答案不对
          </Button>
        </Space>
      </div>
    );
  }

  return (
    <div
      style={{
        marginTop: 12,
        padding: '12px 14px',
        borderRadius: 8,
        border: '1px solid #e8f5e9',
        background: '#f6ffed',
      }}
    >
      <Text strong style={{ fontSize: 13, display: 'block', marginBottom: 8 }}>
        请告诉我们哪里不对
      </Text>

      {hints.predictedRouteLabel ? (
        <Text type="secondary" style={{ fontSize: 12, display: 'block', marginBottom: 8 }}>
          当前意图：{hints.predictedRouteLabel}
        </Text>
      ) : null}

      <Radio.Group
        value={feedbackKind}
        onChange={(e) => setFeedbackKind(e.target.value)}
        style={{ display: 'flex', flexDirection: 'column', gap: 6, marginBottom: 10 }}
        disabled={disabled || busy}
      >
        {feedbackKinds.map((k) => (
          <Radio key={k.id} value={k.id} style={{ fontSize: 13 }}>
            {k.label}
          </Radio>
        ))}
      </Radio.Group>

      {routeOptions.length > 0 ? (
        <div style={{ marginBottom: 10 }}>
          <Text style={{ fontSize: 12, display: 'block', marginBottom: 4 }}>正确意图（可选）</Text>
          <Select
            style={{ width: '100%' }}
            placeholder="选择正确的业务意图"
            value={correctedRouteId}
            onChange={setCorrectedRouteId}
            disabled={disabled || busy}
            options={routeOptions.map((o) => ({ value: o.routeId, label: o.label }))}
            allowClear
          />
        </div>
      ) : null}

      {slotChips.length > 0 ? (
        <div style={{ marginBottom: 10 }}>
          <Text style={{ fontSize: 12, display: 'block', marginBottom: 4 }}>
            关键词（可编辑）
          </Text>
          {slotChips.map((chip) => (
            <div key={chip.slotKey} style={{ marginBottom: 8 }}>
              <Text type="secondary" style={{ fontSize: 12, display: 'block', marginBottom: 4 }}>
                {chip.label}
              </Text>
              <Select
                mode="tags"
                style={{ width: '100%' }}
                placeholder={`输入${chip.label}`}
                value={correctedSlots[chip.slotKey] || []}
                onChange={(vals) => updateSlotValues(chip.slotKey, vals)}
                disabled={disabled || busy}
                tokenSeparators={[',', '，', ';', '；']}
              />
            </div>
          ))}
        </div>
      ) : null}

      <TextArea
        rows={2}
        placeholder="补充说明（可选）"
        value={comment}
        onChange={(e) => setComment(e.target.value)}
        disabled={disabled || busy}
        style={{ marginBottom: 10, fontSize: 13 }}
      />

      <Space wrap>
        <Button size="small" disabled={disabled || busy || !feedbackKind} onClick={() => void handleSubmitOnly()}>
          仅提交反馈
        </Button>
        <Button
          size="small"
          type="primary"
          disabled={disabled || busy || !feedbackKind || !correctedRouteId || slotsIncomplete}
          onClick={() => void handleRerun()}
        >
          按纠正重新回答
        </Button>
      </Space>
    </div>
  );
}
