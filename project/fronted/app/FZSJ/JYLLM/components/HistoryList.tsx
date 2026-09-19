import { useState, useEffect } from 'react';
import { List, Empty, Spin, Popconfirm, Button, message } from 'antd';
import { HistoryOutlined, DeleteOutlined } from '@ant-design/icons';
import { fetchSchemes, deleteSchemeHistory } from '../api';
import type { HistoryRecord } from '../types/flow';

interface HistoryListProps {
  onRecordClick: (taskId: string) => void;
  userId?: string;
  /** 当前正在查看的任务，删除时可提示或清空主界面 */
  currentTaskId?: string;
  onDeleted?: (taskId: string) => void;
  /** 变更时重新拉取列表（如方案保存成功后刷新） */
  refreshKey?: number;
}

export default function HistoryList({ onRecordClick, userId = 'admin', currentTaskId, onDeleted, refreshKey = 0 }: HistoryListProps) {
  const [records, setRecords] = useState<HistoryRecord[]>([]);
  const [loading, setLoading] = useState(false);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  useEffect(() => { load(); }, [userId, refreshKey]);

  const load = async () => {
    setLoading(true);
    try {
      const data = await fetchSchemes(userId);
      setRecords((data as any[]) || []);
    } catch { /* ignore */ }
    finally { setLoading(false); }
  };

  const handleDelete = async (taskId: string) => {
    setDeletingId(taskId);
    try {
      await deleteSchemeHistory(taskId, userId);
      setRecords((prev) => prev.filter((r) => r.task_id !== taskId));
      message.success('已删除');
      onDeleted?.(taskId);
    } catch (err) {
      message.error(err instanceof Error ? err.message : '删除失败');
    } finally {
      setDeletingId(null);
    }
  };

  if (loading) return <div style={{ padding: 24, textAlign: 'center' }}><Spin /></div>;
  if (!records.length) return <Empty description="暂无历史记录" image={Empty.PRESENTED_IMAGE_SIMPLE} style={{ marginTop: 40 }} />;

  return (
    <List
      size="small"
      dataSource={records.slice(0, 50)}
      renderItem={(item: HistoryRecord) => (
        <List.Item
          onClick={() => onRecordClick(item.task_id)}
          style={{ cursor: 'pointer', padding: '6px 8px 6px 12px' }}
          actions={[
            <Popconfirm
              key="del"
              title="确定删除此方案？"
              description="将从数据库中移除该条历史，不可恢复。"
              onConfirm={() => void handleDelete(item.task_id)}
              okText="删除"
              okButtonProps={{ danger: true }}
            >
              <Button
                type="text"
                size="small"
                danger
                icon={<DeleteOutlined />}
                loading={deletingId === item.task_id}
                onClick={(e) => e.stopPropagation()}
                aria-label="删除"
              />
            </Popconfirm>,
          ]}
        >
          <List.Item.Meta
            avatar={<HistoryOutlined style={{ color: '#8c8c8c' }} />}
            title={
              <span style={{ fontSize: 12 }}>
                {(item as any).scheme_name || `方案 ${item.task_id.slice(0, 8)}`}
                {currentTaskId && item.task_id === currentTaskId ? (
                  <span style={{ marginLeft: 4, color: '#1890ff', fontSize: 10 }}>当前</span>
                ) : null}
              </span>
            }
            description={
              <span style={{ fontSize: 11 }}>
                {item.created_at?.slice(0, 10)} · {item.word_count}字
              </span>
            }
          />
        </List.Item>
      )}
    />
  );
}
