'use client';

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Input, Select, Spin } from 'antd';

export interface EntityOption {
  id: string;
  name: string;
  nodeType: string;
  labelZh?: string;
}

interface EntitySearchSelectProps {
  nodeType: string;
  value?: string;
  onChange?: (name: string, entity?: EntityOption) => void;
  placeholder?: string;
  freeText?: boolean;
  disabled?: boolean;
  style?: React.CSSProperties;
}

function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

export default function EntitySearchSelect({
  nodeType,
  value,
  onChange,
  placeholder,
  freeText = false,
  disabled,
  style,
}: EntitySearchSelectProps) {
  const [options, setOptions] = useState<EntityOption[]>([]);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState('');
  const debounced = useDebounced(search, 300);

  const fetchOptions = useCallback(
    async (q: string) => {
      if (!nodeType && !freeText) return;
      setLoading(true);
      try {
        if (freeText) {
          setOptions([]);
          return;
        }
        const params = new URLSearchParams({
          type: nodeType,
          q: q || '',
          limit: '30',
        });
        const res = await fetch(`/api/znwd6/entities/search?${params}`);
        const data = await res.json();
        const items: EntityOption[] = Array.isArray(data?.items) ? data.items : [];
        setOptions(items);
      } catch {
        setOptions([]);
      } finally {
        setLoading(false);
      }
    },
    [nodeType, freeText]
  );

  useEffect(() => {
    void fetchOptions(debounced);
  }, [debounced, fetchOptions]);

  const selectOptions = useMemo(
    () =>
      options.map((o) => ({
        value: o.name,
        label: o.labelZh ? `${o.name}（${o.labelZh}）` : o.name,
        entity: o,
      })),
    [options]
  );

  if (freeText) {
    return (
      <Input
        style={{ width: '100%', ...style }}
        placeholder={placeholder || '输入关键词'}
        value={value || ''}
        disabled={disabled}
        onChange={(e) => onChange?.(e.target.value)}
        allowClear
      />
    );
  }

  return (
    <Select
      showSearch
      filterOption={false}
      style={{ width: '100%', ...style }}
      placeholder={placeholder || `搜索${nodeType}…`}
      value={value || undefined}
      disabled={disabled}
      loading={loading}
      options={selectOptions}
      onSearch={setSearch}
      onFocus={() => void fetchOptions(search)}
      notFoundContent={loading ? <Spin size="small" /> : '无匹配实体'}
      onChange={(name, opt) => {
        const entity = (opt as { entity?: EntityOption })?.entity;
        onChange?.(String(name), entity);
      }}
      allowClear
    />
  );
}
