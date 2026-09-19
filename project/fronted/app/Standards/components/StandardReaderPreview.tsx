'use client';

import { memo, useCallback, useState } from 'react';
import { Card, Spin, Button, message } from 'antd';
import {
  FilePdfOutlined, DownloadOutlined,
  FileImageOutlined, FileOutlined, FileExcelOutlined, FileWordOutlined,
} from '@ant-design/icons';
import WjglOfficePdfViewer from '@/components/WjglOfficePdfViewer';
import PiViewPortOfficeViewer from '@/components/PiViewPortOfficeViewer';
import { formatPreviewError } from '@/lib/formatPreviewError';

export type PreviewKind = 'pdf' | 'image' | 'office_word' | 'office_excel' | 'office_pdf' | 'other';

export interface StandardInfoState {
  id?: string;
  source_name?: string;
  title?: string;
  pdfUrl?: string;
  fileViewUrl?: string;
  preview_pdf_url?: string;
  preview_kind?: PreviewKind;
  object_name?: string;
  metadata?: Record<string, string>;
}

interface StandardReaderPreviewProps {
  standardInfo: StandardInfoState;
  objectName: string;
  displayName: string;
  downloadUrl: string;
  previewHostRef?: React.RefObject<HTMLDivElement | null>;
}

function StandardReaderPreview({
  standardInfo,
  objectName,
  displayName,
  downloadUrl,
  previewHostRef,
}: StandardReaderPreviewProps) {
  const [pdfLoading, setPdfLoading] = useState(true);
  const [pdfError, setPdfError] = useState(false);
  const [pdfKey, setPdfKey] = useState(0);

  const previewKind = standardInfo.preview_kind || 'pdf';
  const fileViewUrl = standardInfo.fileViewUrl || standardInfo.pdfUrl || '';
  const previewPdfUrl = standardInfo.preview_pdf_url || '';

  const handlePreviewReady = useCallback(() => setPdfLoading(false), []);

  const handleWordPreviewError = useCallback((err: unknown) => {
    setPdfLoading(false);
    setPdfError(true);
    const detail = formatPreviewError(err);
    const hint = detail.includes('404') || /onlyoffice|x2t|sdkjs/i.test(detail)
      ? '（可检查 public/packages/onlyoffice 是否存在，或执行 npm run copy-onlyoffice）'
      : '';
    message.error(`Word 预览失败：${detail}${hint}`);
  }, []);

  const handleExcelPreviewError = useCallback((err: unknown) => {
    setPdfLoading(false);
    setPdfError(true);
    const detail = formatPreviewError(err);
    const hint = detail.includes('404') || /onlyoffice|x2t|sdkjs/i.test(detail)
      ? '（可检查 public/packages/onlyoffice 是否存在，或执行 npm run copy-onlyoffice）'
      : '';
    message.error(`Excel 预览失败：${detail}${hint}`);
  }, []);

  const officePreviewFileName =
    (standardInfo.object_name || objectName || '').split('/').pop() ||
    `${displayName || 'document'}.${previewKind === 'office_excel' ? 'xlsx' : 'docx'}`;

  const leftCardTitle = () => {
    if (previewKind === 'pdf') return <span><FilePdfOutlined /> 标准文档 {displayName}</span>;
    if (previewKind === 'image') return <span><FileImageOutlined /> 图片预览 {displayName}</span>;
    if (previewKind === 'office_word') return <span><FileWordOutlined /> Word {displayName}</span>;
    if (previewKind === 'office_excel') return <span><FileExcelOutlined /> Excel {displayName}</span>;
    if (previewKind === 'office_pdf') return <span><FileOutlined /> PPT {displayName}</span>;
    return <span><FileOutlined /> 文件 {displayName}</span>;
  };

  const renderPreview = () => {
    if (pdfError) {
      return (
        <div style={{ textAlign: 'center', padding: 10 }}>
          <p style={{ color: '#ff4d4f', marginBottom: 16 }}>无法加载文档</p>
          <Button
            type="primary"
            onClick={() => {
              setPdfError(false);
              setPdfLoading(true);
              setPdfKey((k) => k + 1);
            }}
          >
            重新加载
          </Button>
        </div>
      );
    }
    const canPreview =
      previewKind === 'office_pdf' ? Boolean(previewPdfUrl) : Boolean(fileViewUrl);
    if (!canPreview) {
      return <div style={{ padding: 16, color: '#999' }}>无可预览地址</div>;
    }
    if (previewKind === 'pdf') {
      return (
        <>
          {pdfLoading && (
            <div style={{ position: 'absolute', top: '50%', left: '50%', transform: 'translate(-50%, -50%)', zIndex: 10, background: 'rgba(255,255,255,0.9)', padding: 20, borderRadius: 8 }}>
              <Spin size="large" tip="加载中..." />
            </div>
          )}
          <iframe
            key={pdfKey}
            src={fileViewUrl}
            style={{ width: '100%', height: '100%', border: 'none' }}
            onLoad={handlePreviewReady}
            onError={() => { setPdfLoading(false); setPdfError(true); }}
            title="文档预览"
          />
        </>
      );
    }
    if (previewKind === 'image') {
      return (
        <div style={{ flex: 1, minHeight: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', overflow: 'auto' }}>
          <img
            key={pdfKey}
            src={fileViewUrl}
            alt={displayName}
            style={{ maxWidth: '100%', maxHeight: '100%', objectFit: 'contain' }}
            onLoad={handlePreviewReady}
            onError={() => { setPdfLoading(false); setPdfError(true); }}
          />
        </div>
      );
    }
    if (previewKind === 'office_word' && fileViewUrl) {
      return (
        <div style={{ flex: 1, minHeight: 0, height: '100%', display: 'flex', flexDirection: 'column', position: 'relative' }}>
          {pdfLoading && (
            <div style={{ position: 'absolute', inset: 0, zIndex: 10, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'rgba(255,255,255,0.92)', borderRadius: 8 }}>
              <Spin size="large" tip="正在加载 Word（OnlyOffice 原格式预览）…" />
            </div>
          )}
          <PiViewPortOfficeViewer
            fileUrl={fileViewUrl}
            fileName={officePreviewFileName}
            variant="word"
            reloadKey={pdfKey}
            onReady={handlePreviewReady}
            onError={handleWordPreviewError}
          />
        </div>
      );
    }
    if (previewKind === 'office_excel' && fileViewUrl) {
      return (
        <div style={{ flex: 1, minHeight: 0, height: '100%', display: 'flex', flexDirection: 'column', position: 'relative' }}>
          {pdfLoading && (
            <div style={{ position: 'absolute', inset: 0, zIndex: 10, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'rgba(255,255,255,0.92)', borderRadius: 8 }}>
              <Spin size="large" tip="正在加载 Excel（OnlyOffice 原格式预览）…" />
            </div>
          )}
          <PiViewPortOfficeViewer
            fileUrl={fileViewUrl}
            fileName={officePreviewFileName}
            variant="excel"
            reloadKey={pdfKey}
            onReady={handlePreviewReady}
            onError={handleExcelPreviewError}
          />
        </div>
      );
    }
    if (previewKind === 'office_pdf' && previewPdfUrl) {
      return (
        <div style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column', position: 'relative' }}>
          {pdfLoading && (
            <div style={{ position: 'absolute', inset: 0, zIndex: 10, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'rgba(255,255,255,0.92)', borderRadius: 8 }}>
              <Spin size="large" tip="正在转换 PPT 为 PDF…" />
            </div>
          )}
          <WjglOfficePdfViewer
            previewPdfUrl={previewPdfUrl}
            reloadKey={pdfKey}
            onReady={handlePreviewReady}
          />
        </div>
      );
    }

    return (
      <div style={{ padding: 24, textAlign: 'center' }}>
        <FileExcelOutlined style={{ fontSize: 48, color: '#1890ff', marginBottom: 16 }} />
        <p>该格式无法在浏览器内直接预览。</p>
        <Button type="primary" icon={<DownloadOutlined />} href={fileViewUrl} target="_blank" rel="noreferrer">
          下载查看
        </Button>
      </div>
    );
  };

  return (
    <Card
      title={leftCardTitle()}
      style={{
        flex: 1,
        minHeight: 0,
        display: 'flex',
        flexDirection: 'column',
        overflow: 'hidden',
        borderRadius: 12,
      }}
      styles={{
        body: {
          flex: 1,
          minHeight: 0,
          display: 'flex',
          flexDirection: 'column',
          overflow: 'hidden',
          padding: 12,
        },
      }}
      extra={
        downloadUrl ? (
          <Button icon={<DownloadOutlined />} onClick={() => window.open(downloadUrl)}>
            下载
          </Button>
        ) : null
      }
    >
      <div
        ref={previewHostRef}
        style={{
          flex: 1,
          minHeight: 0,
          position: 'relative',
          display: 'flex',
          flexDirection: 'column',
          overflow: 'hidden',
          isolation: 'isolate',
        }}
      >
        {renderPreview()}
      </div>
    </Card>
  );
}

export default memo(StandardReaderPreview);
