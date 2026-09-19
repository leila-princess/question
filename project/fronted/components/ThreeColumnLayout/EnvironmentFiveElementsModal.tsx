"use client";

import "katex/dist/katex.min.css";
import React, { useEffect, useMemo, useState } from "react";
import { Modal, Tabs, Typography, Spin, message, Tag, Divider } from "antd";
import MixedContent from "./MixedContent";
import { renderEnvironmentFieldContent } from "./EnvironmentFieldContent";
import {
  ENVIRONMENT_FIVE_ELEMENT_TABS,
  ensureEnvironmentFiveElementsLoaded,
  getEnvironmentFiveElements,
} from "./environmentFiveElements";

const { Text } = Typography;

export interface EnvironmentFiveElementsModalProps {
  open: boolean;
  envName: string | null;
  onClose: () => void;
}

export default function EnvironmentFiveElementsModal({
  open,
  envName,
  onClose,
}: EnvironmentFiveElementsModalProps) {
  const [loading, setLoading] = useState(false);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setLoading(true);
    setReady(false);
    ensureEnvironmentFiveElementsLoaded()
      .then(() => {
        if (!cancelled) setReady(true);
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          message.error(err instanceof Error ? err.message : "加载环境五要素失败");
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open]);

  const setting = envName && ready ? getEnvironmentFiveElements(envName) : undefined;

  const tabItems = useMemo(
    () =>
      ENVIRONMENT_FIVE_ELEMENT_TABS.map((cat) => ({
        key: cat.key,
        label: <Text strong>{cat.label}</Text>,
        children: React.createElement(
          "div",
          {
            style: {
              padding: 12,
              maxHeight: "60vh",
              overflow: "auto",
              background: "#fafafa",
              borderRadius: 6,
              border: "1px solid #f0f0f0",
            },
          },
          renderEnvironmentFieldContent(setting?.[cat.key] || ""),
        ),
      })),
    [setting],
  );

  return (
    <Modal
      title={envName ? `环境五要素 — ${envName}` : "环境五要素"}
      open={open}
      onCancel={onClose}
      footer={null}
      width={800}
      destroyOnHidden
      styles={{ body: { paddingTop: 12 } }}
    >
      {loading
        ? React.createElement(
            "div",
            { style: { textAlign: "center", padding: 40 } },
            <Spin tip="加载中..." />,
          )
        : !envName
          ? null
          : !setting
            ? React.createElement(Text, { type: "secondary" }, "未找到该环境的五要素数据")
            : React.createElement(Tabs, { tabPosition: "left", size: "small", items: tabItems })}
    </Modal>
  );
}
