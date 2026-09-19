'use client';

import dynamic from 'next/dynamic';
import type { WangEditorProps } from './WangEditorInner';

const WangEditor = dynamic<WangEditorProps>(() => import('./WangEditorInner'), {
  ssr: false,
  loading: () => (
    <div
      style={{
        minHeight: 320,
        border: '1px solid #d9d9d9',
        borderRadius: 6,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        color: '#999',
        background: '#fff',
      }}
    >
      加载编辑器…
    </div>
  ),
});

export default WangEditor;
export type { WangEditorProps };
