"use client";

import React from "react";
import { QuestionCircleOutlined } from "@ant-design/icons";
import { Tooltip } from "antd";

export interface EnvironmentHeaderHelpProps {
  envName: string;
  onOpen: (envName: string) => void;
}

/** 环境列表头灰色问号，点击查看该环境五要素 */
export default function EnvironmentHeaderHelp({ envName, onOpen }: EnvironmentHeaderHelpProps) {
  return (
    <Tooltip title="查看环境五要素">
      <QuestionCircleOutlined
        role="button"
        tabIndex={0}
        aria-label={`查看${envName}五要素`}
        onClick={(e) => {
          e.stopPropagation();
          onOpen(envName);
        }}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            e.stopPropagation();
            onOpen(envName);
          }
        }}
        style={{
          color: "#bfbfbf",
          fontSize: 12,
          cursor: "pointer",
          flexShrink: 0,
        }}
      />
    </Tooltip>
  );
}
