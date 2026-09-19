'use client';

import React, { useState, useEffect, useRef, useCallback } from 'react';
import { Bubble, Sender } from '@ant-design/x';
import type { BubbleItemType } from '@ant-design/x/es/bubble/interface';
import {
  Layout, Collapse, Tag, Typography, Button, List, Popconfirm, Empty, Spin,
  Avatar, message as antMessage, Modal
} from 'antd';
import {
  DeleteOutlined, ClearOutlined, RobotOutlined, UserOutlined, SearchOutlined,
  FileSearchOutlined, MergeCellsOutlined, ThunderboltOutlined, LoadingOutlined,
  CheckCircleOutlined, EyeOutlined
} from '@ant-design/icons';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeHighlight from 'rehype-highlight';
import 'highlight.js/styles/github.css';
import { useSSE } from '@/app/hooks/useSSE';

const { Sider, Content } = Layout;
const { Text, Paragraph } = Typography;

interface ThinkingStep {
  type: string;
  message: string;
  status?: 'loading' | 'done';
  hits?: number;
  sample?: string[];
  chunks?: number;
  sources?: string[];
  totalHits?: number;
  neo4jHits?: number;
  chromaHits?: number;
  results?: any[];
  matchedChunks?: any[];
  fullStandards?: any[];
}

interface HistoryItem {
  id: number;
  question: string;
  fullQuestion?: string;
  createdAt: string;
}

interface ChatMessage {
  id: string;
  role: 'user' | 'ai';
  content: string;
  thinkingSteps?: ThinkingStep[];
}

function getStepIcon(type: string, status?: string) {
  if (status === 'loading') return <LoadingOutlined style={{ color: '#1677ff' }} />;
  if (status === 'done') return <CheckCircleOutlined style={{ color: '#52c41a' }} />;
  switch (type) {
    case 'neo4j_search': return <SearchOutlined />;
    case 'chroma_search': return <FileSearchOutlined />;
    case 'fusion': return <MergeCellsOutlined />;
    case 'generating': return <ThunderboltOutlined />;
    default: return <SearchOutlined />;
  }
}

function getStepLabel(type: string) {
  switch (type) {
    case 'neo4j_search': return '图数据库检索';
    case 'chroma_search': return '知识库检索';
    case 'fusion': return '结果融合';
    case 'generating': return '生成答案';
    default: return '处理步骤';
  }
}

function Neo4jResultModal({ open, onClose, results }: { open: boolean; onClose: () => void; results: any[] }) {
  return (
    <Modal title="图数据库检索结果" open={open} onCancel={onClose} footer={null} width={700}>
      <List
        dataSource={results}
        renderItem={(item) => (
          <List.Item style={{ display: 'block', padding: '8px 0' }}>
            <Tag color="blue">{item.labels}</Tag>
            <div style={{ marginTop: 4, fontSize: 13 }}>
              {Object.entries(item.properties || {}).map(([k, v]) => (
                <div key={k} style={{ marginBottom: 2 }}>
                  <Text strong>{k}:</Text> <Text>{String(v)}</Text>
                </div>
              ))}
            </div>
          </List.Item>
        )}
      />
    </Modal>
  );
}

function cleanContent(text: string): string {
  return text.replace(/=====\s*Page\s*\d+\s*=====/g, '').trim();
}

function ChromaResultModal({ open, onClose, standards }: { open: boolean; onClose: () => void; standards: any[] }) {
  return (
    <Modal title="知识库检索结果" open={open} onCancel={onClose} footer={null} width={800}>
      {standards.map((std) => (
        <div key={std.doc_id} style={{ marginBottom: 16, padding: 12, background: '#fafafa', borderRadius: 8 }}>
          <Tag color="green">{std.doc_id}</Tag>
          <Text strong style={{ marginLeft: 8 }}>{std.title}</Text>
          <Text type="secondary" style={{ marginLeft: 8 }}>({std.chunk_count} 个片段)</Text>
          <div style={{ marginTop: 8, fontSize: 13, maxHeight: 400, overflow: 'auto' }}>
            <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]}>
              {cleanContent(std.content)}
            </ReactMarkdown>
          </div>
        </div>
      ))}
    </Modal>
  );
}

function ThinkingPanel({ steps }: { steps: ThinkingStep[] }) {
  const [neo4jModalOpen, setNeo4jModalOpen] = useState(false);
  const [chromaModalOpen, setChromaModalOpen] = useState(false);

  if (!steps || steps.length === 0) return null;

  const neo4jStep = steps.find(s => s.type === 'neo4j_search');
  const chromaStep = steps.find(s => s.type === 'chroma_search');

  const collapseItems = steps.map((step, idx) => ({
    key: String(idx),
    label: (
      <span style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        {getStepIcon(step.type, step.status)}
        <Text strong>{getStepLabel(step.type)}</Text>
        <Text type="secondary" style={{ fontSize: 12 }}>{step.message}</Text>
      </span>
    ),
    children: (
      <div style={{ padding: '4px 0', fontSize: 13 }}>
        {step.type === 'neo4j_search' && step.hits !== undefined && (
          <div>
            <Tag color="blue">命中 {step.hits} 个节点</Tag>
            {step.sample && step.sample.length > 0 && (
              <div style={{ marginTop: 8 }}>
                <Text type="secondary">示例节点：</Text>
                {step.sample.map((s, i) => (
                  <div key={i} style={{ marginLeft: 12, color: '#666' }}>{s}</div>
                ))}
              </div>
            )}
            {step.results && step.results.length > 3 && (
              <Button
                type="link" size="small" icon={<EyeOutlined />}
                style={{ padding: 0, marginTop: 4 }}
                onClick={() => setNeo4jModalOpen(true)}
              >
                查看全部 {step.results.length} 个节点
              </Button>
            )}
          </div>
        )}
        {step.type === 'chroma_search' && step.chunks !== undefined && (
          <div>
            <Tag color="green">找到 {step.chunks} 个匹配片段</Tag>
            {step.sources && step.sources.length > 0 && (
              <div style={{ marginTop: 8 }}>
                <Text type="secondary">来源：</Text>
                {step.sources.map((s, i) => (
                  <Tag key={i} style={{ marginTop: 4 }}>{s}</Tag>
                ))}
              </div>
            )}
            {step.matchedChunks && step.matchedChunks.length > 0 && (
              <div style={{ marginTop: 8 }}>
                <Text type="secondary">匹配片段预览：</Text>
                {step.matchedChunks.map((c, i) => (
                  <div key={i} style={{
                    marginLeft: 12, marginTop: 4, padding: 6,
                    background: '#fff', borderRadius: 4, border: '1px solid #f0f0f0'
                  }}>
                    <Tag color="geekblue">{c.doc_id}</Tag>
                    <Text type="secondary" style={{ fontSize: 11 }}>
                      距离: {c.distance?.toFixed(4)}
                    </Text>
                    <div style={{ fontSize: 12, color: '#555', marginTop: 2 }}>
                      {cleanContent(c.preview)}
                    </div>
                  </div>
                ))}
              </div>
            )}
            {step.fullStandards && step.fullStandards.length > 0 && (
              <Button
                type="link" size="small" icon={<EyeOutlined />}
                style={{ padding: 0, marginTop: 4 }}
                onClick={() => setChromaModalOpen(true)}
              >
                查看 {step.fullStandards.length} 个标准完整内容
              </Button>
            )}
          </div>
        )}
        {step.type === 'fusion' && (
          <div>
            <Tag color="purple">Neo4j: {step.neo4jHits ?? 0} 条</Tag>
            <Tag color="cyan">Chroma: {step.chromaHits ?? 0} 条</Tag>
            <Tag color="orange">总计: {step.totalHits ?? 0} 条</Tag>
          </div>
        )}
        {step.type === 'generating' && (
          <Text type="secondary">
            {step.status === 'loading' ? '正在调用大语言模型生成回答...' : '答案生成完毕'}
          </Text>
        )}
      </div>
    ),
  }));

  return (
    <>
      <Collapse
        size="small" ghost
        items={collapseItems}
        style={{ marginTop: 8, background: '#fafafa', borderRadius: 8 }}
      />
      <Neo4jResultModal
        open={neo4jModalOpen}
        onClose={() => setNeo4jModalOpen(false)}
        results={neo4jStep?.results || []}
      />
      <ChromaResultModal
        open={chromaModalOpen}
        onClose={() => setChromaModalOpen(false)}
        standards={chromaStep?.fullStandards || []}
      />
    </>
  );
}

export default function ZNWDPage() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [history, setHistory] = useState<HistoryItem[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [siderCollapsed, setSiderCollapsed] = useState(false);
  const [inputValue, setInputValue] = useState('');
  const chatEndRef = useRef<HTMLDivElement>(null);
  const { sendMessage, stopGeneration, isGenerating } = useSSE();

  useEffect(() => { loadHistory(); }, []);
  useEffect(() => { chatEndRef.current?.scrollIntoView({ behavior: 'smooth' }); }, [messages]);

  const loadHistory = async () => {
    setHistoryLoading(true);
    try {
      const res = await fetch('/api/znwd/history');
      const data = await res.json();
      setHistory(data.items || []);
    } catch (err) {
      console.error('Load history failed:', err);
    } finally {
      setHistoryLoading(false);
    }
  };

  const handleHistorySelect = async (id: number) => {
    try {
      const res = await fetch(`/api/znwd/history/${id}`);
      const data = await res.json();
      if (data.question) {
        setMessages([
          { id: `user-${id}`, role: 'user', content: data.question },
          { id: `ai-${id}`, role: 'ai', content: data.answer || '', thinkingSteps: data.thinkingSteps || [] },
        ]);
      }
    } catch (err) {
      console.error('Load history detail failed:', err);
    }
  };

  const handleDeleteHistory = async (id: number) => {
    try {
      await fetch(`/api/znwd/history/${id}`, { method: 'DELETE' });
      antMessage.success('已删除');
      loadHistory();
    } catch {
      antMessage.error('删除失败');
    }
  };

  const handleClearHistory = async () => {
    try {
      await fetch('/api/znwd/history', { method: 'DELETE' });
      setHistory([]);
      setMessages([]);
      antMessage.success('已清空');
    } catch {
      antMessage.error('清空失败');
    }
  };

  const handleSend = useCallback(async (msg: string) => {
    if (!msg.trim()) return;

    setInputValue('');

    setMessages(prev => [...prev, { id: `user-${Date.now()}`, role: 'user', content: msg }]);

    const aiMsgId = `ai-${Date.now()}`;
    setMessages(prev => [...prev, { id: aiMsgId, role: 'ai', content: '', thinkingSteps: [] }]);

    await sendMessage(msg, (event) => {
      if (event.type === 'step') {
        const stepData = event.data as ThinkingStep;
        setMessages(prev =>
          prev.map(m => {
            if (m.id !== aiMsgId) return m;
            const existingSteps = m.thinkingSteps || [];
            const existIdx = existingSteps.findIndex(s => s.type === stepData.type);
            let newSteps: ThinkingStep[];
            if (existIdx >= 0) {
              newSteps = [...existingSteps];
              newSteps[existIdx] = stepData;
            } else {
              newSteps = [...existingSteps, stepData];
            }
            return { ...m, thinkingSteps: newSteps };
          })
        );
      } else if (event.type === 'token') {
        const tokenData = event.data as { content: string };
        setMessages(prev =>
          prev.map(m =>
            m.id === aiMsgId ? { ...m, content: m.content + tokenData.content } : m
          )
        );
      } else if (event.type === 'done') {
        loadHistory();
      }
    });
  }, [sendMessage]);

  const bubbleItems: BubbleItemType[] = messages.map(msg => {
    if (msg.role === 'user') {
      return { key: msg.id, role: 'user', content: msg.content, placement: 'end' };
    }

    const aiContent = (
      <div>
        {msg.content ? (
          <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]}>
            {msg.content}
          </ReactMarkdown>
        ) : (
          <div style={{ color: '#999', display: 'flex', alignItems: 'center', gap: 8 }}>
            <LoadingOutlined /> 思考中...
          </div>
        )}
        {msg.thinkingSteps && msg.thinkingSteps.length > 0 && (
          <ThinkingPanel steps={msg.thinkingSteps} />
        )}
      </div>
    );

    return {
      key: msg.id, role: 'ai', content: aiContent, placement: 'start',
      loading: !msg.content && isGenerating,
    };
  });

  const roles: Record<string, any> = {
    ai: {
      placement: 'start',
      avatar: <Avatar icon={<RobotOutlined />} style={{ background: '#1677ff' }} />,
      variant: 'outlined', shape: 'corner',
    },
    user: {
      placement: 'end',
      avatar: <Avatar icon={<UserOutlined />} style={{ background: '#87d068' }} />,
      variant: 'filled', shape: 'corner',
    },
  };

  return (
    <Layout style={{ height: 'calc(100vh - 70px)', background: '#fff' }}>
      <Sider
        width={280} collapsed={siderCollapsed} onCollapse={setSiderCollapsed} theme="light"
        style={{ borderRight: '1px solid #f0f0f0', display: 'flex', flexDirection: 'column' }}
      >
        <div style={{ padding: '16px', borderBottom: '1px solid #f0f0f0', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <Text strong style={{ fontSize: 15 }}>历史对话</Text>
          <Popconfirm title="确定清空所有历史？" onConfirm={handleClearHistory} okText="确定" cancelText="取消">
            <Button type="text" size="small" icon={<ClearOutlined />} danger title="清空全部" />
          </Popconfirm>
        </div>
        <div style={{ flex: 1, overflowY: 'auto' }}>
          {historyLoading ? (
            <div style={{ textAlign: 'center', padding: 40 }}><Spin /></div>
          ) : history.length === 0 ? (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无对话记录" style={{ marginTop: 40 }} />
          ) : (
            <List
              dataSource={history}
              renderItem={(item) => (
                <List.Item
                  style={{ padding: '12px 16px', cursor: 'pointer', borderBottom: '1px solid #f5f5f5' }}
                  onClick={() => handleHistorySelect(item.id)}
                  actions={[
                    <Popconfirm key="delete" title="删除？"
                      onConfirm={(e) => { e?.stopPropagation(); handleDeleteHistory(item.id); }}
                      onCancel={(e) => e?.stopPropagation()}>
                      <Button type="text" size="small" danger icon={<DeleteOutlined />} onClick={(e) => e.stopPropagation()} />
                    </Popconfirm>,
                  ]}
                >
                  <List.Item.Meta
                    title={<Text ellipsis style={{ fontSize: 13 }}>{item.question}</Text>}
                    description={<Text type="secondary" style={{ fontSize: 11 }}>{new Date(item.createdAt).toLocaleString()}</Text>}
                  />
                </List.Item>
              )}
            />
          )}
        </div>
      </Sider>

      <Content style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
        <div style={{ flex: 1, overflowY: 'auto', padding: '16px 24px' }}>
          {messages.length === 0 ? (
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: '100%', color: '#999' }}>
              <RobotOutlined style={{ fontSize: 48, marginBottom: 16, color: '#1677ff' }} />
              <Paragraph style={{ fontSize: 16, color: '#666' }}>智能问答助手</Paragraph>
              <Paragraph type="secondary">支持图数据库 + 知识库双路检索，为您生成精准回答</Paragraph>
            </div>
          ) : (
            <Bubble.List items={bubbleItems} role={roles} autoScroll style={{ maxWidth: 900, margin: '0 auto' }} />
          )}
          <div ref={chatEndRef} />
        </div>
        <div style={{ padding: '12px 24px 16px', borderTop: '1px solid #f0f0f0', background: '#fafafa' }}>
          <div style={{ maxWidth: 900, margin: '0 auto' }}>
            <Sender value={inputValue} onChange={setInputValue} onSubmit={handleSend} loading={isGenerating} onCancel={stopGeneration}
              placeholder="请输入您的问题，Enter 发送，Shift+Enter 换行" autoSize={{ minRows: 1, maxRows: 4 }} />
          </div>
        </div>
      </Content>
    </Layout>
  );
}
