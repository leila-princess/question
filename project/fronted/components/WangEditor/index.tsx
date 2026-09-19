'use client';

import dynamic from 'next/dynamic';
import type { WangEditorProps } from './WangEditorClient';

const WangEditor = dynamic(() => import('./WangEditorClient'), {
  ssr: false,
  loading: () => (
    <div style={{ border: '1px solid #d9d9d9', borderRadius: 6, padding: 12, color: '#999' }}>
      编辑器加载中...
    </div>
  ),
});

export type { WangEditorProps };
export default WangEditor;
