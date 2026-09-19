"use client";

import React, { useCallback, useEffect, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Input,
  Select,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import {
  DownloadOutlined,
  ReloadOutlined,
  SearchOutlined,
} from "@ant-design/icons";

const { Title } = Typography;

export interface ActionLogRow {
  time: string;
  level: string;
  user: string;
  action: string;
  result?: string;
  error?: string;
}

export default function RZGLPage() {
  const [loading, setLoading] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [list, setList] = useState<ActionLogRow[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [level, setLevel] = useState<string>("ALL");
  const [keywordInput, setKeywordInput] = useState("");
  const [appliedKeyword, setAppliedKeyword] = useState("");

  const authHeaders = (): HeadersInit => {
    if (typeof window === "undefined") return {};
    const token = localStorage.getItem("token");
    return token ? { Authorization: `Bearer ${token}` } : {};
  };

  const load = useCallback(
    async (p: number, ps: number, lev: string, kw: string) => {
      setLoading(true);
      try {
        const params = new URLSearchParams();
        params.set("page", String(p));
        params.set("size", String(ps));
        if (lev && lev !== "ALL") {
          params.set("level", lev);
        }
        if (kw.trim()) {
          params.set("keyword", kw.trim());
        }
        const res = await fetch(`/api/xtgl/rzgl/logs?${params.toString()}`, {
          headers: authHeaders(),
        });
        const json = await res.json();
        if (!json.success) {
          message.error(json.error || "加载失败");
          setList([]);
          setTotal(0);
          return;
        }
        const data = json.data || {};
        setList(data.list || []);
        setTotal(typeof data.total === "number" ? data.total : 0);
      } catch (e) {
        message.error((e as Error).message || "网络错误");
        setList([]);
        setTotal(0);
      } finally {
        setLoading(false);
      }
    },
    []
  );

  useEffect(() => {
    load(page, pageSize, level, appliedKeyword);
  }, [page, pageSize, level, appliedKeyword, load]);

  const onSearch = () => {
    setAppliedKeyword(keywordInput.trim());
    setPage(1);
  };

  const onRefresh = () => {
    void load(page, pageSize, level, appliedKeyword);
  };

  const onDownload = async () => {
    setDownloading(true);
    try {
      const res = await fetch("/api/xtgl/rzgl/logs/download", {
        headers: authHeaders(),
      });
      if (!res.ok) {
        const j = await res.json().catch(() => ({}));
        message.error(j.error || `下载失败 (${res.status})`);
        return;
      }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "app.log";
      a.click();
      URL.revokeObjectURL(url);
      message.success("已开始下载");
    } catch (e) {
      message.error((e as Error).message || "下载失败");
    } finally {
      setDownloading(false);
    }
  };

  const levelColor = (lv: string) => {
    const u = (lv || "").toUpperCase();
    if (u === "ERROR") return "red";
    if (u === "WARNING") return "orange";
    if (u === "INFO") return "blue";
    return "default";
  };

  const columns: ColumnsType<ActionLogRow> = [
    { title: "时间", dataIndex: "time", width: 180, ellipsis: true },
    {
      title: "级别",
      dataIndex: "level",
      width: 100,
      render: (v: string) => <Tag color={levelColor(v)}>{v || "-"}</Tag>,
    },
    { title: "用户", dataIndex: "user", width: 120, ellipsis: true },
    { title: "动作", dataIndex: "action", ellipsis: true },
    {
      title: "结果 / 错误",
      key: "outcome",
      width: 220,
      ellipsis: true,
      render: (_, row) => {
        if (row.error != null && row.error !== "") {
          return <span style={{ color: "#cf1322" }}>{row.error}</span>;
        }
        if (row.result != null && row.result !== "") {
          return row.result;
        }
        return "—";
      },
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
          <Title level={4} style={{ margin: 0 }}>
            日志管理
          </Title>
          <Alert
            type="info"
            showIcon
            message="记录范围说明"
            description={
              <span>
                以下模块的增删改等操作会写入本页所查日志（由后端在接口成功时记录）：分类管理（图节点、解析编辑、Neo4j
                导入、环境批量导入）、文件管理（上传/更新元数据/删除/重复处理）、数据库管理（Neo4j/MinIO/Chroma
                元数据与控制台节点）、系统模型配置（XTGL
                model_config）、模型管理（MXGL）、用户管理、问题管理与回复。搜索时可按关键词匹配「动作」列。
              </span>
            }
          />
          <Space wrap align="center">
            <span>级别</span>
            <Select
              style={{ width: 120 }}
              value={level}
              onChange={(v) => {
                setLevel(v);
                setPage(1);
              }}
              options={[
                { value: "ALL", label: "ALL" },
                { value: "INFO", label: "INFO" },
                { value: "ERROR", label: "ERROR" },
              ]}
            />
            <Input
              placeholder="关键词（匹配动作）"
              style={{ width: 220 }}
              value={keywordInput}
              onChange={(e) => setKeywordInput(e.target.value)}
              onPressEnter={onSearch}
              allowClear
            />
            <Button type="primary" icon={<SearchOutlined />} onClick={onSearch}>
              搜索
            </Button>
            <Button icon={<ReloadOutlined />} onClick={onRefresh}>
              刷新
            </Button>
            <Button
              icon={<DownloadOutlined />}
              loading={downloading}
              onClick={onDownload}
            >
              下载日志
            </Button>
          </Space>
          <Table<ActionLogRow>
            rowKey={(row, i) => `${row.time}-${row.action}-${i}`}
            loading={loading}
            columns={columns}
            dataSource={list}
            pagination={{
              current: page,
              pageSize,
              total,
              showSizeChanger: true,
              showTotal: (t) => `共 ${t} 条`,
              onChange: (p, ps) => {
                setPage(p);
                setPageSize(ps);
              },
            }}
            scroll={{ x: 900 }}
          />
        </Space>
      </Card>
    </div>
  );
}
