'use client';

import { useEffect } from 'react';
import DocViewer, { PDFRenderer } from 'react-doc-viewer';

type Props = {
  previewPdfUrl: string;
  onReady?: () => void;
};

export default function WjglOfficePdfViewerInner({
  previewPdfUrl,
  onReady,
}: Props) {
  useEffect(() => {
    onReady?.();
    // 仅首次挂载通知父级结束 loading
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <DocViewer
      documents={[
        {
          uri: previewPdfUrl,
          fileType: 'pdf',
        },
      ]}
      pluginRenderers={[PDFRenderer]}
      config={{
        header: {
          disableHeader: true,
          disableFileName: true,
        },
      }}
      style={{ height: '100%', width: '100%' }}
    />
  );
}
