'use client';

import { useMemo } from 'react';
import { Collapse, Tag, Spin, Button, Space, Card, Descriptions, Avatar } from 'antd';
import { RobotOutlined, UserOutlined, BulbOutlined, CheckCircleOutlined, EditOutlined, FileTextOutlined, CompressOutlined } from '@ant-design/icons';
import { Bubble } from '@ant-design/x';
import MarkdownRenderer from '@/components/MarkdownRenderer';
import type { ChatMessage } from '../types/flow';
import { useTypewriterReveal } from '../hooks/useTypewriterReveal';

interface ChatMessageProps {
  message: ChatMessage;
  onToggleThinking?: () => void;
  onSelectionChange?: (selections: string[]) => void;
  onConfirmSelection?: () => void;
  selectableItems?: { key: string; name: string; category?: string }[];
}

export default function ChatMessage({ 
  message, 
  onToggleThinking,
  onSelectionChange,
  onConfirmSelection,
  selectableItems,
}: ChatMessageProps) {
  const isUser = message.role === 'user';
  const isAssistant = message.role === 'assistant';

  const thinkingTextRaw = useMemo(() => {
    if (!isAssistant || !message.thinking?.length) return '';
    return message.thinking.map((e) => e.content.replace(/▌$/, '')).filter(Boolean).join('');
  }, [isAssistant, message.thinking]);

  const thinkingDisplay = useTypewriterReveal(
    thinkingTextRaw,
    Boolean(isAssistant && message.isStreaming && thinkingTextRaw.length > 0),
  );

  const formatTime = (ts: string) => {
    try {
      return new Date(ts).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
    } catch {
      return '';
    }
  };

  const getAvatar = () => {
    if (isUser) return <Avatar icon={<UserOutlined />} style={{ backgroundColor: '#52c41a' }} />;
    
    let agentType = message.metadata?.agent || 'system';
    if (message.thinking && message.thinking.length > 0) {
      agentType = message.thinking[message.thinking.length - 1].agent;
    } else if (message.contentType === 'chapter') {
      agentType = 'writer';
    } else if (message.contentType === 'planner_prompts') {
      agentType = 'planner';
    }

    switch (agentType) {
      case 'planner':
      case 'planner_outline':
        return <Avatar icon={<EditOutlined />} style={{ backgroundColor: '#722ed1' }} />;
      case 'context_compress':
        return <Avatar icon={<CompressOutlined />} style={{ backgroundColor: '#13c2c2' }} />;
      case 'writer':
        return <Avatar icon={<FileTextOutlined />} style={{ backgroundColor: '#1890ff' }} />;
      case 'reviewer':
        return <Avatar icon={<CheckCircleOutlined />} style={{ backgroundColor: '#fa8c16' }} />;
      default:
        return <Avatar icon={<RobotOutlined />} style={{ backgroundColor: '#1890ff' }} />;
    }
  };

  const renderContent = () => {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8, width: '100%' }}>
        {thinkingTextRaw && (
          <Collapse
            size="small"
            ghost
            defaultActiveKey={message.isStreaming ? ['thinking'] : []}
            style={{ background: '#fafafa', border: '1px solid #e5e7eb', borderRadius: 8 }}
            items={[{
              key: 'thinking',
              label: (
                <span style={{ fontSize: 12, color: '#8c8c8c' }}>
                  <BulbOutlined style={{ marginRight: 4 }} />
                  思考过程
                  {message.isStreaming && <Spin size="small" style={{ marginLeft: 8 }} />}
                </span>
              ),
              children: (
                <div style={{ fontSize: 13, color: '#595959', lineHeight: 1.8, padding: '0 8px', maxHeight: 400, overflow: 'auto' }}>
                  <MarkdownRenderer content={thinkingDisplay} />
                  {message.isStreaming && (
                    <span className="jyllm-caret-blink">▌</span>
                  )}
                </div>
              ),
            }]}
          />
        )}

        {message.content && message.content.trim() && (
          message.isStreaming ? (
            <span>
              {message.content}
              <span className="jyllm-caret-blink">▊</span>
            </span>
          ) : (
            <div style={isUser ? { color: '#fff' } : {}}>
              <MarkdownRenderer content={message.content} />
            </div>
          )
        )}

        {message.contentType === 'device_select' && selectableItems && selectableItems.length > 0 && (
          <div style={{ marginTop: 8 }}>
            <div style={{ maxHeight: 250, overflow: 'auto', marginBottom: 8 }}>
              {selectableItems.map(item => (
                <div
                  key={item.key}
                  onClick={() => {
                    if (onSelectionChange) {
                      const current = message.selections || [];
                      const next = current.includes(item.key)
                        ? current.filter(k => k !== item.key)
                        : [...current, item.key];
                      onSelectionChange(next);
                    }
                  }}
                  style={{
                    padding: '8px 12px',
                    marginBottom: 4,
                    borderRadius: 6,
                    cursor: 'pointer',
                    border: `1px solid ${(message.selections || []).includes(item.key) ? '#1890ff' : '#e5e7eb'}`,
                    background: (message.selections || []).includes(item.key) ? '#e6f7ff' : '#fff',
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'center',
                  }}
                >
                  <span style={{ fontSize: 13 }}>{item.name}</span>
                  <Space size={4}>
                    {item.category && <Tag color="blue" style={{ fontSize: 10 }}>{item.category}</Tag>}
                    {(message.selections || []).includes(item.key) && (
                      <CheckCircleOutlined style={{ color: '#1890ff', fontSize: 12 }} />
                    )}
                  </Space>
                </div>
              ))}
            </div>
            <Button 
              type="primary" 
              size="small"
              disabled={(message.selections || []).length === 0}
              onClick={onConfirmSelection}
            >
              确认选择 ({(message.selections || []).length})
            </Button>
          </div>
        )}

        {message.contentType === 'env_select' && selectableItems && selectableItems.length > 0 && (
          <div style={{ marginTop: 8 }}>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 8 }}>
              {selectableItems.map(env => {
                const selected = (message.selections || []).includes(env.key);
                return (
                  <Tag
                    key={env.key}
                    color={selected ? 'blue' : 'default'}
                    style={{ 
                      cursor: 'pointer', 
                      padding: '4px 12px',
                      fontSize: 12,
                      border: selected ? '1px solid #1890ff' : '1px solid #d9d9d9',
                    }}
                    onClick={() => {
                      if (onSelectionChange) {
                        const current = message.selections || [];
                        const next = current.includes(env.key)
                          ? current.filter(e => e !== env.key)
                          : [...current, env.key];
                        onSelectionChange(next);
                      }
                    }}
                  >
                    {selected && <CheckCircleOutlined style={{ marginRight: 4 }} />}
                    {env.name}
                  </Tag>
                );
              })}
            </div>
            <Button 
              type="primary" 
              size="small"
              disabled={(message.selections || []).length === 0}
              onClick={onConfirmSelection}
            >
              确认选择 ({(message.selections || []).length})
            </Button>
          </div>
        )}

        {message.contentType === 'planner_prompts' && message.metadata?.confirmedPlanMarkdown ? (
          <div style={{ marginTop: 8 }}>
            <div style={{ fontSize: 12, color: '#8c8c8c', marginBottom: 8 }}>已确认的规划与骨架</div>
            <div
              style={{
                fontSize: 13,
                color: '#262626',
                lineHeight: 1.75,
                padding: 12,
                background: '#fafafa',
                borderRadius: 8,
                border: '1px solid #f0f0f0',
                maxHeight: 480,
                overflow: 'auto',
              }}
            >
              <MarkdownRenderer content={message.metadata.confirmedPlanMarkdown} />
            </div>
          </div>
        ) : null}

        {message.contentType === 'planner_prompts' && !message.metadata?.confirmedPlanMarkdown && message.metadata?.plannerPrompts && (
          <Collapse
            size="small"
            ghost
            style={{ marginTop: 8, background: '#fafafa', borderRadius: 6 }}
            items={message.metadata.plannerPrompts.map((p: any, i: number) => ({
              key: String(i),
              label: <span style={{ fontSize: 13 }}>{i + 1}. {p.chapter_title}</span>,
              children: (
                <div style={{ fontSize: 12, color: '#595959', lineHeight: 1.6, padding: '0 8px' }}>
                  <div style={{ marginBottom: 8 }}>
                    <Tag color="blue" style={{ fontSize: 10 }}>写作要求</Tag>
                    <div style={{ marginTop: 4, whiteSpace: 'pre-wrap' }}>{p.writer_prompt}</div>
                  </div>
                  <div>
                    <Tag color="orange" style={{ fontSize: 10 }}>评审要求</Tag>
                    <div style={{ marginTop: 4, whiteSpace: 'pre-wrap' }}>{p.reviewer_prompt}</div>
                  </div>
                </div>
              ),
            }))}
          />
        )}

        {message.contentType === 'chapter' && (
          <div style={{ marginTop: 8 }}>
            {message.metadata?.chapterTitle && (
              <Tag color="blue" style={{ marginBottom: 8 }}>
                章节 {message.metadata.chapterIndex}: {message.metadata.chapterTitle}
              </Tag>
            )}
            <div style={isUser ? { color: '#fff' } : {}}>
              <MarkdownRenderer content={message.content} />
            </div>
          </div>
        )}
      </div>
    );
  };

  return (
    <div style={{ marginBottom: 24, animation: 'fadeIn 0.3s ease-in-out' }}>
      <Bubble
        placement={isUser ? 'end' : 'start'}
        avatar={getAvatar()}
        content={renderContent()}
        styles={{
          content: {
            maxWidth: 750,
            background: isUser ? '#1890ff' : '#fff',
            color: isUser ? '#fff' : '#1f1f1f',
            boxShadow: '0 2px 8px rgba(0,0,0,0.04)',
            borderRadius: 12,
            padding: '12px 16px',
          }
        }}
      />
      <div style={{ 
        textAlign: isUser ? 'right' : 'left', 
        marginTop: 4, 
        padding: isUser ? '0 48px 0 0' : '0 0 0 48px',
        fontSize: 11, 
        color: '#bfbfbf' 
      }}>
        {formatTime(message.timestamp)}
      </div>
    </div>
  );
}
