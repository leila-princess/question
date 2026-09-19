"use client";

import React, { useMemo } from "react";
import MarkdownRenderer from "@/components/MarkdownRenderer";
import EnvironmentHtmlField, {
  ENVIRONMENT_HTML_TABLE_STYLES,
  isEnvironmentHtmlValue,
  prepareEnvironmentHtml,
} from "@/components/EnvironmentHtmlField";
import MixedContent from "@/components/ThreeColumnLayout/MixedContent";
import { normalizeEnvironmentFieldRaw, minioImageProxyUrl } from "@/components/ThreeColumnLayout/environmentMedia";

/** 环境五要素相关问题：分类/分级/组合/约束/要求 */
export function isEnvironmentPropertiesAnswer(raw: string): boolean {
  const s = (raw || "").trim();
  if (!s) return false;
  if (/环境/.test(s) && /(?:分类|分级|典型组合|约束|要求|属性如下)/.test(s)) return true;
  return isEnvironmentHtmlValue(s) && /(?:分类|分级|典型组合|约束|要求)/.test(s);
}

export default function EnvironmentAnswerBody({ content }: { content: string }) {
  const prepared = useMemo(() => normalizeEnvironmentFieldRaw(content), [content]);
  const hasHtml = isEnvironmentHtmlValue(prepared);

  if (!hasHtml) {
    return <MarkdownRenderer content={prepared} enableImageDownload />;
  }

  const html = prepareEnvironmentHtml(prepared);

  return (
    <div className="environment-answer-body environment-html-field" style={{ fontSize: 14, lineHeight: 1.65 }}>
      <style>{ENVIRONMENT_HTML_TABLE_STYLES}</style>
      <MixedContent content={html} enableImageDownload />
    </div>
  );
}

export { minioImageProxyUrl };
