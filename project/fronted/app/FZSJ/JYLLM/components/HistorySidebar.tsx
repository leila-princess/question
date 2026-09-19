'use client';

import React, { useState, useEffect, useCallback } from 'react';
import {
  Card, List, Typography, Tag, Button, Space, Spin, Empty,
  Drawer, Descriptions, Divider, Timeline, Badge, Alert,
  message, Popconfirm
} from 'antd';
import {
  HistoryOutlined, DeleteOutlined, EyeOutlined, FileTextOutlined,
  CheckCircleOutlined, ClockCircleOutlined, LoadingOutlined,
  RobotOutlined, UserOutlined, FileMarkdownOutlined
} from '@ant-design/icons';
import { useRouter } from 'next/navigation';
import MarkdownRenderer from '@/components/MarkdownRenderer';

const { Title, Text, Paragraph } = Typography;

// 历史记录项类型
interface HistoryItem {
  task_id: string;
  scheme_name: string;
  generation_status: 'generating' | 'complete' | 'failed';
  created_at: string;
  completed_at: string | null;
  word_count: number;
}

// 方案详情类型
interface SchemeDetail {
  task_id: string;
  scheme_name: string;
  scheme_description?: string;
  selected_equipment: any[];
  selected_environments: any[];
  selected_projects: any[];
  selected_indicators: any[];
  selected_methods: any[];
  generated_content?: string;
  generation_status: string;
  word_count: number;
  chapters: any[];
  agent_interactions: any[];
  created_at: string;
  completed_at?: string;
}

interface HistorySidebarProps {
  userId: string;
  onRestore?: (detail: SchemeDetail) => void;
}

export default function HistorySidebar({ userId, onRestore }: HistorySidebarProps) {
  const router = useRouter();
  const [historyList, setHistoryList] = useState<HistoryItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [selectedItem, setSelectedItem] = useState<SchemeDetail | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);

  // 加载历史列表
  const loadHistory = useCallback(async () => {
    setLoading(true);
    try {
      const response = await fetch(`/api/fzsj/jyllm/history/list?user_id=${userId}&page=1&page_size=50`);
      const result = await response.json();
      
      if (result.success) {
        setHistoryList(result.items);
      } else {
        message.error(result.error || '加载历史记录失败');
      }
    } catch (e) {
      console.error('Load history error:', e);
    } finally {
      setLoading(false);
    }
  }, [userId]);

  useEffect(() => {
    loadHistory();
  }, [loadHistory]);

  // 查看详情
  const handleViewDetail = async (taskId: string) => {
    setDetailLoading(true);
    setDrawerOpen(true);
    
    try {
      const response = await fetch(`/api/fzsj/jyllm/history/detail/${taskId}?user_id=${userId}`);
      const result = await response.json();
      
      if (result.success) {
        setSelectedItem(result.data);
      } else {
        message.error(result.error || '加载详情失败');
      }
    } catch (e) {
      console.error('Load detail error:', e);
    } finally {
      setDetailLoading(false);
    }
  };

  // 删除历史
  const handleDelete = async (taskId: string) => {
    try {
      const response = await fetch(`/api/fzsj/jyllm/history/delete/${taskId}?user_id=${userId}`, {
        method: 'DELETE'
      });
      const result = await response.json();
      
      if (result.success) {
        message.success('已删除');
        loadHistory();
      } else {
        message.error(result.error || '删除失败');
      }
    } catch (e) {
      console.error('Delete error:', e);
    }
  };

  // 恢复到主界面
  const handleRestore = () => {
    if (selectedItem && onRestore) {
      onRestore(selectedItem);
      setDrawerOpen(false);
      message.success('已恢复历史方案');
    }
  };

  // 渲染状态标签
  const renderStatusTag = (status: string) => {
    switch (status) {
      case 'complete':
        return <Tag color="success" icon={<CheckCircleOutlined />}>已完成</Tag>;
      case 'generating':
        return <Tag color="processing" icon={<LoadingOutlined />}>生成中</Tag>;
      case 'failed':
        return <Tag color="error">失败</Tag>;
      default:
        return <Tag>{status}</Tag>;
    }
  };

  // 渲染历史列表项
  const renderHistoryItem = (item: HistoryItem) => (
    <List.Item
      actions={[
        <Button
          key="view"
          type="text"
          size="small"
          icon={<EyeOutlined />}
          onClick={() => handleViewDetail(item.task_id)}
        >
          查看
        </Button>,
        <Popconfirm
          key="delete"
          title="确认删除"
          description="删除后无法恢复，是否继续？"
          onConfirm={() => handleDelete(item.task_id)}
          okText="删除"
          okType="danger"
          cancelText="取消"
        >
          <Button type="text" size="small" danger icon={<DeleteOutlined />}>
            删除
          </Button>
        </Popconfirm>
      ]}
    >
      <List.Item.Meta
        title={
          <Space>
            <Text strong>{item.scheme_name || '未命名方案'}</Text>
            {renderStatusTag(item.generation_status)}
          </Space>
        }
        description={
          <Space direction="vertical" size={0}>
            <Text type="secondary" style={{ fontSize: 12 }}>
              创建时间: {new Date(item.created_at).toLocaleString('zh-CN')}
            </Text>
            {item.completed_at && (
              <Text type="secondary" style={{ fontSize: 12 }}>
                完成时间: {new Date(item.completed_at).toLocaleString('zh-CN')}
              </Text>
            )}
            {item.word_count > 0 && (
              <Text type="secondary" style={{ fontSize: 12 }}>
                字数: {item.word_count} 字
              </Text>
            )}
          </Space>
        }
      />
    </List.Item>
  );

  // 渲染详情Drawer
  const renderDetailDrawer = () => (
    <Drawer
      title={
        <Space>
          <HistoryOutlined />
          <span>历史方案详情</span>
        </Space>
      }
      size={800}
      open={drawerOpen}
      onClose={() => setDrawerOpen(false)}
      extra={
        selectedItem?.generation_status === 'complete' && (
          <Button type="primary" onClick={handleRestore}>
            恢复此方案
          </Button>
        )
      }
    >
      {detailLoading ? (
        <div style={{ textAlign: 'center', padding: 40 }}>
          <Spin size="large" />
          <p>加载中...</p>
        </div>
      ) : selectedItem ? (
        <Space direction="vertical" style={{ width: '100%' }} size="large">
          {/* 基本信息 */}
          <Card title="基本信息" size="small">
            <Descriptions column={2} size="small">
              <Descriptions.Item label="方案名称">{selectedItem.scheme_name}</Descriptions.Item>
              <Descriptions.Item label="任务ID">{selectedItem.task_id}</Descriptions.Item>
              <Descriptions.Item label="状态">{renderStatusTag(selectedItem.generation_status)}</Descriptions.Item>
              <Descriptions.Item label="字数">{selectedItem.word_count || 0} 字</Descriptions.Item>
              <Descriptions.Item label="创建时间">
                {new Date(selectedItem.created_at).toLocaleString('zh-CN')}
              </Descriptions.Item>
              <Descriptions.Item label="完成时间">
                {selectedItem.completed_at ? new Date(selectedItem.completed_at).toLocaleString('zh-CN') : '-'}
              </Descriptions.Item>
            </Descriptions>
          </Card>

          {/* 用户选择 */}
          <Card title="用户选择配置" size="small">
            <Space direction="vertical" style={{ width: '100%' }}>
              {selectedItem.selected_equipment?.length > 0 && (
                <div>
                  <Text strong>设备：</Text>
                  <Space wrap>
                    {selectedItem.selected_equipment.map((e: any, i: number) => (
                      <Tag key={i}>{e.name || e}</Tag>
                    ))}
                  </Space>
                </div>
              )}
              
              {selectedItem.selected_environments?.length > 0 && (
                <div>
                  <Text strong>环境：</Text>
                  <Space wrap>
                    {selectedItem.selected_environments.map((e: any, i: number) => (
                      <Tag key={i} color="blue">{e.label || e}</Tag>
                    ))}
                  </Space>
                </div>
              )}
              
              {selectedItem.selected_projects?.length > 0 && (
                <div>
                  <Text strong>项目：</Text>
                  <Space wrap>
                    {selectedItem.selected_projects.map((p: any, i: number) => (
                      <Tag key={i} color="green">{p.name || p}</Tag>
                    ))}
                  </Space>
                </div>
              )}
              
              {selectedItem.selected_indicators?.length > 0 && (
                <div>
                  <Text strong>指标：</Text>
                  <Space wrap>
                    {selectedItem.selected_indicators.map((ind: any, i: number) => (
                      <Tag key={i} color="orange">{ind.name || ind}</Tag>
                    ))}
                  </Space>
                </div>
              )}
              
              {selectedItem.selected_methods?.length > 0 && (
                <div>
                  <Text strong>方法：</Text>
                  <Space wrap>
                    {selectedItem.selected_methods.map((m: any, i: number) => (
                      <Tag key={i} color="purple">{m.name || m}</Tag>
                    ))}
                  </Space>
                </div>
              )}
            </Space>
          </Card>

          {/* 章节列表 */}
          {selectedItem.chapters?.length > 0 && (
            <Card title={`章节列表 (${selectedItem.chapters.length})`} size="small">
              <Timeline>
                {selectedItem.chapters.map((chapter: any, index: number) => (
                  <Timeline.Item
                    key={index}
                    dot={<FileTextOutlined />}
                    color={chapter.review_status === 'completed' ? 'green' : 'blue'}
                  >
                    <div>
                      <Text strong>{chapter.chapter_title}</Text>
                      <br />
                      <Text type="secondary" style={{ fontSize: 12 }}>
                        自动评分: {chapter.auto_score || '-'} | 
                        用户评分: {chapter.user_score || '-'} | 
                        状态: {chapter.review_status}
                      </Text>
                    </div>
                  </Timeline.Item>
                ))}
              </Timeline>
            </Card>
          )}

          {/* 智能体交互记录 */}
          {selectedItem.agent_interactions?.length > 0 && (
            <Card title={`智能体交互记录 (${selectedItem.agent_interactions.length})`} size="small">
              <List
                size="small"
                dataSource={selectedItem.agent_interactions.slice(0, 10)}
                renderItem={(interaction: any) => (
                  <List.Item>
                    <Space direction="vertical" size={0} style={{ width: '100%' }}>
                      <Space>
                        <Tag color={
                          interaction.agent_type === 'planner' ? 'purple' :
                          interaction.agent_type === 'writer' ? 'blue' :
                          interaction.agent_type === 'reviewer' ? 'orange' : 'default'
                        }>
                          {interaction.agent_type}
                        </Tag>
                        <Text type="secondary" style={{ fontSize: 12 }}>
                          {new Date(interaction.created_at).toLocaleString('zh-CN')}
                        </Text>
                      </Space>
                      {interaction.chapter_title && (
                        <Text type="secondary" style={{ fontSize: 12 }}>
                          章节: {interaction.chapter_title}
                        </Text>
                      )}
                      {interaction.score && (
                        <Text type="secondary" style={{ fontSize: 12 }}>
                          评分: {interaction.score}
                        </Text>
                      )}
                    </Space>
                  </List.Item>
                )}
              />
            </Card>
          )}

          {/* 生成内容预览 */}
          {selectedItem.generated_content && (
            <Card title="生成内容预览" size="small">
              <div style={{ maxHeight: 400, overflow: 'auto', padding: 12, background: '#f5f5f5', borderRadius: 4 }}>
                <MarkdownRenderer content={selectedItem.generated_content.substring(0, 2000) + '...'} />
              </div>
            </Card>
          )}
        </Space>
      ) : (
        <Empty description="未找到详情" />
      )}
    </Drawer>
  );

  return (
    <>
      <Card 
        title={
          <Space>
            <HistoryOutlined />
            <span>历史记录</span>
          </Space>
        }
        size="small"
        extra={
          <Button type="link" size="small" onClick={loadHistory}>
            刷新
          </Button>
        }
      >
        {loading ? (
          <div style={{ textAlign: 'center', padding: 20 }}>
            <Spin />
          </div>
        ) : historyList.length === 0 ? (
          <Empty description="暂无历史记录" image={Empty.PRESENTED_IMAGE_SIMPLE} />
        ) : (
          <List
            size="small"
            dataSource={historyList}
            renderItem={renderHistoryItem}
            pagination={{
              size: 'small',
              pageSize: 5,
              hideOnSinglePage: true
            }}
          />
        )}
      </Card>

      {renderDetailDrawer()}
    </>
  );
}
