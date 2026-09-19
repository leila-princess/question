'use client';

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Button, Input, Space, Typography, message } from 'antd';
import {
  ApartmentOutlined,
  BookOutlined,
  EditOutlined,
  FileTextOutlined,
  LeftOutlined,
  PartitionOutlined,
  ReloadOutlined,
  RightOutlined,
  RocketOutlined,
  SearchOutlined,
  SendOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';
import { getGuidedCopy } from '../constants/guidedCopy';
import {
  buildGuidedExecutePayload,
  createInitialFlowState,
  fillSlot,
  getCurrentTurn,
  selectAspect,
  selectNodeType,
  setTemplateIdx,
  type GuidedExecutePayload,
  type GuidedFlowState,
} from '../constants/guidedFlowEngine';
import {
  composeGuidedQuestion,
  slotValueKey,
  type GuidedSchema,
  type GuidedSlot,
} from '../constants/guidedSchema';
import type { ZnwdLocale } from '../hooks/useZnwdAppearance';
import EntitySearchSelect, { type EntityOption } from './EntitySearchSelect';
import GuidedTurnBubble from './GuidedTurnBubble';

const { Paragraph, Text } = Typography;

function GuidedOptionTags<T extends string | number>({
  options,
  value,
  onChange,
  disabled,
  compactLabel,
}: {
  options: { value: T; label: string }[];
  value?: T;
  onChange: (v: T) => void;
  disabled?: boolean;
  /** 问法模板等较长文案时使用略大的内边距 */
  compactLabel?: boolean;
}) {
  return (
    <div
      style={{
        display: 'flex',
        flexWrap: 'wrap',
        gap: 8,
        marginTop: 10,
      }}
    >
      {options.map((opt) => {
        const selected = value === opt.value;
        return (
          <button
            key={String(opt.value)}
            type="button"
            disabled={disabled}
            onClick={() => onChange(opt.value)}
            style={{
              cursor: disabled ? 'not-allowed' : 'pointer',
              opacity: disabled ? 0.55 : 1,
              padding: compactLabel ? '6px 12px' : '6px 14px',
              borderRadius: compactLabel ? 8 : 999,
              fontSize: compactLabel ? 12 : 13,
              lineHeight: 1.45,
              textAlign: 'left' as const,
              maxWidth: '100%',
              whiteSpace: compactLabel ? 'normal' as const : 'nowrap' as const,
              border: selected ? '1.5px solid #1677ff' : '1px solid rgba(22, 119, 255, 0.22)',
              background: selected
                ? 'rgba(22, 119, 255, 0.12)'
                : 'var(--znwd-page-bg, rgba(255,255,255,0.85))',
              color: selected ? '#1677ff' : 'var(--znwd-text-color, inherit)',
              fontWeight: selected ? 500 : 400,
              boxShadow: selected ? '0 0 0 2px rgba(22, 119, 255, 0.08)' : 'none',
              transition: 'border-color 0.15s, background 0.15s, box-shadow 0.15s',
            }}
          >
            {opt.label}
          </button>
        );
      })}
    </div>
  );
}

function phaseStepIndex(phase: string): number {
  switch (phase) {
    case 'node_type':
      return 1;
    case 'aspect':
      return 2;
    case 'slot':
      return 3;
    case 'preview':
      return 4;
    default:
      return 1;
  }
}

function PhaseIcon({ phase }: { phase: string }) {
  const style = { fontSize: 14, color: '#1677ff', opacity: 0.8 };
  switch (phase) {
    case 'node_type':
      return <ApartmentOutlined style={style} />;
    case 'aspect':
      return <PartitionOutlined style={style} />;
    case 'slot':
      return <SearchOutlined style={style} />;
    case 'preview':
      return <FileTextOutlined style={style} />;
    default:
      return <EditOutlined style={style} />;
  }
}

function GuidedSlotControl({
  slot,
  value,
  disabled,
  locale,
  onFill,
}: {
  slot: GuidedSlot;
  value: string;
  disabled?: boolean;
  locale: ZnwdLocale;
  onFill: (val: string, meta?: { node_type?: string; entity_id?: string }) => void;
}) {
  const [draft, setDraft] = useState(value);

  if (slot.free_text) {
    return (
      <Space.Compact style={{ width: '100%' }}>
        <Input
          value={draft}
          placeholder={slot.label}
          disabled={disabled}
          prefix={<EditOutlined style={{ color: 'rgba(22,119,255,0.45)' }} />}
          onChange={(e) => setDraft(e.target.value)}
          onPressEnter={() => {
            const v = draft.trim();
            if (v) onFill(v);
          }}
        />
        <Button
          type="primary"
          disabled={disabled || !draft.trim()}
          icon={<RocketOutlined />}
          onClick={() => {
            const v = draft.trim();
            if (v) onFill(v);
          }}
        >
          {locale === 'en' ? 'OK' : '确认'}
        </Button>
      </Space.Compact>
    );
  }

  return (
    <EntitySearchSelect
      nodeType={slot.node_type}
      freeText={false}
      value={value}
      placeholder={slot.label}
      disabled={disabled}
      onChange={(name, entity?: EntityOption) =>
        onFill(name, {
          node_type: entity?.nodeType || slot.node_type,
          entity_id: entity?.id,
        })
      }
    />
  );
}

interface ZnwdGuideConversationProps {
  schema: GuidedSchema;
  locale?: ZnwdLocale;
  disabled?: boolean;
  onFillQuestion: (question: string, send?: boolean) => void;
  onGuidedExecute?: (payload: GuidedExecutePayload) => void;
  quickExamples?: string[];
  showQuickExamples?: boolean;
  compact?: boolean;
  /** 嵌入统一输入区：精简布局，预览同步到外部输入框 */
  embedded?: boolean;
  onDraftChange?: (draft: {
    phase: string;
    previewText: string;
    payload: GuidedExecutePayload | null;
    ready: boolean;
  }) => void;
  resetKey?: number;
}

export default function ZnwdGuideConversation({
  schema,
  locale = 'zh',
  disabled,
  onFillQuestion,
  onGuidedExecute,
  quickExamples = [],
  showQuickExamples = true,
  compact = false,
  embedded = false,
  onDraftChange,
  resetKey = 0,
}: ZnwdGuideConversationProps) {
  const [flowState, setFlowState] = useState<GuidedFlowState>(createInitialFlowState);
  const [pageViewIndex, setPageViewIndex] = useState<number | null>(null);
  const copy = useMemo(() => getGuidedCopy(locale), [locale]);

  const turn = useMemo(
    () => getCurrentTurn(schema, flowState, locale),
    [schema, flowState, locale],
  );

  const completedCount = flowState.history.length;
  const totalPages = completedCount + 1;
  const activeViewIndex = pageViewIndex ?? completedCount;
  const isViewingPast = activeViewIndex < completedCount;
  const currentStep = phaseStepIndex(turn.phase);

  useEffect(() => {
    setPageViewIndex(null);
  }, [completedCount]);

  const resetFlow = useCallback(() => {
    setFlowState(createInitialFlowState());
    setPageViewIndex(null);
  }, []);

  useEffect(() => {
    if (resetKey > 0) resetFlow();
  }, [resetKey, resetFlow]);

  const goPrevPage = () => {
    setPageViewIndex((i) => {
      const cur = i ?? completedCount;
      return Math.max(0, cur - 1);
    });
  };

  const goNextPage = () => {
    setPageViewIndex((i) => {
      const cur = i ?? completedCount;
      if (cur >= completedCount) return null;
      return cur + 1 >= completedCount ? null : cur + 1;
    });
  };

  const handleNodeType = (nodeType: string) => {
    setFlowState((prev) => selectNodeType(schema, prev, nodeType, locale));
  };

  const handleAspect = (aspectId: string) => {
    setFlowState((prev) => selectAspect(schema, prev, aspectId, locale));
  };

  const handleSlotFill = (
    slotKey: string,
    value: string,
    meta?: { node_type?: string; entity_id?: string },
  ) => {
    if (!value.trim()) return;
    setFlowState((prev) => fillSlot(schema, prev, slotKey, value.trim(), locale, meta));
  };

  const previewText = useMemo(() => {
    if (turn.phase !== 'preview' || !turn.resolvedItem) return turn.preview || '';
    const templates = turn.templates || turn.resolvedItem.question_templates || [];
    const tpl = templates[flowState.templateIdx] || templates[0] || '';
    const slots = turn.resolvedItem.slots || [];
    const mapped: Record<string, string> = {};
    for (const s of slots) {
      const v = flowState.slotValues[s.slot_key] || '';
      mapped[slotValueKey(s.slot_key)] = v;
      mapped[s.slot_key] = v;
    }
    return composeGuidedQuestion(tpl, mapped);
  }, [turn, flowState.templateIdx, flowState.slotValues]);

  useEffect(() => {
    if (!onDraftChange) return;
    const payload =
      turn.phase === 'preview' && previewText
        ? buildGuidedExecutePayload(schema, flowState, previewText)
        : null;
    onDraftChange({
      phase: turn.phase,
      previewText: turn.phase === 'preview' ? previewText : '',
      payload,
      ready: !!payload,
    });
  }, [turn.phase, previewText, flowState, schema, onDraftChange]);

  const renderControl = () => {
    if (disabled || isViewingPast) return null;

    switch (turn.controlType) {
      case 'node_type':
        return (
          <GuidedOptionTags
            options={(turn.nodeTypeOptions || []).map((n) => ({
              value: n.type,
              label: locale === 'en' ? n.labelEn : n.labelZh,
            }))}
            value={flowState.nodeType}
            disabled={disabled}
            onChange={handleNodeType}
          />
        );
      case 'aspect':
        return (
          <GuidedOptionTags
            options={(turn.aspectOptions || []).map((a) => ({
              value: a.id,
              label: locale === 'en' ? a.labelEn : a.labelZh,
            }))}
            value={flowState.aspectId}
            disabled={disabled}
            onChange={handleAspect}
          />
        );
      case 'slot':
        if (!turn.slot) return null;
        return (
          <GuidedSlotControl
            slot={turn.slot}
            value={flowState.slotValues[turn.slot.slot_key] || ''}
            disabled={disabled}
            locale={locale}
            onFill={(name, meta) => handleSlotFill(turn.slot!.slot_key, name, meta)}
          />
        );
      case 'preview':
        if (embedded) {
          return (
            <Space direction="vertical" style={{ width: '100%' }} size="small">
              {(turn.templates || []).length > 1 ? (
                <div>
                  <Text type="secondary" style={{ fontSize: 12, display: 'flex', alignItems: 'center', gap: 6 }}>
                    <FileTextOutlined />
                    {copy.pickTemplate}
                  </Text>
                  <GuidedOptionTags
                    compactLabel
                    options={(turn.templates || []).map((t, i) => ({ value: i, label: t }))}
                    value={flowState.templateIdx}
                    disabled={disabled}
                    onChange={(idx) => setFlowState((prev) => setTemplateIdx(prev, idx))}
                  />
                </div>
              ) : null}
              <Text type="secondary" style={{ fontSize: 12 }}>
                {copy.previewEmbeddedHint}
              </Text>
              <Button type="link" size="small" icon={<ReloadOutlined />} onClick={resetFlow} style={{ padding: 0 }}>
                {copy.restart}
              </Button>
            </Space>
          );
        }
        return (
          <Space direction="vertical" style={{ width: '100%' }} size="middle">
            {(turn.templates || []).length > 1 ? (
              <div>
                <Text type="secondary" style={{ fontSize: 12, display: 'flex', alignItems: 'center', gap: 6 }}>
                  <FileTextOutlined />
                  {copy.pickTemplate}
                </Text>
                <GuidedOptionTags
                  compactLabel
                  options={(turn.templates || []).map((t, i) => ({ value: i, label: t }))}
                  value={flowState.templateIdx}
                  disabled={disabled}
                  onChange={(idx) => setFlowState((prev) => setTemplateIdx(prev, idx))}
                />
              </div>
            ) : null}
            <div
              style={{
                padding: '12px 14px',
                borderRadius: 10,
                background: 'var(--znwd-page-bg, #fff)',
                border: '1px dashed rgba(22, 119, 255, 0.25)',
                position: 'relative',
              }}
            >
              <FileTextOutlined
                style={{
                  position: 'absolute',
                  top: 10,
                  right: 12,
                  fontSize: 28,
                  color: '#1677ff',
                  opacity: 0.08,
                }}
              />
              <Paragraph
                copyable
                style={{
                  marginBottom: 0,
                  fontSize: 14,
                  lineHeight: 1.7,
                  color: 'var(--znwd-text-color, inherit)',
                }}
              >
                {previewText}
              </Paragraph>
            </div>
            <Space wrap>
              <Button
                type="default"
                disabled={!previewText}
                icon={<EditOutlined />}
                onClick={() => {
                  if (!previewText) return;
                  onFillQuestion(previewText, false);
                  message.success(locale === 'en' ? 'Filled input' : '已填入输入框');
                }}
              >
                {copy.fill}
              </Button>
              <Button
                type="primary"
                disabled={!previewText || disabled}
                icon={<SendOutlined />}
                onClick={() => {
                  if (!previewText) return;
                  const payload = buildGuidedExecutePayload(schema, flowState, previewText);
                  if (payload && onGuidedExecute) {
                    onGuidedExecute(payload);
                    resetFlow();
                  } else {
                    onFillQuestion(previewText, true);
                    resetFlow();
                  }
                }}
              >
                {copy.send}
              </Button>
              <Button type="link" icon={<ReloadOutlined />} onClick={resetFlow}>
                {copy.restart}
              </Button>
            </Space>
          </Space>
        );
      default:
        return null;
    }
  };

  const pastPage = isViewingPast ? flowState.history[activeViewIndex] : null;

  if (embedded) {
    const promptText = isViewingPast && pastPage ? pastPage.promptText : turn.promptText;
    return (
      <div style={{ width: '100%' }}>
        <div
          style={{
            display: 'flex',
            alignItems: 'flex-start',
            gap: 10,
            marginBottom: 10,
            position: 'relative',
            paddingRight: completedCount > 0 ? 88 : 0,
          }}
        >
          <div
            style={{
              width: 28,
              height: 28,
              borderRadius: 8,
              flexShrink: 0,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              background: 'var(--znwd-bubble-bg, rgba(22, 119, 255, 0.08))',
            }}
          >
            <PhaseIcon phase={turn.phase} />
          </div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6 }}>
              {Array.from({ length: 4 }).map((_, i) => (
                <span
                  key={i}
                  style={{
                    width: i + 1 === currentStep ? 14 : 5,
                    height: 5,
                    borderRadius: 999,
                    background:
                      i + 1 <= currentStep ? '#1677ff' : 'rgba(22, 119, 255, 0.15)',
                    transition: 'width 0.2s',
                  }}
                />
              ))}
            </div>
            <Text style={{ fontSize: 13, lineHeight: 1.55, fontWeight: 500 }}>{promptText}</Text>
          </div>
          {completedCount > 0 ? (
            <div
              style={{
                position: 'absolute',
                right: 0,
                top: 0,
                display: 'flex',
                alignItems: 'center',
                gap: 2,
              }}
            >
              <Button type="text" size="small" icon={<LeftOutlined />} disabled={activeViewIndex <= 0} onClick={goPrevPage} />
              <Text type="secondary" style={{ fontSize: 10 }}>
                {activeViewIndex + 1}/{totalPages}
              </Text>
              <Button
                type="text"
                size="small"
                icon={<RightOutlined />}
                disabled={activeViewIndex >= completedCount}
                onClick={goNextPage}
              />
            </div>
          ) : null}
        </div>
        {!isViewingPast ? renderControl() : null}
      </div>
    );
  }

  return (
    <div>
      <div
        style={{
          position: 'relative',
          minHeight: compact ? 72 : 88,
          paddingTop: completedCount > 0 ? 4 : 0,
        }}
      >
        {completedCount > 0 ? (
          <div
            style={{
              position: 'absolute',
              right: 0,
              top: 0,
              zIndex: 2,
              display: 'flex',
              alignItems: 'center',
              gap: 4,
              background: 'var(--znwd-bubble-bg, rgba(255,255,255,0.6))',
              padding: '2px 6px',
              borderRadius: 999,
              border: '1px solid rgba(22, 119, 255, 0.1)',
            }}
          >
            <Button
              type="text"
              size="small"
              icon={<LeftOutlined />}
              title={copy.pagePrev}
              disabled={activeViewIndex <= 0}
              onClick={goPrevPage}
            />
            <Text type="secondary" style={{ fontSize: 11, minWidth: 36, textAlign: 'center' }}>
              <BookOutlined style={{ marginRight: 4, fontSize: 10 }} />
              {activeViewIndex + 1}/{totalPages}
            </Text>
            <Button
              type="text"
              size="small"
              icon={<RightOutlined />}
              title={activeViewIndex >= completedCount ? copy.pageCurrent : copy.pageNext}
              disabled={activeViewIndex >= completedCount}
              onClick={goNextPage}
            />
          </div>
        ) : null}

        {isViewingPast && pastPage ? (
          <GuidedTurnBubble
            promptText={pastPage.promptText}
            answerSummary={pastPage.answerSummary}
            readonly
            stepIndex={activeViewIndex + 1}
            totalSteps={totalPages}
          />
        ) : (
          <GuidedTurnBubble
            promptText={turn.promptText}
            stepIndex={currentStep}
            totalSteps={4}
          >
            {renderControl()}
          </GuidedTurnBubble>
        )}
      </div>

      {showQuickExamples && quickExamples.length > 0 ? (
        <div style={{ marginTop: 12 }}>
          <Text
            type="secondary"
            style={{ fontSize: 12, display: 'flex', alignItems: 'center', gap: 6 }}
          >
            <ThunderboltOutlined style={{ color: '#1677ff', opacity: 0.7 }} />
            {copy.quick}
          </Text>
          <div style={{ marginTop: 8, display: 'flex', flexWrap: 'wrap', gap: 8 }}>
            {quickExamples.map((q) => (
              <Button
                key={q}
                size="small"
                shape="round"
                icon={<ThunderboltOutlined />}
                onClick={() => onFillQuestion(q, false)}
                disabled={disabled}
                style={{
                  borderColor: 'rgba(22, 119, 255, 0.2)',
                  background: 'var(--znwd-bubble-bg, transparent)',
                }}
              >
                {q}
              </Button>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}
