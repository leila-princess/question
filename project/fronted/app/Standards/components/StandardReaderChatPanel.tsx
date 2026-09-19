'use client';

import { memo, useCallback, useEffect, useRef, useState } from 'react';
import { Card, Spin, Tag, Input, Alert } from 'antd';
import type { InputRef } from 'antd';
import { BookOutlined } from '@ant-design/icons';
import MarkdownRenderer from '@/components/MarkdownRenderer';
import type { StandardInfoState } from './StandardReaderPreview';

interface ChatMessage {
  id: string;
  role: string;
  content: string;
  loading?: boolean;
}

interface StandardReaderChatPanelProps {
  displayName: string;
  summary: string;
  wjglMode: boolean;
  standardInfo: StandardInfoState;
  chatSourceId: string;
  previewHostRef: React.RefObject<HTMLDivElement | null>;
}

function StandardReaderChatPanel({
  displayName,
  summary,
  wjglMode,
  standardInfo,
  chatSourceId,
  previewHostRef,
}: StandardReaderChatPanelProps) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [sending, setSending] = useState(false);
  const [inputValue, setInputValue] = useState('');
  const chatRef = useRef<HTMLDivElement>(null);
  const chatInputRef = useRef<InputRef>(null);

  const releasePreviewFocus = useCallback(() => {
    const active = document.activeElement;
    if (active instanceof HTMLIFrameElement) {
      active.blur();
    } else if (active instanceof HTMLElement && previewHostRef.current?.contains(active)) {
      active.blur();
    }
  }, [previewHostRef]);

  const focusChatInput = useCallback(() => {
    releasePreviewFocus();
    window.setTimeout(() => chatInputRef.current?.focus(), 0);
  }, [releasePreviewFocus]);

  const sendMessage = useCallback(async (content: string) => {
    if (!content.trim() || sending) return;
    if (!chatSourceId) return;

    setMessages((prev) => [...prev, { id: Date.now().toString(), role: 'user', content }]);
    setSending(true);
    const aiId = (Date.now() + 1).toString();
    setMessages((prev) => [...prev, { id: aiId, role: 'ai', content: '', loading: true }]);
    try {
      const res = await fetch('/api/standard/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          source_id: chatSourceId,
          question: content,
        }),
      });
      const data = await res.json();
      setMessages((prev) => prev.map((m) =>
        m.id === aiId
          ? { ...m, content: data.success ? data.data.answer : (data.error || '回答失败'), loading: false }
          : m,
      ));
    } catch {
      setMessages((prev) => prev.map((m) =>
        m.id === aiId ? { ...m, content: '请求后端失败', loading: false } : m,
      ));
    }
    setSending(false);
    setInputValue('');
  }, [sending, chatSourceId]);

  useEffect(() => {
    chatRef.current?.scrollTo({ top: chatRef.current.scrollHeight, behavior: 'smooth' });
  }, [messages]);

  return (
    <>
      <Card style={{ borderRadius: 12, flexShrink: 0 }}>
        <div style={{ marginBottom: 8 }}>
          <BookOutlined style={{ marginRight: 8, color: '#3b82f6' }} />
          <strong>{displayName}</strong>
        </div>
        {wjglMode && standardInfo.object_name && (
          <Tag color="geekblue" style={{ marginBottom: 8, maxWidth: '100%', whiteSpace: 'normal' }}>
            {standardInfo.object_name}
          </Tag>
        )}
        {standardInfo.title && !wjglMode && (
          <Tag color="blue" style={{ marginBottom: 8 }}>{standardInfo.title}</Tag>
        )}
        <div style={{ maxHeight: 120, overflow: 'auto', fontSize: 13 }}>
          {summary === '加载中...' ? (
            <Spin size="small" tip="加载摘要中..." />
          ) : (
            <MarkdownRenderer content={summary} />
          )}
        </div>
      </Card>

      <Card
        style={{
          flex: 1,
          minHeight: 0,
          borderRadius: 12,
          display: 'flex',
          flexDirection: 'column',
          overflow: 'hidden',
        }}
        styles={{
          body: {
            flex: 1,
            minHeight: 0,
            display: 'flex',
            flexDirection: 'column',
            padding: 0,
            overflow: 'hidden',
          },
        }}
        onMouseDown={focusChatInput}
      >
        {!chatSourceId && (
          <Alert type="warning" showIcon message="未绑定 File 节点时无法使用右侧问答（仅预览/下载）。" style={{ margin: 12 }} />
        )}
        <div ref={chatRef} style={{ flex: 1, overflowY: 'auto', padding: 16 }}>
          {messages.length === 0 ? (
            <div style={{ textAlign: 'center', color: '#999', padding: 40, fontSize: 14 }}>
              根据文件问答
            </div>
          ) : (
            messages.map((msg) => (
              <div
                key={msg.id}
                style={{
                  display: 'flex',
                  justifyContent: msg.role === 'user' ? 'flex-end' : 'flex-start',
                  marginBottom: 12,
                }}
              >
                <div
                  style={{
                    maxWidth: '80%',
                    background: msg.role === 'user' ? '#eaf3ff' : '#fff',
                    borderRadius: 12,
                    padding: '10px 16px',
                    boxShadow: '0 1px 4px 0 rgba(60,72,88,0.04)',
                  }}
                >
                  {msg.loading ? (
                    <Spin size="small" tip="AI思考中..." />
                  ) : (
                    <MarkdownRenderer content={msg.content} />
                  )}
                </div>
              </div>
            ))
          )}
        </div>

        <div style={{ padding: 12, borderTop: '1px solid #f0f0f0' }} onMouseDown={(e) => e.stopPropagation()}>
          <Input.Search
            ref={chatInputRef}
            placeholder="请输入你的问题..."
            value={inputValue}
            onChange={(e) => setInputValue(e.target.value)}
            onFocus={releasePreviewFocus}
            onSearch={(value) => { if (value.trim()) void sendMessage(value); }}
            loading={sending}
            enterButton="发送"
            disabled={!chatSourceId}
          />
        </div>
      </Card>
    </>
  );
}

export default memo(StandardReaderChatPanel);
