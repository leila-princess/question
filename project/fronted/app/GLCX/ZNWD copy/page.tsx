'use client';

import { useState, useRef, useEffect } from 'react';
import { Input, Button, Avatar, Spin, Typography, Upload, Drawer, message, Tag } from 'antd';
import { SendOutlined, RobotOutlined, UserOutlined, CopyOutlined, LikeOutlined, DislikeOutlined, ReloadOutlined, PaperClipOutlined, FileTextOutlined, CloseOutlined, FilePdfOutlined, FileWordOutlined } from '@ant-design/icons';
import MarkdownRenderer from '@/components/MarkdownRenderer';

const { Text } = Typography;

interface ChatMessage {
  role: 'ai' | 'user';
  content: string;
  files?: FileInfo[];
}

interface FileInfo {
  name: string;
  type: string;
  size: number;
  url?: string;
}

export default function IntelligentQA() {
  const [input, setInput] = useState('');
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [loading, setLoading] = useState(false);
  const [attachedFiles, setAttachedFiles] = useState<FileInfo[]>([]);
  const [drawerVisible, setDrawerVisible] = useState(false);
  const [selectedFiles, setSelectedFiles] = useState<FileInfo[]>([]);
  const chatBoxRef = useRef<HTMLDivElement>(null);

  const suggestedQuestions = [
    '无人机高温试验的方法有哪些？',
    '振动试验的标准规范是什么？',
    '如何评估试验数据的可靠性？',
    '低温试验的注意事项有哪些？',
  ];

  useEffect(() => {
    if (chatBoxRef.current) {
      chatBoxRef.current.scrollTop = chatBoxRef.current.scrollHeight;
    }
  }, [messages]);

  const handleSend = async () => {
    if (!input.trim() && attachedFiles.length === 0) return;

    const userMessage = input.trim();
    const currentFiles = [...attachedFiles];
    
    setInput('');
    setAttachedFiles([]);
    setMessages(prev => [...prev, { 
      role: 'user', 
      content: userMessage || '请分析上传的文件',
      files: currentFiles 
    }]);
    setLoading(true);

    try {
      const formData = new FormData();
      formData.append('message', userMessage);
      
      currentFiles.forEach(file => {
        formData.append('files', file.name);
      });

      const response = await fetch('/api/hello', {
        method: 'POST',
        body: formData,
      });

      const data = await response.json();
      const aiContent = data.response || data.content || '抱歉，无法获取回答';
      const relatedFiles = data.files || [];

      setMessages(prev => [...prev, { 
        role: 'ai', 
        content: aiContent,
        files: relatedFiles 
      }]);
    } catch (error) {
      setMessages(prev => [...prev, { 
        role: 'ai', 
        content: '请求失败，请稍后重试' 
      }]);
    } finally {
      setLoading(false);
    }
  };

  const handleRegenerate = async (messageIndex: number) => {
    const userMsg = messages[messageIndex - 1];
    if (!userMsg || userMsg.role !== 'user') return;

    setLoading(true);
    
    try {
      const response = await fetch('/api/hello', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: userMsg.content }),
      });

      const data = await response.json();
      const aiContent = data.response || data.content || '抱歉，无法获取回答';

      setMessages(prev => {
        const newMessages = [...prev];
        newMessages[messageIndex] = { 
          ...newMessages[messageIndex], 
          content: aiContent 
        };
        return newMessages;
      });
      
      message.success('已重新生成回答');
    } catch (error) {
      message.error('重新生成失败');
    } finally {
      setLoading(false);
    }
  };

  const copyToClipboard = (text: string) => {
    navigator.clipboard.writeText(text);
    message.success('已复制到剪贴板');
  };

  const handleFileUpload = (file: File) => {
    const fileInfo: FileInfo = {
      name: file.name,
      type: file.type,
      size: file.size,
    };
    setAttachedFiles(prev => [...prev, fileInfo]);
    message.success(`已添加 ${file.name}`);
    return false;
  };

  const removeFile = (index: number) => {
    setAttachedFiles(prev => prev.filter((_, i) => i !== index));
  };

  const showFileDrawer = (files: FileInfo[]) => {
    setSelectedFiles(files);
    setDrawerVisible(true);
  };

  const getFileIcon = (type: string) => {
    if (type.includes('pdf')) return <FilePdfOutlined style={{ color: '#ef4444' }} />;
    if (type.includes('word') || type.includes('doc')) return <FileWordOutlined style={{ color: '#3b82f6' }} />;
    return <FileTextOutlined style={{ color: '#6b7280' }} />;
  };

  const formatFileSize = (bytes: number) => {
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
    return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
  };

  return (
    <div style={{
      height: '90vh',
      display: 'flex',
      flexDirection: 'column',
      background: '#f7f7f8',
    }}>
      <div
        ref={chatBoxRef}
        style={{
          flex: 1,
          overflowY: 'auto',
          padding: '24px 0',
        }}
      >
        {messages.length === 0 ? (
          <div style={{
            maxWidth: 1020,
            margin: '0 auto',
            padding: '80px 24px',
            textAlign: 'center',
          }}>
            <div style={{
              fontSize: 32,
              fontWeight: 600,
              color: '#1f2937',
              marginBottom: 16,
            }}>
              智能问答助手
            </div>
            <Text type="secondary" style={{ fontSize: 16, display: 'block', marginBottom: 48 }}>
              专业的试验标准知识问答与咨询服务
            </Text>

            <div style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(2, 1fr)',
              gap: 12,
              maxWidth: 600,
              margin: '0 auto',
            }}>
              {suggestedQuestions.map((question, index) => (
                <div
                  key={index}
                  onClick={() => setInput(question)}
                  style={{
                    padding: '16px',
                    background: '#fff',
                    borderRadius: 12,
                    border: '1px solid #e5e7eb',
                    cursor: 'pointer',
                    textAlign: 'left',
                    transition: 'all 0.2s',
                    fontSize: 14,
                    color: '#374151',
                  }}
                  onMouseEnter={(e) => {
                    e.currentTarget.style.borderColor = '#3b82f6';
                    e.currentTarget.style.boxShadow = '0 2px 8px rgba(59, 130, 246, 0.15)';
                  }}
                  onMouseLeave={(e) => {
                    e.currentTarget.style.borderColor = '#e5e7eb';
                    e.currentTarget.style.boxShadow = 'none';
                  }}
                >
                  {question}
                </div>
              ))}
            </div>
          </div>
        ) : (
          <div style={{ maxWidth: 1020, margin: '0 auto', padding: '0 24px' }}>
            {messages.map((msg, idx) => (
              <div
                key={idx}
                style={{
                  display: 'flex',
                  flexDirection: msg.role === 'user' ? 'row-reverse' : 'row',
                  gap: 12,
                  marginBottom: 24,
                  alignItems: 'flex-start',
                }}
              >
                <Avatar
                  size={36}
                  icon={msg.role === 'user' ? <UserOutlined /> : <RobotOutlined />}
                  style={{
                    background: msg.role === 'user' ? '#3b82f6' : '#10b981',
                    flexShrink: 0,
                  }}
                />
                <div style={{ maxWidth: '80%' }}>
                  <div
                    style={{
                      padding: '12px 16px',
                      borderRadius: 12,
                      background: msg.role === 'user' ? '#3b82f6' : '#fff',
                      color: msg.role === 'user' ? '#fff' : '#1f2937',
                      border: msg.role === 'user' ? 'none' : '1px solid #e5e7eb',
                      fontSize: 15,
                      lineHeight: 1.6,
                    }}
                  >
                    <MarkdownRenderer content={msg.content} />
                  </div>
                  
                  {msg.files && msg.files.length > 0 && (
                    <div style={{ marginTop: 8 }}>
                      <Button
                        type="link"
                        size="small"
                        icon={<FileTextOutlined />}
                        onClick={() => showFileDrawer(msg.files!)}
                        style={{ padding: 0, fontSize: 12 }}
                      >
                        查看相关文件 ({msg.files.length})
                      </Button>
                    </div>
                  )}
                  
                  {msg.role === 'ai' && (
                    <div style={{
                      display: 'flex',
                      gap: 8,
                      marginTop: 8,
                      paddingLeft: 4,
                    }}>
                      <Button
                        type="text"
                        size="small"
                        icon={<CopyOutlined />}
                        onClick={() => copyToClipboard(msg.content)}
                        style={{ color: '#9ca3af', fontSize: 12 }}
                      >
                        复制
                      </Button>
                      <Button
                        type="text"
                        size="small"
                        icon={<ReloadOutlined />}
                        onClick={() => handleRegenerate(idx)}
                        disabled={loading}
                        style={{ color: '#9ca3af', fontSize: 12 }}
                      >
                        重新生成
                      </Button>
                      <Button
                        type="text"
                        size="small"
                        icon={<LikeOutlined />}
                        style={{ color: '#9ca3af', fontSize: 12 }}
                      />
                      <Button
                        type="text"
                        size="small"
                        icon={<DislikeOutlined />}
                        style={{ color: '#9ca3af', fontSize: 12 }}
                      />
                    </div>
                  )}
                </div>
              </div>
            ))}
            
            {loading && (
              <div style={{ display: 'flex', gap: 12, marginBottom: 24 }}>
                <Avatar
                  size={36}
                  icon={<RobotOutlined />}
                  style={{ background: '#10b981', flexShrink: 0 }}
                />
                <div style={{
                  padding: '12px 16px',
                  borderRadius: 12,
                  background: '#fff',
                  border: '1px solid #e5e7eb',
                }}>
                  <Spin size="small" />
                  <Text style={{ marginLeft: 8, color: '#6b7280' }}>思考中...</Text>
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      <div style={{
        padding: '16px 24px 24px',
        background: '#f7f7f8',
      }}>
        {attachedFiles.length > 0 && (
          <div style={{
            maxWidth: 1020,
            margin: '0 auto 12px',
            display: 'flex',
            flexWrap: 'wrap',
            gap: 8,
          }}>
            {attachedFiles.map((file, index) => (
              <Tag
                key={index}
                closable
                onClose={() => removeFile(index)}
                icon={getFileIcon(file.type)}
                style={{
                  padding: '4px 8px',
                  borderRadius: 6,
                  background: '#fff',
                  border: '1px solid #e5e7eb',
                }}
              >
                {file.name}
              </Tag>
            ))}
          </div>
        )}
        
        <div style={{
          maxWidth: 720,
          margin: '0 auto',
          position: 'relative',
        }}>
          <Upload
            beforeUpload={handleFileUpload}
            showUploadList={false}
            accept=".pdf,.doc,.docx,.txt,.md"
          >
            <Button
              icon={<PaperClipOutlined />}
              style={{
                position: 'absolute',
                left: 8,
                bottom: 8,
                width: 36,
                height: 36,
                borderRadius: 10,
                border: 'none',
                background: 'transparent',
                zIndex: 1,
              }}
            />
          </Upload>
          
          <Input.TextArea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onPressEnter={(e) => {
              if (!e.shiftKey && !loading) {
                e.preventDefault();
                handleSend();
              }
            }}
            placeholder="输入您的问题，按 Enter 发送，Shift + Enter 换行"
            autoSize={{ minRows: 1, maxRows: 4 }}
            style={{
              borderRadius: 16,
              padding: '12px 52px 12px 48px',
              fontSize: 15,
              background: '#fff',
              border: '1px solid #e5e7eb',
              resize: 'none',
              boxShadow: '0 2px 8px rgba(0, 0, 0, 0.04)',
            }}
            disabled={loading}
          />
          <Button
            type="primary"
            icon={<SendOutlined />}
            onClick={handleSend}
            disabled={(!input.trim() && attachedFiles.length === 0) || loading}
            style={{
              position: 'absolute',
              right: 8,
              bottom: 8,
              width: 36,
              height: 36,
              borderRadius: 10,
              background: (input.trim() || attachedFiles.length > 0) && !loading ? '#3b82f6' : '#e5e7eb',
              border: 'none',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
            }}
          />
        </div>
        <div style={{
          textAlign: 'center',
          marginTop: 12,
          fontSize: 12,
          color: '#9ca3af',
        }}>
          内容由 AI 生成，仅供参考
        </div>
      </div>

      <Drawer
        title="相关文件"
        placement="right"
        onClose={() => setDrawerVisible(false)}
        open={drawerVisible}
        size={400}
      >
        {selectedFiles.map((file, index) => (
          <div
            key={index}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 12,
              padding: '12px 16px',
              background: '#f9fafb',
              borderRadius: 8,
              marginBottom: 8,
            }}
          >
            <div style={{ fontSize: 24 }}>
              {getFileIcon(file.type)}
            </div>
            <div style={{ flex: 1 }}>
              <div style={{ fontSize: 14, color: '#1f2937', fontWeight: 500 }}>
                {file.name}
              </div>
              <div style={{ fontSize: 12, color: '#9ca3af', marginTop: 2 }}>
                {formatFileSize(file.size)}
              </div>
            </div>
            {file.url && (
              <Button
                type="link"
                size="small"
                onClick={() => window.open(file.url, '_blank')}
              >
                查看
              </Button>
            )}
          </div>
        ))}
      </Drawer>
    </div>
  );
}
