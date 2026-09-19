'use client';

import { useEffect, useRef, useState } from 'react';
import { Tabs } from 'antd';

type Props = {
  fileUrl: string;
  reloadKey?: number;
  onLoaded?: () => void;
  onFailed?: (err: unknown) => void;
};

/**
 * Excel 本地预览（xlsx / xls），不依赖 LibreOffice 或 Office Online。
 */
export default function LocalXlsxPreview({ fileUrl, reloadKey = 0, onLoaded, onFailed }: Props) {
  const hostRef = useRef<HTMLDivElement>(null);
  const [sheetNames, setSheetNames] = useState<string[]>([]);
  const [activeSheet, setActiveSheet] = useState('');
  const workbookRef = useRef<import('xlsx').WorkBook | null>(null);

  const renderSheet = async (name: string, wb: import('xlsx').WorkBook) => {
    const host = hostRef.current;
    if (!host) return;
    const sheet = wb.Sheets[name];
    if (!sheet) {
      host.innerHTML = '<p style="color:#999;padding:12px">工作表为空</p>';
      return;
    }
    const XLSX = await import('xlsx');
    host.innerHTML = XLSX.utils.sheet_to_html(sheet, { id: 'wjgl-xlsx-table', editable: false });
  };

  useEffect(() => {
    let cancelled = false;
    setSheetNames([]);
    setActiveSheet('');
    workbookRef.current = null;
    if (hostRef.current) hostRef.current.innerHTML = '';

    (async () => {
      try {
        const res = await fetch(fileUrl);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const buf = await res.arrayBuffer();
        if (buf.byteLength < 4) throw new Error('文件过小或为空');
        const XLSX = await import('xlsx');
        const wb = XLSX.read(buf, { type: 'array' });
        if (cancelled) return;
        workbookRef.current = wb;
        const names = wb.SheetNames.length ? wb.SheetNames : ['Sheet1'];
        setSheetNames(names);
        const first = names[0];
        setActiveSheet(first);
        await renderSheet(first, wb);
        if (!cancelled) onLoaded?.();
      } catch (e) {
        if (!cancelled) onFailed?.(e);
      }
    })();

    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fileUrl, reloadKey]);

  const onTabChange = (key: string) => {
    setActiveSheet(key);
    const wb = workbookRef.current;
    if (wb) void renderSheet(key, wb);
  };

  return (
    <div style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      {sheetNames.length > 1 && (
        <Tabs
          size="small"
          activeKey={activeSheet}
          onChange={onTabChange}
          items={sheetNames.map((n) => ({ key: n, label: n }))}
          style={{ flexShrink: 0, marginBottom: 8 }}
        />
      )}
      <div
        ref={hostRef}
        className="wjgl-local-xlsx-root"
        style={{
          flex: 1,
          minHeight: 0,
          overflow: 'auto',
          background: '#fff',
          padding: 8,
          border: '1px solid #f0f0f0',
          borderRadius: 8,
        }}
      />
    </div>
  );
}
