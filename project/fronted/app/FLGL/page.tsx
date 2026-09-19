import { Suspense } from 'react';
import { Spin } from 'antd';
import FLGLPage from './FLGLClient';

export const dynamic = 'force-dynamic';

export default function Page() {
  return (
    <Suspense
      fallback={
        <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', minHeight: '60vh' }}>
          <Spin size="large" tip="加载中..." />
        </div>
      }
    >
      <FLGLPage />
    </Suspense>
  );
}
