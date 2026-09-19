import React from 'react';
import { Tag } from 'antd';

/** 分类管理状态：库内统一存中文「启用」「禁用」 */
export const FLGL_STATUS_ENABLED = '启用';
export const FLGL_STATUS_DISABLED = '禁用';
export const DEFAULT_FLGL_STATUS = FLGL_STATUS_ENABLED;

export const FLGL_STATUS_OPTIONS = [
  { label: FLGL_STATUS_ENABLED, value: FLGL_STATUS_ENABLED },
  { label: FLGL_STATUS_DISABLED, value: FLGL_STATUS_DISABLED },
];

export function isStatusEnabled(val: unknown): boolean {
  const s = String(val ?? '').trim();
  if (!s) return false;
  const lower = s.toLowerCase();
  return s === FLGL_STATUS_ENABLED || lower === 'active';
}

export function isStatusDisabled(val: unknown): boolean {
  const s = String(val ?? '').trim();
  if (!s) return false;
  const lower = s.toLowerCase();
  return s === FLGL_STATUS_DISABLED || s === '停用' || lower === 'inactive';
}

/** 表格展示用中文标签 */
export function normalizeStatusLabel(val: unknown): string {
  if (val == null || String(val).trim() === '') return '—';
  if (isStatusEnabled(val)) return FLGL_STATUS_ENABLED;
  if (isStatusDisabled(val)) return FLGL_STATUS_DISABLED;
  return String(val).trim();
}

/** 表单/提交用中文值（兼容历史 active/inactive/停用） */
export function normalizeStatusValue(val: unknown): string {
  if (isStatusEnabled(val)) return FLGL_STATUS_ENABLED;
  if (isStatusDisabled(val)) return FLGL_STATUS_DISABLED;
  const s = String(val ?? '').trim();
  return s || DEFAULT_FLGL_STATUS;
}

export function StatusTag({ value }: { value: unknown }) {
  const label = normalizeStatusLabel(value);
  if (label === '—') return <Tag>—</Tag>;
  const color = isStatusEnabled(value) ? 'green' : isStatusDisabled(value) ? 'red' : 'default';
  return <Tag color={color}>{label}</Tag>;
}
