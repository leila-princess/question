'use client';

import React, { useEffect, useMemo, useState } from 'react';
import { Button, Typography } from 'antd';
import { ThunderboltOutlined } from '@ant-design/icons';
import { FALLBACK_GUIDED_SCHEMA, type GuidedFlow, type GuidedSchema } from '../constants/guidedSchema';
import { getGuidedCopy } from '../constants/guidedCopy';
import type { ZnwdLocale } from '../hooks/useZnwdAppearance';
import ZnwdGuideConversation from './ZnwdGuideConversation';

const { Text } = Typography;

interface ZnwdGuidePanelProps {
  variant?: 'card' | 'footer';
  collapsed?: boolean;
  onToggleCollapse?: () => void;
  onFillQuestion: (question: string, send?: boolean) => void;
  onGuidedExecute?: (payload: import('../constants/guidedFlowEngine').GuidedExecutePayload) => void;
  locale?: ZnwdLocale;
  disabled?: boolean;
}

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

export default function ZnwdGuidePanel({
  variant = 'card',
  collapsed = false,
  onToggleCollapse,
  onFillQuestion,
  onGuidedExecute,
  locale = 'zh',
  disabled,
}: ZnwdGuidePanelProps) {
  const [schema, setSchema] = useState<GuidedSchema>(FALLBACK_GUIDED_SCHEMA);
  const copy = useMemo(() => getGuidedCopy(locale), [locale]);

  useEffect(() => {
    let cancelled = false;
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
  }, []);

  if (variant === 'footer') {
    return (
      <div style={{ width: '100%' }}>
        <ZnwdGuideConversation
          schema={schema}
          locale={locale}
          disabled={disabled}
          onFillQuestion={onFillQuestion}
          onGuidedExecute={onGuidedExecute}
          showQuickExamples={false}
          compact
        />
        {schema.quickExamples.length > 0 ? (
          <div style={{ marginTop: 10 }}>
            <Text type="secondary" style={{ fontSize: 12, display: 'block', marginBottom: 8 }}>
              {copy.quick}
            </Text>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
              {schema.quickExamples.map((q) => (
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

  if (collapsed) {
    return (
      <div style={{ textAlign: 'center', padding: '8px 0' }}>
        <Button type="link" onClick={onToggleCollapse} disabled={disabled}>
          {copy.expand}
        </Button>
      </div>
    );
  }

  return (
    <div
      style={{
        marginBottom: 16,
        maxWidth: 960,
        marginLeft: 'auto',
        marginRight: 'auto',
        width: '100%',
        padding: '12px 16px',
        borderRadius: 8,
        border: '1px solid #f0f0f0',
        background: 'var(--znwd-footer-bg, #fafafa)',
      }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
        <Text strong style={{ fontSize: 14 }}>{copy.title}</Text>
        {onToggleCollapse ? (
          <Button type="link" size="small" onClick={onToggleCollapse}>
            {copy.collapse}
          </Button>
        ) : null}
      </div>
      <ZnwdGuideConversation
        schema={schema}
        locale={locale}
        disabled={disabled}
        onFillQuestion={onFillQuestion}
        onGuidedExecute={onGuidedExecute}
        quickExamples={schema.quickExamples}
      />
    </div>
  );
}
