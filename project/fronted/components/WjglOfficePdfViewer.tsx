'use client';

import dynamic from 'next/dynamic';
import { Spin } from 'antd';

function LoadingBox() {
  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', minHeight: 320 }}>
      <Spin size="large" tip="正在转换 PPT 并加载预览…" />
    </div>
  );
}

const OfficePdfViewer = dynamic(
  () => import('@/components/WjglOfficePdfViewerInner'),
  { ssr: false, loading: () => <LoadingBox /> },
);

type Props = {
  previewPdfUrl: string;
  reloadKey?: number;
  onReady?: () => void;
};

export default function WjglOfficePdfViewer({
  previewPdfUrl,
  reloadKey = 0,
  onReady,
}: Props) {
  if (!previewPdfUrl) return null;

  return (
    <div
      style={{
        flex: 1,
        minHeight: 0,
        height: '100%',
        width: '100%',
        overflow: 'hidden',
      }}
    >
      <OfficePdfViewer
        key={reloadKey}
        previewPdfUrl={previewPdfUrl}
        onReady={onReady}
      />
    </div>
  );
}
