'use client';

import dynamic from 'next/dynamic';
import { Spin } from 'antd';
import type { PiViewPortOfficeVariant } from '@/lib/piViewPortOffice';

const Inner = dynamic(() => import('@/components/PiViewPortOfficeViewerInner'), {
  ssr: false,
  loading: () => (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        height: '100%',
        minHeight: 320,
      }}
    >
      <Spin size="large" tip="正在加载 OnlyOffice 预览引擎（首次较慢）…" />
    </div>
  ),
});

type Props = {
  fileUrl: string;
  fileName: string;
  variant?: PiViewPortOfficeVariant;
  reloadKey?: number;
  onReady?: () => void;
  onError?: (err: unknown) => void;
};

export default function PiViewPortOfficeViewer({
  fileUrl,
  fileName,
  variant = 'auto',
  reloadKey = 0,
  onReady,
  onError,
}: Props) {
  if (!fileUrl) return null;

  return (
    <div
      className="pi-viewport-office-root onlyoffice-container"
      style={{
        flex: 1,
        minHeight: 0,
        height: '100%',
        width: '100%',
        overflow: 'hidden',
        display: 'flex',
        flexDirection: 'column',
        position: 'relative',
        isolation: 'isolate',
      }}
    >
      <Inner
        key={reloadKey}
        fileUrl={fileUrl}
        fileName={fileName}
        variant={variant}
        onReady={onReady}
        onError={onError}
      />
    </div>
  );
}
