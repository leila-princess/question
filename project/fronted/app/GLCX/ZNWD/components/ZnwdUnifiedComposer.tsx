'use client';

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Button, Segmented, Typography } from 'antd';
import { CompassOutlined, EditOutlined, ReloadOutlined, ThunderboltOutlined } from '@ant-design/icons';
import { Sender } from '@ant-design/x';
import { FALLBACK_GUIDED_SCHEMA, type GuidedFlow, type GuidedSchema } from '../constants/guidedSchema';
import { getGuidedCopy } from '../constants/guidedCopy';
import type { GuidedExecutePayload } from '../constants/guidedFlowEngine';
import type { ZnwdLocale } from '../hooks/useZnwdAppearance';
import ZnwdGuideConversation from './ZnwdGuideConversation';

const { Text } = Typography;

export type ComposerMode = 'guide' | 'free';

function parseGuidedFlow(raw: Record<string, unknown> | undefined): GuidedFlow | undefined {
  if (!raw || !Array.isArray(raw.nodeTypes)) return undefined;
  return {
    greetingZh: String(raw.greetingZh || ''),
    greetingEn: String(raw.greetingEn || ''),
    nodeTypes: (raw.nodeTypes as Record<string, unknown>[]).map((nt) => ({
      type: String(nt.type || ''),
      labelZh: String(nt.labelZh || nt.type || ''),
      labelEn: String(nt.labelEn || nt.type || ''),
      aspects: ((nt.aspects as Record<string, unknown>[]) || []).map((a) => ({
        id: String(a.id || ''),
        labelZh: String(a.labelZh || a.id || ''),
        labelEn: String(a.labelEn || a.id || ''),
        aspectKind: String(a.aspectKind || 'relation'),
        intentId: String(a.intentId || ''),
      })),
    })),
  };
}

export interface ZnwdUnifiedComposerProps {
  locale?: ZnwdLocale;
  disabled?: boolean;
  loading?: boolean;
  guideEnabled?: boolean;
  value: string;
  onChange: (v: string) => void;
  onFreeSend: (msg: string) => void;
  onGuidedExecute: (payload: GuidedExecutePayload) => void;
  onCancel?: () => void;
  maxWidth?: number;
}

export default function ZnwdUnifiedComposer({
  locale = 'zh',
  disabled,
  loading,
  guideEnabled = true,
  value,
  onChange,
  onFreeSend,
  onGuidedExecute,
  onCancel,
  maxWidth = 960,
}: ZnwdUnifiedComposerProps) {
  const copy = useMemo(() => getGuidedCopy(locale), [locale]);
  const [mode, setMode] = useState<ComposerMode>(guideEnabled ? 'guide' : 'free');
  const [schema, setSchema] = useState<GuidedSchema>(FALLBACK_GUIDED_SCHEMA);
  const [guideResetKey, setGuideResetKey] = useState(0);
  const [guidedReady, setGuidedReady] = useState(false);
  const guidedPayloadRef = useRef<GuidedExecutePayload | null>(null);
  const userEditedInputRef = useRef(false);

  useEffect(() => {
    if (!guideEnabled) setMode('free');
  }, [guideEnabled]);

  useEffect(() => {
    let cancelled = false;
    if (!guideEnabled) return;
    (async () => {
      try {
        const res = await fetch('/api/znwd6/guided/schema');
        const data = await res.json();
        if (!cancelled && data?.data) {
          const guidedFlow = parseGuidedFlow(data.data.guidedFlow);
          setSchema({
            nodeTypes: data.data.nodeTypes || FALLBACK_GUIDED_SCHEMA.nodeTypes,
            intents: data.data.intents?.length ? data.data.intents : FALLBACK_GUIDED_SCHEMA.intents,
            composites: data.data.composites?.length
              ? data.data.composites
              : FALLBACK_GUIDED_SCHEMA.composites,
            quickExamples: data.data.quickExamples || FALLBACK_GUIDED_SCHEMA.quickExamples,
            slotKeys: data.data.slotKeys || {},
            guidedFlow: guidedFlow || FALLBACK_GUIDED_SCHEMA.guidedFlow,
          });
        }
      } catch {
        /* fallback */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [guideEnabled]);

  const handleDraftChange = useCallback(
    (draft: {
      phase: string;
      previewText: string;
      payload: GuidedExecutePayload | null;
      ready: boolean;
    }) => {
      guidedPayloadRef.current = draft.payload;
      setGuidedReady(draft.ready);
      if (mode === 'guide' && draft.ready && draft.previewText && !userEditedInputRef.current) {
        onChange(draft.previewText);
      }
    },
    [mode, onChange],
  );

  const handleModeChange = (next: ComposerMode) => {
    setMode(next);
    userEditedInputRef.current = false;
    if (next === 'free') {
      guidedPayloadRef.current = null;
      setGuidedReady(false);
    } else {
      onChange('');
    }
  };

  const handleSubmit = (msg: string) => {
    const text = (msg || value || '').trim();
    if (!text || disabled || loading) return;

    if (mode === 'guide' && !guidedReady) {
      onFreeSend(text);
      onChange('');
      userEditedInputRef.current = false;
      return;
    }

    if (mode === 'guide' && guidedReady && guidedPayloadRef.current) {
      onGuidedExecute({
        ...guidedPayloadRef.current,
        question: text,
      });
      userEditedInputRef.current = false;
      guidedPayloadRef.current = null;
      setGuidedReady(false);
      onChange('');
      setGuideResetKey((k) => k + 1);
      return;
    }

    onFreeSend(text);
    onChange('');
    userEditedInputRef.current = false;
  };

  const handleQuickExample = (q: string) => {
    userEditedInputRef.current = false;
    setMode('free');
    onChange(q);
  };

  const handleInputChange = (v: string) => {
    if (mode === 'guide' && !guidedReady && v.trim()) {
      setMode('free');
      guidedPayloadRef.current = null;
      setGuidedReady(false);
    }
    userEditedInputRef.current = true;
    onChange(v);
  };

  const placeholder =
    mode === 'guide'
      ? guidedReady
        ? copy.previewEmbeddedHint
        : copy.placeholderGuide
      : copy.placeholderFree;

  const modeOptions = [
    {
      label: (
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, padding: '0 2px' }}>
          <CompassOutlined />
          {copy.modeGuide}
        </span>
      ),
      value: 'guide' as const,
    },
    {
      label: (
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, padding: '0 2px' }}>
          <EditOutlined />
          {copy.modeFree}
        </span>
      ),
      value: 'free' as const,
    },
  ];

  return (
    <div
      style={{
        maxWidth,
        margin: '0 auto',
        width: '100%',
        borderRadius: 14,
        border: '1px solid rgba(22, 119, 255, 0.14)',
        background: 'var(--znwd-bubble-bg, rgba(255,255,255, 0.6))',
        boxShadow: '0 2px 12px rgba(22, 119, 255, 0.06)',
        overflow: 'hidden',
      }}
    >
      {guideEnabled ? (
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            gap: 12,
            padding: '10px 14px 8px',
            borderBottom: '1px solid rgba(22, 119, 255, 0.08)',
          }}
        >
          <Segmented
            size="small"
            value={mode}
            onChange={(v) => handleModeChange(v as ComposerMode)}
            options={modeOptions}
            disabled={disabled || loading}
          />
          {mode === 'guide' ? (
            <Button
              type="text"
              size="small"
              icon={<ReloadOutlined />}
              disabled={disabled || loading}
              onClick={() => {
                userEditedInputRef.current = false;
                guidedPayloadRef.current = null;
                setGuidedReady(false);
                onChange('');
                setGuideResetKey((k) => k + 1);
              }}
            >
              {copy.restart}
            </Button>
          ) : null}
        </div>
      ) : null}

      {guideEnabled && mode === 'guide' ? (
        <div
          style={{
            padding: '12px 14px',
            borderBottom: '1px dashed rgba(22, 119, 255, 0.12)',
            background: 'var(--znwd-page-bg, transparent)',
          }}
        >
          <ZnwdGuideConversation
            embedded
            schema={schema}
            locale={locale}
            disabled={disabled || loading}
            onFillQuestion={() => {}}
            onGuidedExecute={onGuidedExecute}
            showQuickExamples={false}
            onDraftChange={handleDraftChange}
            resetKey={guideResetKey}
          />
        </div>
      ) : null}

      <div style={{ padding: guideEnabled ? '8px 10px 10px' : '10px' }}>
        <Sender
          value={value}
          onChange={handleInputChange}
          onSubmit={handleSubmit}
          loading={loading}
          onCancel={onCancel}
          placeholder={placeholder}
          autoSize={{ minRows: 1, maxRows: 4 }}
        />
      </div>

      {schema.quickExamples.length > 0 ? (
        <div
          style={{
            padding: '0 14px 12px',
            display: 'flex',
            flexWrap: 'wrap',
            alignItems: 'center',
            gap: 8,
          }}
        >
          <Text type="secondary" style={{ fontSize: 11, display: 'flex', alignItems: 'center', gap: 4 }}>
            <ThunderboltOutlined style={{ color: '#1677ff', opacity: 0.65, fontSize: 11 }} />
            {copy.quick}
          </Text>
          {schema.quickExamples.map((q) => (
            <Button
              key={q}
              size="small"
              shape="round"
              disabled={disabled || loading}
              onClick={() => handleQuickExample(q)}
              style={{
                fontSize: 12,
                borderColor: 'rgba(22, 119, 255, 0.18)',
                background: 'transparent',
              }}
            >
              {q}
            </Button>
          ))}
        </div>
      ) : null}
    </div>
  );
}
