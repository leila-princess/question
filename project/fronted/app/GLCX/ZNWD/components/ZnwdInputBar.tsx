'use client';

import React from 'react';
import { Sender } from '@ant-design/x';

export interface ZnwdInputBarProps {
  value: string;
  onChange: (v: string) => void;
  onSubmit: (msg: string) => void;
  loading?: boolean;
  onCancel?: () => void;
  placeholder?: string;
  maxWidth?: number;
}

export default function ZnwdInputBar({
  value,
  onChange,
  onSubmit,
  loading,
  onCancel,
  placeholder,
  maxWidth = 960,
}: ZnwdInputBarProps) {
  return (
    <div style={{ maxWidth, margin: '0 auto', width: '100%' }}>
      <Sender
        value={value}
        onChange={onChange}
        onSubmit={onSubmit}
        loading={loading}
        onCancel={onCancel}
        placeholder={placeholder}
        autoSize={{ minRows: 1, maxRows: 4 }}
      />
    </div>
  );
}
