// components/DynamicTemplateForm.tsx
import React from 'react';
import { Form, Input, Select, InputNumber, Checkbox, Space, Typography, Tag } from 'antd';
import { TemplateStructure } from '@/utils/template-parser';

const { Title, Text } = Typography;
const { Option } = Select;

interface DynamicTemplateFormProps {
  template: TemplateStructure;
  form: any;
}

const DynamicTemplateForm: React.FC<DynamicTemplateFormProps> = ({ template, form }) => {
  const renderFormItem = (section: any) => {
    const { id, title, type, required, options, placeholder, description, defaultValue } = section;

    const rules = [
      { required, message: `${title}是必填项` }
    ];

    switch (type) {
      case 'text':
        return (
          <Form.Item
            key={id}
            label={
              <div>
                <Text strong>{title}</Text>
                {description && (
                  <div style={{ fontSize: 12, color: '#666', fontWeight: 'normal' }}>
                    {description}
                  </div>
                )}
              </div>
            }
            name={['templateConfig', id]}
            rules={rules}
            initialValue={defaultValue}
          >
            <Input.TextArea 
              placeholder={placeholder} 
              rows={3}
              style={{ width: '100%' }}
            />
          </Form.Item>
        );

      case 'number':
        return (
          <Form.Item
            key={id}
            label={
              <div>
                <Text strong>{title}</Text>
                {description && (
                  <div style={{ fontSize: 12, color: '#666', fontWeight: 'normal' }}>
                    {description}
                  </div>
                )}
              </div>
            }
            name={['templateConfig', id]}
            rules={rules}
            initialValue={defaultValue}
          >
            <InputNumber 
              placeholder={placeholder} 
              style={{ width: '100%' }}
              addonAfter={title.includes('长度') || title.includes('直径') ? 'mm' : 
                         title.includes('面积') ? 'mm²' : 
                         title.includes('速率') ? 'mm/min' : ''}
            />
          </Form.Item>
        );

      case 'select':
        return (
          <Form.Item
            key={id}
            label={
              <div>
                <Text strong>{title}</Text>
                {description && (
                  <div style={{ fontSize: 12, color: '#666', fontWeight: 'normal' }}>
                    {description}
                  </div>
                )}
              </div>
            }
            name={['templateConfig', id]}
            rules={rules}
            initialValue={defaultValue || (options && options[0])}
          >
            <Select placeholder={placeholder || `请选择${title}`}>
              {options?.map((option: string) => (
                <Option key={option} value={option}>{option}</Option>
              ))}
            </Select>
          </Form.Item>
        );

      case 'checkbox':
        return (
          <Form.Item
            key={id}
            label={
              <div>
                <Text strong>{title}</Text>
                {description && (
                  <div style={{ fontSize: 12, color: '#666', fontWeight: 'normal' }}>
                    {description}
                  </div>
                )}
              </div>
            }
            name={['templateConfig', id]}
            valuePropName="checked"
          >
            <Checkbox.Group style={{ width: '100%' }}>
              <Space direction="vertical" style={{ width: '100%' }}>
                {options?.map((option: string) => (
                  <Checkbox key={option} value={option}>
                    {option}
                  </Checkbox>
                ))}
              </Space>
            </Checkbox.Group>
          </Form.Item>
        );

      default:
        return null;
    }
  };

  return (
    <div style={{ marginTop: 24 }}>
      <div style={{ marginBottom: 24 }}>
        <Title level={5} style={{ color: '#667eea' }}>
          模板配置: {template.name}
        </Title>
        <Text type="secondary" style={{ fontSize: 13 }}>
          {template.description}
        </Text>
        <div style={{ marginTop: 8 }}>
          <Tag color="blue">{template.category}</Tag>
          <Text type="secondary" style={{ fontSize: 12, marginLeft: 8 }}>
            共 {template.sections.length} 个配置项
          </Text>
        </div>
      </div>

      <div style={{ 
        background: '#f8f9fa', 
        padding: 24, 
        borderRadius: 12,
        marginBottom: 24 
      }}>
        {template.sections.map((section: any) => renderFormItem(section))}
      </div>
    </div>
  );
};

export default DynamicTemplateForm;