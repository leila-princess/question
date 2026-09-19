'use client';

import { useEffect, useRef } from 'react';
import type { DocRenderer, DocRendererProps } from 'react-doc-viewer';
import { arrayBufferFileLoader } from 'react-doc-viewer/build/utils/fileLoaders';
import { renderDocxPreview } from '@/lib/renderDocxPreview';

function DocxLocalRendererComponent({ mainState }: DocRendererProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const hostRef = useRef<HTMLDivElement>(null);
  const doc = mainState.currentDocument;

  useEffect(() => {
    const host = hostRef.current;
    if (!host || !doc) return undefined;

    let cancelled = false;
    const fileData = doc.fileData;

    (async () => {
      try {
        let buf: ArrayBuffer | null = null;
        if (fileData instanceof ArrayBuffer) {
          buf = fileData;
        } else if (doc.uri) {
          const res = await fetch(doc.uri);
          if (!res.ok) throw new Error(`HTTP ${res.status}`);
          buf = await res.arrayBuffer();
        }
        if (!buf || cancelled) return;

        const sig = new Uint8Array(buf.slice(0, 4));
        if (!(sig[0] === 0x50 && sig[1] === 0x4b)) {
          throw new Error('非 docx 格式（.doc 请下载查看）');
        }

        await renderDocxPreview(buf, host);
      } catch (e) {
        if (!cancelled) {
          host.innerHTML = `<p style="padding:16px;color:#ff4d4f">Word 预览失败：${
            e instanceof Error ? e.message : '未知错误'
          }</p>`;
        }
      }
    })();

    return () => {
      cancelled = true;
      host.innerHTML = '';
    };
  }, [doc?.uri, doc?.fileData]);

  return (
    <div
      ref={scrollRef}
      id="wjgl-docx-local-renderer"
      style={{
        width: '100%',
        height: '100%',
        minHeight: 0,
        overflow: 'auto',
        WebkitOverflowScrolling: 'touch',
        background: '#e8e8e8',
      }}
    >
      <div
        ref={hostRef}
        className="wjgl-local-docx-host"
        style={{ minHeight: 'min-content', padding: '12px 0' }}
      />
    </div>
  );
}

const DocxLocalRenderer = DocxLocalRendererComponent as DocRenderer;

DocxLocalRenderer.fileTypes = [
  'docx',
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
];
DocxLocalRenderer.weight = 20;
DocxLocalRenderer.fileLoader = arrayBufferFileLoader;

export default DocxLocalRenderer;
