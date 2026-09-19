'use client';

import { useEffect } from 'react';
import DocViewer from 'react-doc-viewer';
import DocxLocalRenderer from '@/components/docxLocalRenderer';

type Props = {
  fileUrl: string;
  fileName?: string;
  onReady?: () => void;
};

export default function WjglWordDocViewerInner({ fileUrl, fileName = 'document.docx', onReady }: Props) {
  useEffect(() => {
    onReady?.();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <DocViewer
      documents={[
        {
          uri: fileUrl,
          fileType: 'docx',
        },
      ]}
      pluginRenderers={[DocxLocalRenderer]}
      config={{
        header: {
          disableHeader: true,
          disableFileName: true,
        },
      }}
      theme={{ disableThemeScrollbar: true }}
      style={{ height: '100%', width: '100%' }}
    />
  );
}
