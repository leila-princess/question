"use client";



import { useState, useEffect, useRef, Suspense } from "react";

import { useSearchParams } from "next/navigation";

import { Row, Col, Spin, message } from "antd";

import StandardReaderPreview, { type StandardInfoState, type PreviewKind } from "./components/StandardReaderPreview";

import StandardReaderChatPanel from "./components/StandardReaderChatPanel";



/** Next.js useSearchParams 偶发首帧为空，用 location 兜底避免误判「未提供参数」 */

function readQueryParamFromWindow(key: string): string {

  if (typeof window === "undefined") return "";

  try {

    return (new URLSearchParams(window.location.search).get(key) || "").trim();

  } catch {

    return "";

  }

}



function StandardDetailWithParams() {

  const searchParams = useSearchParams();

  return <StandardDetailContent searchParams={searchParams} />;

}



function StandardDetailContent({ searchParams }: { searchParams: URLSearchParams }) {

  const sourceId =

    (searchParams.get("source_id") || "").trim() || readQueryParamFromWindow("source_id");

  const sourceName =

    (searchParams.get("source_name") || "").trim() || readQueryParamFromWindow("source_name");

  const objectName =

    (searchParams.get("object_name") || "").trim() || readQueryParamFromWindow("object_name");



  const wjglMode = Boolean(objectName);



  const [summary, setSummary] = useState<string>("加载中...");

  const [standardInfo, setStandardInfo] = useState<StandardInfoState | null>(null);

  const previewHostRef = useRef<HTMLDivElement>(null);



  useEffect(() => {

    if (!sourceId && !sourceName && !objectName) {

      message.error("未提供标准或文件信息");

      return;

    }



    const fetchInfo = async () => {

      try {

        if (wjglMode) {

          const res = await fetch(`/api/wjgl/object-meta?object_name=${encodeURIComponent(objectName)}`);

          const data = await res.json();

          if (data.success && data.data) {

            const d = data.data;

            const fileViewUrl = `/api/wjgl/object-file?object_name=${encodeURIComponent(d.object_name || objectName)}`;

            const rawAb = (d.abstract || "").trim();

            let decodedAb = rawAb;

            try {

              if (rawAb) decodedAb = decodeURIComponent(rawAb);

            } catch { /* keep raw */ }

            setSummary(decodedAb || "暂无摘要信息");

            const previewKind = (d.preview_kind || "other") as PreviewKind;

            const previewPdfUrl =

              (d.preview_pdf_url as string) ||

              (previewKind === "office_pdf"

                ? `/api/wjgl/preview-pdf?object_name=${encodeURIComponent(d.object_name || objectName)}`

                : "");

            setStandardInfo({

              id: d.source_id || "",

              source_name: d.title || d.object_name || objectName,

              title: d.std_code ? `${d.std_code}` : d.title,

              pdfUrl: fileViewUrl,

              fileViewUrl,

              preview_pdf_url: previewPdfUrl,

              preview_kind: previewKind,

              object_name: d.object_name || objectName,

              metadata: d.metadata,

            });

          } else {

            message.error(data.error || "获取文件信息失败");

          }

        } else {

          const params = sourceId

            ? `source_id=${encodeURIComponent(sourceId)}`

            : `source_name=${encodeURIComponent(sourceName)}`;

          const res = await fetch(`/api/standard/info?${params}`);

          const data = await res.json();

          if (data.success) {

            const d = data.data || {};

            const previewKind = (d.preview_kind as PreviewKind) || "pdf";

            const fileViewUrl =

              d.fileViewUrl ||

              (previewKind === "pdf" ? d.pdfUrl : undefined) ||

              (d.object_name

                ? `/api/wjgl/object-file?object_name=${encodeURIComponent(d.object_name)}`

                : undefined);

            setStandardInfo({

              ...d,

              preview_kind: previewKind,

              fileViewUrl: fileViewUrl || d.pdfUrl,

              preview_pdf_url: d.preview_pdf_url || d.previewPdfUrl,

            });

          } else {

            message.error(data.error || "获取标准信息失败");

          }

        }

      } catch (error) {

        console.error(error);

        message.error("获取信息失败");

      }

    };

    void fetchInfo();

  }, [sourceId, sourceName, objectName, wjglMode]);



  useEffect(() => {

    if (!standardInfo) return;



    if (wjglMode) {

      return;

    }



    const minioAbstract = standardInfo?.metadata?.["x-amz-meta-abstract"];

    if (minioAbstract) {

      try {

        const decoded = decodeURIComponent(minioAbstract);

        setSummary(decoded);

      } catch {

        setSummary(minioAbstract);

      }

      return;

    }



    if (!standardInfo?.source_name) {

      setSummary("暂无摘要信息");

      return;

    }



    const fetchSummary = async () => {

      try {

        const res = await fetch("/api/standard/summary", {

          method: "POST",

          headers: { "Content-Type": "application/json" },

          body: JSON.stringify({ source_id: standardInfo.id }),

        });

        const data = await res.json();

        if (data.success) {

          setSummary(data.data.summary);

        } else {

          setSummary("暂无摘要信息");

        }

      } catch {

        setSummary("暂无摘要信息");

      }

    };

    void fetchSummary();

  }, [standardInfo, wjglMode]);



  const displayName = standardInfo?.source_name || sourceName || sourceId || objectName;

  const fileViewUrl = standardInfo?.fileViewUrl || standardInfo?.pdfUrl || "";

  const chatSourceId = standardInfo?.id || sourceId || "";



  if (!sourceId && !sourceName && !objectName) {

    return (

      <div style={{ display: "flex", justifyContent: "center", alignItems: "center", height: "100%", minHeight: 0 }}>

        未提供标准或文件信息（请确认地址栏含 source_id / source_name / object_name 查询参数）

      </div>

    );

  }



  if (!standardInfo) {

    return (

      <div style={{ display: "flex", justifyContent: "center", alignItems: "center", height: "100%", minHeight: 0 }}>

        <Spin size="large" tip="加载中..." />

      </div>

    );

  }



  return (

    <div

      style={{

        height: "100%",

        minHeight: 0,

        boxSizing: "border-box",

        display: "flex",

        flexDirection: "column",

        overflow: "hidden",

        background: "linear-gradient(135deg, #f8fafc 0%, #e8f0fe 100%)",

      }}

    >

      <div

        style={{

          flex: 1,

          minHeight: 0,

          padding: 8,

          display: "flex",

          flexDirection: "column",

          overflow: "hidden",

        }}

      >

        <Row

          gutter={[16, 16]}

          wrap={false}

          className="standards-reader-layout"

          style={{ flex: 1, minHeight: 0, marginInline: 0, display: "flex" }}

        >

          <Col

            span={14}

            style={{

              display: "flex",

              flexDirection: "column",

              minHeight: 0,

              height: "100%",

              position: "relative",

              zIndex: 1,

              overflow: "hidden",

            }}

          >

            <StandardReaderPreview
              key={standardInfo.object_name || standardInfo.id || objectName}
              standardInfo={standardInfo}

              objectName={objectName}

              displayName={displayName}

              downloadUrl={fileViewUrl}

              previewHostRef={previewHostRef}

            />

          </Col>



          <Col

            span={10}

            className="standards-chat-panel"

            style={{

              display: "flex",

              flexDirection: "column",

              gap: 16,

              minHeight: 0,

              height: "100%",

              position: "relative",

              zIndex: 30,

            }}

          >

            <StandardReaderChatPanel

              displayName={displayName}

              summary={summary}

              wjglMode={wjglMode}

              standardInfo={standardInfo}

              chatSourceId={chatSourceId}

              previewHostRef={previewHostRef}

            />

          </Col>

        </Row>

      </div>

    </div>

  );

}



function StandardDetailPage() {

  return (

    <div style={{ height: "100%", minHeight: 0, overflow: "hidden" }}>

      <Suspense

        fallback={(

          <div

            style={{

              height: "100%",

              minHeight: 0,

              display: "flex",

              alignItems: "center",

              justifyContent: "center",

            }}

          >

            <Spin size="large" />

          </div>

        )}

      >

        <StandardDetailWithParams />

      </Suspense>

    </div>

  );

}



export default StandardDetailPage;



export const dynamic = "force-dynamic";


