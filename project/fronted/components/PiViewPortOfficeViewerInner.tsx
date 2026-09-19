'use client';

import 'pi-view-port/style.css';
import { useEffect, useRef } from 'react';
import { createApp, h, type App } from 'vue';
import {
  ensureOfficeFileName,
  PI_VIEWPORT_OFFICE_SUPPORTED_TYPES,
  type PiViewPortOfficeVariant,
} from '@/lib/piViewPortOffice';

type Props = {
  fileUrl: string;
  fileName: string;
  variant?: PiViewPortOfficeVariant;
  onReady?: () => void;
  onError?: (err: unknown) => void;
};

/**
 * Office 原格式预览：pi-view-port（OnlyOffice WASM）
 * officeConfig.editable=false → OnlyOffice view 模式（只读浏览，缩放/查找等可用）
 * 依赖 scripts/patch-pi-view-port.mjs 修复 pi-view-port 未设置 mode:'view' 的问题
 */
export default function PiViewPortOfficeViewerInner({
  fileUrl,
  fileName,
  variant = 'auto',
  onReady,
  onError,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const vueAppRef = useRef<App | null>(null);
  const onReadyRef = useRef(onReady);
  const onErrorRef = useRef(onError);

  onReadyRef.current = onReady;
  onErrorRef.current = onError;

  useEffect(() => {
    const el = containerRef.current;
    if (!el || !fileUrl) return undefined;

    let cancelled = false;

    (async () => {
      try {
        const { FileViewer } = await import('pi-view-port');

        if (cancelled) return;

        vueAppRef.current?.unmount();
        vueAppRef.current = null;
        el.innerHTML = '';

        const app = createApp({
          render() {
            return h(FileViewer, {
              fileUrl,
              fileName: ensureOfficeFileName(fileName, variant),
              theme: 'light',
              useDefaultStyles: true,
              supportedTypes: PI_VIEWPORT_OFFICE_SUPPORTED_TYPES,
              officeConfig: { editable: false },
              onLoadComplete: () => {
                if (!cancelled) onReadyRef.current?.();
              },
              onLoadError: (err: unknown) => {
                console.error('[pi-view-port] load-error', err);
                if (!cancelled) onErrorRef.current?.(err);
              },
              onParseError: (err: unknown) => {
                console.error('[pi-view-port] parse-error', err);
                if (!cancelled) onErrorRef.current?.(err);
              },
            });
          },
        });

        vueAppRef.current = app;
        app.mount(el);
      } catch (e) {
        console.error('[pi-view-port] mount failed', e);
        if (!cancelled) onError?.(e);
      }
    })();

    return () => {
      cancelled = true;
      vueAppRef.current?.unmount();
      vueAppRef.current = null;
    };
  }, [fileUrl, fileName, variant]);

  return (
    <div
      ref={containerRef}
      className="pi-viewport-office-mount onlyoffice-container"
      style={{
        width: '100%',
        height: '100%',
        minHeight: 360,
        flex: 1,
        position: 'relative',
        overflow: 'hidden',
        isolation: 'isolate',
      }}
    />
  );
}
