'use client';

import PiViewPortOfficeViewer from '@/components/PiViewPortOfficeViewer';

type Props = {
  fileUrl: string;
  fileName: string;
  reloadKey?: number;
  onReady?: () => void;
  onError?: (err: unknown) => void;
};

/** @deprecated 请使用 PiViewPortOfficeViewer；保留别名兼容旧引用 */
export default function PiViewPortWordViewer(props: Props) {
  return <PiViewPortOfficeViewer {...props} variant="word" />;
}
