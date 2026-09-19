'use client';

import { useEffect, useRef } from 'react';
import { renderDocxPreview } from '@/lib/renderDocxPreview';

type Props = {
  /** 同源文件地址，如 /api/wjgl/object-file?object_name=... */
  fileUrl: string;
  reloadKey?: number;
  onLoaded?: () => void;
  onFailed?: (err: unknown) => void;
};

/**
 * Word .docx 本地预览（docx-preview，多页可滚动）
 */
export default function LocalDocxPreview({ fileUrl, reloadKey = 0, onLoaded, onFailed }: Props) {
  const hostRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    const host = hostRef.current;
    if (!host || !fileUrl) return undefined;

    (async () => {
      try {
        host.innerHTML = '';
        const res = await fetch(fileUrl);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const blob = await res.blob();
        const buf = await blob.arrayBuffer();
        if (buf.byteLength < 4) throw new Error('文件过小或为空');
        const sig = new Uint8Array(buf.slice(0, 4));
        const isZip = sig[0] === 0x50 && sig[1] === 0x4b;
        if (!isZip) {
          const head = new TextDecoder('utf-8', { fatal: false }).decode(buf.slice(0, 240)).trimStart();
          if (head.startsWith('<') || head.startsWith('<!') || head.startsWith('{')) {
            throw new Error('接口返回了网页或 JSON，而不是 Word 文件（请检查登录或代理）。');
          }
          throw new Error('仅支持 .docx；.doc 请下载查看');
        }
        if (cancelled || !hostRef.current) return;

        try {
          await renderDocxPreview(buf, hostRef.current);
          if (!cancelled) onLoaded?.();
          return;
        } catch (primaryErr) {
          console.warn('docx-preview failed, try mammoth fallback', primaryErr);
        }

        if (cancelled || !hostRef.current) return;
        hostRef.current.innerHTML = '';
        const mammoth = await import('mammoth');
        const { value: html } = await mammoth.convertToHtml({ arrayBuffer: buf });
        if (cancelled || !hostRef.current) return;
        const wrap = document.createElement('div');
        wrap.className = 'wjgl-mammoth-fallback';
        wrap.style.cssText = 'font-size:14px;line-height:1.6;color:#333;word-break:break-word;';
        wrap.innerHTML = html;
        hostRef.current.appendChild(wrap);
        if (!cancelled) onLoaded?.();
      } catch (e) {
        if (!cancelled) onFailed?.(e);
      }
    })();

    return () => {
      cancelled = true;
      if (hostRef.current) hostRef.current.innerHTML = '';
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fileUrl, reloadKey]);

  return (
    <div
      className="wjgl-docx-scroll"
      style={{
        flex: 1,
        minHeight: 0,
        height: '100%',
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
