'use client';

import { Button, Dropdown } from 'antd';
import { DownloadOutlined, DownOutlined } from '@ant-design/icons';
import type { MenuProps } from 'antd';
import type { SchemeExportFormat } from '../lib/schemeKmMeta';

interface SchemeExportDropdownProps {
  onExport: (format: SchemeExportFormat) => void | Promise<void>;
  loading?: boolean;
  disabled?: boolean;
  size?: 'small' | 'middle' | 'large';
}

export default function SchemeExportDropdown({
  onExport,
  loading = false,
  disabled = false,
  size = 'middle',
}: SchemeExportDropdownProps) {
  const items: MenuProps['items'] = [
    { key: 'md', label: '导出 MD' },
    { key: 'pdf', label: '导出 PDF' },
    { key: 'word', label: '导出 Word' },
  ];

  return (
    <Dropdown
      menu={{
        items,
        onClick: ({ key }) => void onExport(key as SchemeExportFormat),
      }}
      disabled={disabled || loading}
    >
      <Button size={size} icon={<DownloadOutlined />} loading={loading}>
        导出 <DownOutlined />
      </Button>
    </Dropdown>
  );
}
