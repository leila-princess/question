"use client";

import React, { useMemo } from "react";
import { renderEnvironmentFieldContent } from "@/components/ThreeColumnLayout/EnvironmentFieldContent";
import { normalizeEnvironmentFieldRaw } from "@/components/ThreeColumnLayout/environmentMedia";

/** 是否含 HTML 标签或实体编码的 HTML */
export function isEnvironmentHtmlValue(raw: string): boolean {
  const s = normalizeEnvironmentFieldRaw(raw);
  if (!s) return false;
  if (/<[a-z][\s\S]*?>/i.test(s)) return true;
  return /&lt;[a-z]/i.test(String(raw || ""));
}

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/** 回答区：将少量 Markdown 标题换成 HTML，保留五要素原始 HTML */
export function prepareEnvironmentHtml(raw: string): string {
  let s = normalizeEnvironmentFieldRaw(raw);
  if (!s) return "";

  s = s.replace(
    /^环境[「『""]([^」』""]+)[」』""]的(分类|分级|典型组合|约束|要求)[：:]\s*\n+/m,
    (_m, name, field) =>
      `<p style="margin:0 0 12px;color:#434343"><strong>环境「${escapeHtml(name)}」的${escapeHtml(field)}</strong></p>`,
  );

  s = s.replace(
    /^(环境[「『""].+?[」』""]属性如下[：:]?)\s*\n+/m,
    (_m, intro) => `<p style="margin:0 0 12px;color:#434343">${escapeHtml(intro.trim())}</p>`,
  );

  s = s.replace(
    /^###\s+(.+)$/gm,
    '<div style="font-weight:600;font-size:16px;margin:14px 0 8px">$1</div>',
  );

  s = s.replace(
    /^-\s+\*\*(分类|分级|典型组合|约束|要求|名称)\*\*[：:]\s*$/gm,
    '<div style="font-weight:600;margin:14px 0 6px;color:#1f1f1f">$1</div>',
  );
  s = s.replace(
    /^-\s+\*\*(分类|分级|典型组合|约束|要求|名称)\*\*[：:]\s*/gm,
    '<div style="font-weight:600;margin:14px 0 6px;color:#1f1f1f">$1</div>',
  );

  return s;
}

export const ENVIRONMENT_HTML_TABLE_STYLES = `
.environment-html-field .html-table-wrapper table,
.environment-html-field .mixed-content-html table {
  border-collapse: collapse;
  width: 100%;
  margin: 8px 0;
  font-size: 13px;
}
.environment-html-field .html-table-wrapper th,
.environment-html-field .html-table-wrapper td,
.environment-html-field .mixed-content-html th,
.environment-html-field .mixed-content-html td {
  border: 1px solid #d9d9d9;
  padding: 8px 10px;
  vertical-align: top;
  word-break: break-word;
}
.environment-html-field .html-table-wrapper th,
.environment-html-field .mixed-content-html th {
  background: #fafafa;
  font-weight: 600;
}
.environment-html-field img {
  max-width: 100%;
  height: auto;
  border-radius: 4px;
}
.znwd-env-source-panel .html-table-wrapper table,
.znwd-env-source-panel .mixed-content-html table {
  border-collapse: collapse;
  width: 100%;
  margin: 8px 0;
  font-size: 13px;
}
.znwd-env-source-panel .html-table-wrapper th,
.znwd-env-source-panel .html-table-wrapper td,
.znwd-env-source-panel .mixed-content-html th,
.znwd-env-source-panel .mixed-content-html td {
  border: 1px solid #d9d9d9;
  padding: 8px 10px;
  vertical-align: top;
  word-break: break-word;
}
.znwd-env-source-panel .html-table-wrapper th,
.znwd-env-source-panel .mixed-content-html th {
  background: #fafafa;
  font-weight: 600;
}
.znwd-env-source-panel img {
  max-width: 100%;
  height: auto;
}
`;

export default function EnvironmentHtmlField({
  value,
  compact = false,
  className = "",
}: {
  value: string;
  compact?: boolean;
  className?: string;
}) {
  const prepared = useMemo(() => normalizeEnvironmentFieldRaw(value), [value]);
  if (!prepared || prepared === "—") {
    return <span>—</span>;
  }

  const rich = isEnvironmentHtmlValue(value);

  if (!rich) {
    return (
      <span style={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }} className={className}>
        {prepared}
      </span>
    );
  }

  const content = prepareEnvironmentHtml(prepared);

  return (
    <div
      className={`environment-html-field${className ? ` ${className}` : ""}`}
      style={{
        fontSize: 13,
        lineHeight: 1.55,
        maxHeight: compact ? 160 : undefined,
        overflow: compact ? "auto" : undefined,
        minWidth: compact ? 120 : undefined,
      }}
    >
      <style>{ENVIRONMENT_HTML_TABLE_STYLES}</style>
      {renderEnvironmentFieldContent(content, { enableImageDownload: true })}
    </div>
  );
}
