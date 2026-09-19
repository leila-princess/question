"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Descriptions,
  InputNumber,
  Modal,
  Space,
  Switch,
  Table,
  Tag,
  Typography,
  message,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import {
  CloudUploadOutlined,
  ExclamationCircleFilled,
  RollbackOutlined,
  SyncOutlined,
} from "@ant-design/icons";

const { Text, Paragraph } = Typography;

export interface BackupDayRow {
  date: string;
  path: string;
  mysql_bytes: number;
  neo4j_bytes: number;
  minio_bytes: number;
  chroma_bytes: number;
  total_bytes: number;
}

interface TaskPart {
  status?: string;
  error?: string;
  reason?: string;
  bytes?: number;
  object_count?: number;
}

interface BackupTask {
  task_id: string;
  status: string;
  created?: string;
  started?: string | null;
  finished?: string | null;
  date?: string | null;
  parts?: Record<string, TaskPart>;
  error?: string | null;
}

interface BackupSchedule {
  enabled: boolean;
  hour: number;
  minute: number;
  last_autorun_ymd: string;
  backup_root: string;
  retention_days: number;
  schedule_file: string;
  server_time_note: string;
}

function formatBytes(n: number): string {
  if (n == null || n === 0) return "0 B";
  const k = 1024;
  const sizes = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.min(Math.floor(Math.log(n) / Math.log(k)), sizes.length - 1);
  return `${(n / Math.pow(k, i)).toFixed(i === 0 ? 0 : 2)} ${sizes[i]}`;
}

function partTag(p?: TaskPart) {
  if (!p) return <Tag>—</Tag>;
  const s = (p.status || "").toLowerCase();
  if (s === "success") return <Tag color="success">成功</Tag>;
  if (s === "skipped") return <Tag color="default">跳过</Tag>;
  if (s === "running") return <Tag color="processing">进行中</Tag>;
  if (s === "error") return <Tag color="error">失败</Tag>;
  return <Tag>{p.status || "—"}</Tag>;
}

export default function SJBFPage() {
  const [rows, setRows] = useState<BackupDayRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [backupSubmitting, setBackupSubmitting] = useState(false);
  const [restoreSubmitting, setRestoreSubmitting] = useState(false);
  const [taskModalOpen, setTaskModalOpen] = useState(false);
  const [taskModalKind, setTaskModalKind] = useState<"backup" | "restore">("backup");
  const [activeTask, setActiveTask] = useState<BackupTask | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const [schedule, setSchedule] = useState<BackupSchedule | null>(null);
  const [schedEnabled, setSchedEnabled] = useState(false);
  const [schedHour, setSchedHour] = useState(2);
  const [schedMinute, setSchedMinute] = useState(0);
  const [scheduleLoading, setScheduleLoading] = useState(false);
  const [scheduleSaving, setScheduleSaving] = useState(false);

  const authHeaders = (): HeadersInit => {
    if (typeof window === "undefined") return {};
    const token = localStorage.getItem("token");
    return token ? { Authorization: `Bearer ${token}` } : {};
  };

  const fetchBackups = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch("/api/backups", { headers: authHeaders() });
      const json = await res.json();
      if (!json.success) {
        message.error(json.error || "获取备份列表失败");
        setRows([]);
        return;
      }
      setRows(Array.isArray(json.data) ? json.data : []);
    } catch {
      message.error("网络错误");
      setRows([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchBackups();
  }, [fetchBackups]);

  const fetchSchedule = useCallback(async () => {
    setScheduleLoading(true);
    try {
      const res = await fetch("/api/backup/schedule", { headers: authHeaders() });
      const json = await res.json();
      if (!json.success || !json.data) {
        message.error(json.error || "获取自动备份配置失败");
        setSchedule(null);
        return;
      }
      const d = json.data as BackupSchedule;
      setSchedule(d);
      setSchedEnabled(!!d.enabled);
      setSchedHour(typeof d.hour === "number" ? d.hour : 2);
      setSchedMinute(typeof d.minute === "number" ? d.minute : 0);
    } catch {
      message.error("获取自动备份配置失败");
      setSchedule(null);
    } finally {
      setScheduleLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchSchedule();
  }, [fetchSchedule]);

  const saveSchedule = async () => {
    setScheduleSaving(true);
    try {
      const res = await fetch("/api/backup/schedule", {
        method: "PUT",
        headers: { ...authHeaders(), "Content-Type": "application/json" },
        body: JSON.stringify({
          enabled: schedEnabled,
          hour: schedHour,
          minute: schedMinute,
        }),
      });
      const json = await res.json();
      if (!json.success || !json.data) {
        message.error(json.error || "保存失败");
        return;
      }
      const d = json.data as BackupSchedule;
      setSchedule(d);
      setSchedEnabled(!!d.enabled);
      setSchedHour(typeof d.hour === "number" ? d.hour : 2);
      setSchedMinute(typeof d.minute === "number" ? d.minute : 0);
      message.success("自动备份设置已保存");
    } catch {
      message.error("保存失败");
    } finally {
      setScheduleSaving(false);
    }
  };

  const stopPoll = () => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  };

  const pollTask = (taskId: string, kind: "backup" | "restore") => {
    stopPoll();
    const endpoint = kind === "restore" ? `/api/restore/${taskId}` : `/api/backup/${taskId}`;
    const tick = async () => {
      try {
        const res = await fetch(endpoint, {
          headers: authHeaders(),
        });
        const json = await res.json();
        if (!json.success || !json.data) return;
        const t: BackupTask = { task_id: taskId, ...json.data };
        setActiveTask(t);
        const st = (t.status || "").toLowerCase();
        if (st === "completed") {
          stopPoll();
          message.success(kind === "restore" ? "恢复已完成" : "备份已完成");
          fetchBackups();
          setBackupSubmitting(false);
          setRestoreSubmitting(false);
        } else if (st === "failed" || st === "skipped") {
          stopPoll();
          message.error(t.error || (kind === "restore" ? "恢复失败" : "备份失败"));
          fetchBackups();
          setBackupSubmitting(false);
          setRestoreSubmitting(false);
        }
      } catch {
        /* 单次轮询失败忽略 */
      }
    };
    void tick();
    pollRef.current = setInterval(tick, 2000);
  };

  useEffect(() => () => stopPoll(), []);

  const handleBackup = () => {
    Modal.confirm({
      title: "确认全量备份",
      icon: <ExclamationCircleFilled />,
      content:
        "将异步备份 MySQL、Neo4j、MinIO、Chroma 到服务器备份目录（默认 /data/backup/日期/）。同一自然日目录会复用覆盖。",
      async onOk() {
        setBackupSubmitting(true);
        try {
          const res = await fetch("/api/backup", {
            method: "POST",
            headers: authHeaders(),
          });
          const json = await res.json();
          if (res.status === 202 && json.success && json.data?.task_id) {
            const tid = json.data.task_id as string;
            message.success("任务已提交，正在后台执行…");
            setActiveTask({
              task_id: tid,
              status: "pending",
              parts: {},
            });
            setTaskModalKind("backup");
            setTaskModalOpen(true);
            pollTask(tid, "backup");
            return;
          }
          message.error(json.error || `请求失败 (${res.status})`);
        } catch {
          message.error("备份请求失败");
        } finally {
          if (!pollRef.current) setBackupSubmitting(false);
        }
      },
    });
  };

  const handleRestore = (row: BackupDayRow) => {
    Modal.confirm({
      title: `确认恢复 ${row.date} 备份`,
      icon: <ExclamationCircleFilled />,
      okText: "确认恢复",
      okType: "danger",
      content: (
        <div>
          <Paragraph>
            将把该日备份目录中的数据<strong>覆盖导入</strong>到当前数据库（MySQL、Neo4j、MinIO、Chroma），
            现有数据将被替换，此操作不可撤销。
          </Paragraph>
          <Paragraph type="secondary" style={{ marginBottom: 0, fontSize: 12 }}>
            目录：{row.path}
          </Paragraph>
        </div>
      ),
      async onOk() {
        setRestoreSubmitting(true);
        try {
          const res = await fetch("/api/restore", {
            method: "POST",
            headers: { ...authHeaders(), "Content-Type": "application/json" },
            body: JSON.stringify({ date: row.date }),
          });
          const json = await res.json();
          if (res.status === 202 && json.success && json.data?.task_id) {
            const tid = json.data.task_id as string;
            message.success("恢复任务已提交，正在后台执行…");
            setActiveTask({
              task_id: tid,
              status: "pending",
              parts: {},
              date: row.date,
            });
            setTaskModalKind("restore");
            setTaskModalOpen(true);
            pollTask(tid, "restore");
            return;
          }
          message.error(json.error || `请求失败 (${res.status})`);
        } catch {
          message.error("恢复请求失败");
        } finally {
          if (!pollRef.current) setRestoreSubmitting(false);
        }
      },
    });
  };

  const columns: ColumnsType<BackupDayRow> = [
    { title: "备份日期", dataIndex: "date", width: 120 },
    {
      title: "MySQL",
      dataIndex: "mysql_bytes",
      width: 110,
      render: (v: number) => formatBytes(v),
    },
    {
      title: "Neo4j",
      dataIndex: "neo4j_bytes",
      width: 110,
      render: (v: number) => formatBytes(v),
    },
    {
      title: "MinIO",
      dataIndex: "minio_bytes",
      width: 110,
      render: (v: number) => formatBytes(v),
    },
    {
      title: "Chroma",
      dataIndex: "chroma_bytes",
      width: 110,
      render: (v: number) => formatBytes(v),
    },
    {
      title: "合计",
      dataIndex: "total_bytes",
      width: 120,
      render: (v: number) => <Text strong>{formatBytes(v)}</Text>,
    },
    {
      title: "目录",
      dataIndex: "path",
      ellipsis: true,
      render: (p: string) => (
        <Text copyable={{ text: p }} style={{ fontSize: 12 }}>
          {p}
        </Text>
      ),
    },
    {
      title: "操作",
      key: "actions",
      width: 100,
      fixed: "right",
      render: (_: unknown, row: BackupDayRow) => (
        <Button
          size="small"
          danger
          icon={<RollbackOutlined />}
          disabled={row.total_bytes === 0 || restoreSubmitting}
          onClick={() => handleRestore(row)}
        >
          恢复
        </Button>
      ),
    },
  ];

  return (
    <div
      style={{
        height: '100%',
        minHeight: 0,
        overflow: 'hidden',
        boxSizing: 'border-box',
        padding: 24,
        display: 'flex',
        flexDirection: 'column',
      }}
    >
      <Card
        style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}
        styles={{ body: { flex: 1, minHeight: 0, overflow: 'auto' } }}
      >
        <Space direction="vertical" size="middle" style={{ width: "100%" }}>
          <Typography.Title level={4} style={{ margin: 0 }}>
            数据备份
          </Typography.Title>
          <Alert
            type="info"
            showIcon
            message="说明"
            description={
              <Paragraph style={{ marginBottom: 0 }}>
                备份根目录默认为 <Text code>/data/backup/</Text>
                ，按日期建子目录；保留天数由环境变量{" "}
                <Text code>BACKUP_RETENTION_DAYS</Text> 控制（下方显示当前服务端取值）。
                Windows 请设置 <Text code>BACKUP_ROOT</Text> 指向本地目录。
                若 MySQL / Neo4j 显示「跳过」，通常是因为当前进程找不到{" "}
                <Text code>mysqldump</Text>、<Text code>mysql</Text> 或{" "}
                <Text code>neo4j-admin</Text>
                ：请安装客户端并加入 PATH，或设置{" "}
                <Text code>MYSQLDUMP_BIN</Text>、<Text code>MYSQL_BIN</Text>、
                <Text code>NEO4J_ADMIN_BIN</Text>、<Text code>NEO4J_HOME</Text>
                （见仓库 <Text code>.env.example</Text>）。
                Neo4j 5 备份/恢复前通常需先停止 Neo4j 服务。
                备份列表中可对某日备份执行「恢复」，将原样覆盖导入当前数据库。
              </Paragraph>
            }
          />
          <Card
            size="small"
            title="自动备份（服务器本地时间）"
            loading={scheduleLoading}
          >
            <Space direction="vertical" size="small" style={{ width: "100%" }}>
              {schedule && (
                <Text type="secondary" style={{ fontSize: 12 }}>
                  当前保留 {schedule.retention_days} 天；备份根目录{" "}
                  <Text code copyable={{ text: schedule.backup_root }}>
                    {schedule.backup_root}
                  </Text>
                  ；配置保存在{" "}
                  <Text code copyable={{ text: schedule.schedule_file }} style={{ fontSize: 11 }}>
                    {schedule.schedule_file}
                  </Text>
                </Text>
              )}
              <Space wrap align="center">
                <Text>启用每日定时</Text>
                <Switch checked={schedEnabled} onChange={setSchedEnabled} />
                <Text>时刻</Text>
                <InputNumber
                  min={0}
                  max={23}
                  value={schedHour}
                  onChange={(v) => setSchedHour(typeof v === "number" ? v : 0)}
                />
                <Text>:</Text>
                <InputNumber
                  min={0}
                  max={59}
                  value={schedMinute}
                  onChange={(v) => setSchedMinute(typeof v === "number" ? v : 0)}
                />
                <Button type="primary" loading={scheduleSaving} onClick={() => void saveSchedule()}>
                  保存设置
                </Button>
              </Space>
              {schedule?.last_autorun_ymd ? (
                <Text type="secondary" style={{ fontSize: 12 }}>
                  上次自动触发标记日期：{schedule.last_autorun_ymd}（标记在任务排队时写入，与是否跳过部分组件无关）
                </Text>
              ) : null}
              {schedule?.server_time_note ? (
                <Text style={{ fontSize: 12 }}>{schedule.server_time_note}</Text>
              ) : null}
            </Space>
          </Card>
          <Space wrap>
            <Button
              type="primary"
              icon={<CloudUploadOutlined />}
              onClick={handleBackup}
              loading={backupSubmitting}
            >
              立即备份
            </Button>
            <Button icon={<SyncOutlined />} onClick={() => { void fetchBackups(); void fetchSchedule(); }}>
              刷新列表
            </Button>
          </Space>
          <Table<BackupDayRow>
            rowKey="date"
            columns={columns}
            dataSource={rows}
            loading={loading}
            pagination={{ pageSize: 14, showTotal: (t) => `共 ${t} 天` }}
            scroll={{ x: 1100 }}
          />
        </Space>
      </Card>

      <Modal
        title={taskModalKind === "restore" ? "恢复任务" : "备份任务"}
        open={taskModalOpen}
        onCancel={() => {
          stopPoll();
          setTaskModalOpen(false);
          setBackupSubmitting(false);
          setRestoreSubmitting(false);
          setActiveTask(null);
        }}
        footer={
          <Button
            type="primary"
            onClick={() => {
              stopPoll();
              setTaskModalOpen(false);
              setBackupSubmitting(false);
              setRestoreSubmitting(false);
            }}
          >
            关闭
          </Button>
        }
        width={560}
      >
        {activeTask && (
          <Descriptions bordered size="small" column={1}>
            <Descriptions.Item label="task_id">
              <Text copyable>{activeTask.task_id}</Text>
            </Descriptions.Item>
            <Descriptions.Item label="状态">
              <Tag
                color={
                  activeTask.status === "completed"
                    ? "success"
                    : activeTask.status === "failed"
                      ? "error"
                      : "processing"
                }
              >
                {activeTask.status}
              </Tag>
            </Descriptions.Item>
            <Descriptions.Item label="备份日期目录">
              {activeTask.date || "—"}
            </Descriptions.Item>
            {activeTask.error && (
              <Descriptions.Item label="错误">
                <Text type="danger">{activeTask.error}</Text>
              </Descriptions.Item>
            )}
            <Descriptions.Item label="MySQL">
              {partTag(activeTask.parts?.mysql)}
              {activeTask.parts?.mysql?.error && (
                <div>
                  <Text type="danger">{activeTask.parts.mysql.error}</Text>
                </div>
              )}
              {activeTask.parts?.mysql?.reason && (
                <div>
                  <Text type="secondary">{activeTask.parts.mysql.reason}</Text>
                </div>
              )}
            </Descriptions.Item>
            <Descriptions.Item label="Neo4j">
              {partTag(activeTask.parts?.neo4j)}
              {activeTask.parts?.neo4j?.error && (
                <div>
                  <Text type="danger">{activeTask.parts.neo4j.error}</Text>
                </div>
              )}
              {activeTask.parts?.neo4j?.reason && (
                <div>
                  <Text type="secondary">{activeTask.parts.neo4j.reason}</Text>
                </div>
              )}
            </Descriptions.Item>
            <Descriptions.Item label="MinIO">
              {partTag(activeTask.parts?.minio)}
              {activeTask.parts?.minio?.error && (
                <div>
                  <Text type="danger">{activeTask.parts.minio.error}</Text>
                </div>
              )}
            </Descriptions.Item>
            <Descriptions.Item label="Chroma">
              {partTag(activeTask.parts?.chroma)}
              {activeTask.parts?.chroma?.error && (
                <div>
                  <Text type="danger">{activeTask.parts.chroma.error}</Text>
                </div>
              )}
            </Descriptions.Item>
          </Descriptions>
        )}
      </Modal>
    </div>
  );
}
