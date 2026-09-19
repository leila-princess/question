'use client';

import dynamic from 'next/dynamic';
import { Spin } from 'antd';

function LoadingBox() {
  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', minHeight: 320 }}>
      <Spin size="large" tip="正在加载 Word 文档…" />
    </div>
  );
}

const WordViewer = dynamic(() => import('@/components/WjglWordDocViewerInner'), {
  ssr: false,
  loading: () => <LoadingBox />,
});

type Props = {
  fileUrl: string;
  fileName?: string;
  reloadKey?: number;
  onReady?: () => void;
};

export default function WjglWordDocViewer({ fileUrl, fileName, reloadKey = 0, onReady }: Props) {
  if (!fileUrl) return null;

  return (
    <div style={{ flex: 1, minHeight: 0, height: '100%', width: '100%', overflow: 'hidden' }}>
      <WordViewer key={reloadKey} fileUrl={fileUrl} fileName={fileName} onReady={onReady} />
    </div>
  );
}
