'use client';

import React, { useEffect } from 'react';
import { Form, Input, Modal } from 'antd';
import MathAwareTextArea from '@/components/MathAwareTextArea';

export type ParsedQuickEditKind = 'equipment_type' | 'equipment' | 'test_project' | 'indicator';

const TITLE_MAP: Record<ParsedQuickEditKind, string> = {
  equipment_type: '编辑设备类型',
  equipment: '编辑设备',
  test_project: '编辑测试项目',
  indicator: '编辑指标',
};

export interface ParsedQuickEditValues {
  name: string;
  description?: string;
  unit?: string;
  standard_value?: string;
}

interface ParsedNodeQuickEditModalProps {
  open: boolean;
  kind: ParsedQuickEditKind;
  record: Record<string, unknown> | null;
  onCancel: () => void;
  onSave: (values: ParsedQuickEditValues) => void;
}

export default function ParsedNodeQuickEditModal({
  open,
  kind,
  record,
  onCancel,
  onSave,
}: ParsedNodeQuickEditModalProps) {
  const [form] = Form.useForm<ParsedQuickEditValues>();

  useEffect(() => {
    if (!open || !record) return;
    form.setFieldsValue({
      name: String(record.name ?? ''),
      description: String(record.description ?? ''),
      unit: String(record.unit ?? ''),
      standard_value: String(record.standard_value ?? ''),
    });
  }, [open, record, form]);

  const handleOk = async () => {
    const values = await form.validateFields();
    onSave(values);
  };

  return (
    <Modal
      title={TITLE_MAP[kind]}
      open={open}
      onCancel={onCancel}
      onOk={handleOk}
      destroyOnHidden
      width={kind === 'indicator' ? 560 : 480}
    >
      <Form form={form} layout="vertical" style={{ marginTop: 8 }}>
        <Form.Item label="名称" name="name" rules={[{ required: true, message: '请输入名称' }]}>
          <Input />
        </Form.Item>
        {kind === 'indicator' ? (
          <>
            <Form.Item label="单位" name="unit">
              <Input />
            </Form.Item>
            <Form.Item label="标准值" name="standard_value">
              <Input />
            </Form.Item>
          </>
        ) : null}
        {kind !== 'equipment_type' ? (
          <Form.Item label="描述" name="description">
            <MathAwareTextArea rows={3} autoSize={{ minRows: 2, maxRows: 8 }} />
          </Form.Item>
        ) : null}
      </Form>
    </Modal>
  );
}
