"use client";

import React from "react";
import { Typography } from "antd";
import MixedContent from "./MixedContent";
import {
  rewriteEnvironmentMediaUrls,
  unescapeEnvironmentNewlines,
} from "./environmentMedia";

const { Text, Paragraph } = Typography;

/** 将纯文本/HTML 按 ThreeColumnLayout 环境五要素方式分段渲染 */
export function renderEnvironmentFieldContent(
  content: string,
  options?: { enableImageDownload?: boolean },
) {
  const prepared = rewriteEnvironmentMediaUrls(
    unescapeEnvironmentNewlines(content || ""),
  );
  if (!prepared) return <Text type="secondary">无数据</Text>;

  const mixedProps = { enableImageDownload: options?.enableImageDownload };

  const hasHtml = /<[a-z][\s\S]*>/i.test(prepared);
  const lines = prepared
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean);

  // 多行且每行是独立块（纯文本或完整 HTML 片段），分行展示
  if (hasHtml && lines.length > 1) {
    return (
      <div style={{ lineHeight: 1.8 }}>
        {lines.map((line, idx) => (
          <div key={idx} style={{ marginBottom: 8 }}>
            <MixedContent content={line} {...mixedProps} />
          </div>
        ))}
      </div>
    );
  }

  if (hasHtml) {
    return <MixedContent content={prepared} {...mixedProps} />;
  }

  if (lines.length === 0) return <Text type="secondary">无数据</Text>;
  if (lines.length === 1) return <MixedContent content={lines[0]} {...mixedProps} />;

  return (
    <div style={{ lineHeight: 1.8 }}>
      {lines.map((line, idx) => {
        const isNumbered =
          /^[①②③④⑤⑥⑦⑧⑨⑩\u2460-\u2473\u3251-\u325f(\（]?\s*[\d一二三四五六七八九十百千①②③④⑤⑥⑦⑧⑨⑩a-zA-Z]+[\.、\)））]/.test(
            line,
          );
        if (isNumbered) {
          return (
            <div key={idx} style={{ marginBottom: 8, paddingLeft: 8 }}>
              <MixedContent content={line} {...mixedProps} />
            </div>
          );
        }
        return (
          <Paragraph key={idx} style={{ marginBottom: 8 }}>
            <MixedContent content={line} {...mixedProps} />
          </Paragraph>
        );
      })}
    </div>
  );
}
