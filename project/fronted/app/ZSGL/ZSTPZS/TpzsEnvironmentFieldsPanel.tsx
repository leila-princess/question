"use client";

import React, { useEffect } from "react";
import { Typography } from "antd";
import MarkdownRenderer from "@/components/MarkdownRenderer";
import { renderEnvironmentFieldContent } from "@/components/ThreeColumnLayout/EnvironmentFieldContent";
import { rewriteEnvironmentMediaUrls, unescapeEnvironmentNewlines } from "@/components/ThreeColumnLayout/environmentMedia";

const { Text } = Typography;

export type TpzsEnvironmentField = { label: string; key: string; value: string };

function fieldUsesMarkdown(value: string): boolean {
  return /\|.+\|/.test(value) || /^#{1,6}\s/m.test(value) || /!\[[^\]]*\]\(/.test(value);
}

function TpzsEnvironmentFieldValue({ value }: { value: string }) {
  const prepared = rewriteEnvironmentMediaUrls(unescapeEnvironmentNewlines(value || ""));
  if (!prepared.trim()) return <Text type="secondary">—</Text>;
  if (fieldUsesMarkdown(prepared)) {
    return <MarkdownRenderer content={prepared} allowHtml enableImageDownload />;
  }
  return renderEnvironmentFieldContent(prepared, { enableImageDownload: true });
}

export default function TpzsEnvironmentFieldsPanel({
  fields,
}: {
  fields: TpzsEnvironmentField[];
}) {
  useEffect(() => {
    if (typeof document === "undefined") return;
    const id = "tpzs-env-field-rich-styles";
    if (document.getElementById(id)) return;
    const el = document.createElement("style");
    el.id = id;
    el.textContent = `
      .tpzs-env-fields-panel .html-table-wrapper table { border-collapse: collapse; width: 100%; margin: 8px 0; }
      .tpzs-env-fields-panel .html-table-wrapper th,
      .tpzs-env-fields-panel .html-table-wrapper td {
        border: 1px solid #d9d9d9;
        padding: 6px 10px;
        vertical-align: top;
      }
      .tpzs-env-fields-panel .html-table-wrapper th { background: #fafafa; font-weight: 600; }
      .tpzs-env-fields-panel .mixed-content-html table { border-collapse: collapse; width: 100%; margin: 8px 0; }
      .tpzs-env-fields-panel .mixed-content-html th,
      .tpzs-env-fields-panel .mixed-content-html td {
        border: 1px solid #d9d9d9;
        padding: 6px 10px;
      }
      .tpzs-env-fields-panel .mixed-content-html th { background: #fafafa; font-weight: 600; }
      .tpzs-env-fields-panel .env-media-figure img { background: #fafafa; }
    `;
    document.head.appendChild(el);
  }, []);

  return (
    <div className="tpzs-env-fields-panel" style={{ width: "100%" }}>
      {fields.map((f) => (
        <div
          key={f.key}
          style={{
            marginBottom: 14,
            padding: "12px 14px",
            background: "#fafafa",
            borderRadius: 8,
            border: "1px solid #f0f0f0",
          }}
        >
          <Text strong style={{ display: "block", marginBottom: 8, color: "#1f1f1f", fontSize: 14 }}>
            {f.label}
          </Text>
          <TpzsEnvironmentFieldValue value={f.value} />
        </div>
      ))}
    </div>
  );
}
