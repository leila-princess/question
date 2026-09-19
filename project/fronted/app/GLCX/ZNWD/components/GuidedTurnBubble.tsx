'use client';

import React from 'react';
import { Typography } from 'antd';
import {
  BulbOutlined,
  CheckCircleFilled,
  CompassOutlined,
} from '@ant-design/icons';

const { Text } = Typography;

interface GuidedTurnBubbleProps {
  promptText: string;
  answerSummary?: string;
  children?: React.ReactNode;
  readonly?: boolean;
  /** 当前步骤序号（从 1 开始），用于顶部进度点 */
  stepIndex?: number;
  totalSteps?: number;
}

export default function GuidedTurnBubble({
  promptText,
  answerSummary,
  children,
  readonly,
  stepIndex,
  totalSteps,
}: GuidedTurnBubbleProps) {
  const showProgress =
    typeof stepIndex === 'number' && typeof totalSteps === 'number' && totalSteps > 1;

  return (
    <div
      style={{
        display: 'flex',
        gap: 12,
        marginBottom: 12,
        alignItems: 'flex-start',
      }}
    >
      <div
        style={{
          width: 36,
          height: 36,
          borderRadius: 10,
          flexShrink: 0,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          background: 'var(--znwd-bubble-bg, rgba(22, 119, 255, 0.08))',
          border: '1px solid rgba(22, 119, 255, 0.12)',
          boxShadow: '0 1px 2px rgba(0, 0, 0, 0.04)',
        }}
      >
        {readonly ? (
          <CheckCircleFilled style={{ fontSize: 16, color: '#1677ff', opacity: 0.85 }} />
        ) : (
          <CompassOutlined style={{ fontSize: 17, color: '#1677ff' }} />
        )}
      </div>

      <div style={{ flex: 1, minWidth: 0 }}>
        {showProgress ? (
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 6,
              marginBottom: 8,
            }}
          >
            {Array.from({ length: totalSteps }).map((_, i) => (
              <span
                key={i}
                style={{
                  width: i + 1 === stepIndex ? 18 : 6,
                  height: 6,
                  borderRadius: 999,
                  transition: 'width 0.2s ease',
                  background:
                    i + 1 < stepIndex
                      ? '#1677ff'
                      : i + 1 === stepIndex
                        ? '#1677ff'
                        : 'rgba(22, 119, 255, 0.18)',
                  opacity: i + 1 <= stepIndex ? 1 : 0.55,
                }}
              />
            ))}
            <Text type="secondary" style={{ fontSize: 11, marginLeft: 4 }}>
              {stepIndex}/{totalSteps}
            </Text>
          </div>
        ) : null}

        <div
          style={{
            position: 'relative',
            padding: readonly ? '8px 0' : '12px 14px',
            borderRadius: 12,
            background: readonly ? 'transparent' : 'var(--znwd-bubble-bg, rgba(0, 0, 0, 0.02))',
            borderLeft: readonly ? 'none' : '3px solid rgba(22, 119, 255, 0.35)',
            color: 'var(--znwd-text-color, inherit)',
          }}
        >
          <div style={{ display: 'flex', gap: 8, alignItems: 'flex-start' }}>
            {!readonly ? (
              <BulbOutlined
                style={{
                  fontSize: 14,
                  color: '#1677ff',
                  marginTop: 3,
                  opacity: 0.75,
                  flexShrink: 0,
                }}
              />
            ) : null}
            <Text
              style={{
                fontSize: 14,
                lineHeight: 1.65,
                fontWeight: readonly ? 400 : 500,
              }}
            >
              {promptText}
            </Text>
          </div>
          {!readonly && children ? (
            <div style={{ marginTop: 12, paddingLeft: 22 }}>{children}</div>
          ) : null}
        </div>

        {answerSummary ? (
          <div
            style={{
              display: 'flex',
              justifyContent: 'flex-end',
              marginTop: 8,
            }}
          >
            <span
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: 6,
                fontSize: 13,
                padding: '4px 10px',
                borderRadius: 999,
                background: 'var(--znwd-bubble-bg, rgba(22, 119, 255, 0.06))',
                color: 'var(--znwd-text-color, inherit)',
                opacity: 0.88,
                maxWidth: '100%',
              }}
            >
              <CheckCircleFilled style={{ fontSize: 12, color: '#1677ff', flexShrink: 0 }} />
              <span
                style={{
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                }}
              >
                {answerSummary}
              </span>
            </span>
          </div>
        ) : null}
      </div>
    </div>
  );
}
