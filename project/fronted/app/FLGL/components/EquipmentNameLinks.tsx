'use client';

import React from 'react';
import { Button, Space } from 'antd';

interface EquipmentNameLinksProps {
  equipmentIds?: string[];
  equipmentNames?: string[];
  onLocate?: (equipmentId: string) => void;
  fallbackText?: string;
}

export default function EquipmentNameLinks({
  equipmentIds = [],
  equipmentNames = [],
  onLocate,
  fallbackText = '—',
}: EquipmentNameLinksProps) {
  const ids = (equipmentIds || []).map((id) => String(id || '').trim()).filter(Boolean);
  const names = (equipmentNames || []).map((n) => String(n || '').trim());

  if (!ids.length && !names.length) {
    return <span>{fallbackText}</span>;
  }

  const pairs: Array<{ id: string; name: string }> = [];
  if (ids.length) {
    ids.forEach((id, i) => {
      pairs.push({ id, name: names[i] || id });
    });
  } else {
    names.forEach((name) => pairs.push({ id: '', name }));
  }

  return (
    <Space size={0} wrap split={<span>、</span>}>
      {pairs.map((p, i) => {
        if (p.id && onLocate) {
          return (
            <Button
              key={`${p.id}-${i}`}
              type="link"
              size="small"
              style={{ padding: 0, height: 'auto' }}
              onClick={() => onLocate(p.id)}
            >
              {p.name}
            </Button>
          );
        }
        return <span key={`${p.name}-${i}`}>{p.name}</span>;
      })}
    </Space>
  );
}
