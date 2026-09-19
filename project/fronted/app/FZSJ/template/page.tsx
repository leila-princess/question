'use client';

import React, { useState, useEffect, useCallback } from 'react';
import { 
  Card, Layout, Typography, Button, Space, Tabs, Input, 
  Select, Tag, message, Spin, Empty, Tooltip, Badge, Alert,
  List, Divider, Switch, Popover, Form, Modal
} from 'antd';
import { 
  SaveOutlined, UndoOutlined, RedoOutlined, PlayCircleOutlined,
  CodeOutlined, EyeOutlined, CheckCircleOutlined, ExclamationCircleOutlined,
  PlusOutlined, DeleteOutlined, CopyOutlined, DownloadOutlined,
  UploadOutlined, FileTextOutlined, SettingOutlined, HistoryOutlined,
  QuestionCircleOutlined, BulbOutlined
} from '@ant-design/icons';
import { useRouter } from 'next/navigation';

const { Title, Text, Paragraph } = Typography;
const { TextArea } = Input;
const { Sider, Content } = Layout;

// 模板变量类型
interface TemplateVariable {
  name: string;
  type: 'text' | 'multiline' | 'number' | 'select' | 'multiselect' | 'date' | 'boolean';
  required: boolean;
  label: string;
  placeholder?: string;
  default?: any;
  description?: string;
  options?: string[];
}

// 模板章节
interface TemplateSection {
  id: string;
  title: string;
  required: boolean;
  can_edit_title: boolean;
  can_delete: boolean;
  has_retrieval_mark: boolean;
  depends_on?: string[];
}

// 模板元数据
interface TemplateMetadata {
  id: string;
  name: string;
  description: string;
  version: string;
  category: string;
  created_by: string;
  created_at: string;
  updated_at: string;
  is_system: boolean;
  is_default: boolean;
  status: 'draft' | 'active' | 'archived';
}

// 完整模板
interface UserTemplate {
  metadata: TemplateMetadata;
  variables: TemplateVariable[];
  sections: TemplateSection[];
  content: string;
}

// 校验结果
interface ValidationResult {
  valid: boolean;
  score: number;
  errors: Array<{field: string; message: string; level: string}>;
  warnings: Array<{field: string; message: string; level: string}>;
}

// API 函数
const fetchTemplateList = async (userId: string) => {
  const res = await fetch(`/api/templates/list?user_id=${userId}`);
  return res.json();
};

const fetchTemplateDetail = async (templateId: string) => {
  const res = await fetch(`/api/templates/detail/${templateId}`);
  return res.json();
};

const createTemplate = async (data: any) => {
  const res = await fetch('/api/templates/create', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data)
  });
  return res.json();
};

const updateTemplate = async (templateId: string, data: any) => {
  const res = await fetch(`/api/templates/update/${templateId}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data)
  });
  return res.json();
};

const validateTemplate = async (data: any) => {
  const res = await fetch('/api/templates/validate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data)
  });
  return res.json();
};

// 默认模板内容
const DEFAULT_TEMPLATE = `---
name: 通用环境试验方案模板
description: 适用于各类设备的环境及边界性能试验方案
version: "1.0"
category: 环境试验
variables:
  - name: equipment_name
    type: text
    required: true
    label: 设备名称
  - name: environment_desc
    type: multiline
    required: true
    label: 环境描述
---

## 1. 范围
### 1.1 标识
- **文档标识**：\`{{equipment_name}}\`

### 1.2 试验目的
考核\`{{equipment_name}}\`在\`{{environment_desc}}\`下的环境适应性和边界性能。
**【检索】** 无。

---

## 2. 试验依据
**【检索】** 无。

---

## 3. 试验环境与设备
**【检索】** 设备能力要求、试验条件参数。
`;

export default function TemplateEditorPage() {
  const router = useRouter();
  const [userId, setUserId] = useState('user_001');
  const [templateList, setTemplateList] = useState<any[]>([]);
  const [currentTemplate, setCurrentTemplate] = useState<UserTemplate | null>(null);
  const [editorContent, setEditorContent] = useState(DEFAULT_TEMPLATE);
  const [activeTab, setActiveTab] = useState('edit');
  const [validationResult, setValidationResult] = useState<ValidationResult | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [selectedSection, setSelectedSection] = useState<string | null>(null);
  const [showVariableModal, setShowVariableModal] = useState(false);
  const [editingVariable, setEditingVariable] = useState<TemplateVariable | null>(null);

  // 加载模板列表
  useEffect(() => {
    loadTemplateList();
  }, [userId]);

  const loadTemplateList = async () => {
    setIsLoading(true);
    try {
      const result = await fetchTemplateList(userId);
      if (result.success) {
        setTemplateList(result.templates);
      }
    } catch (e) {
      message.error('加载模板列表失败');
    } finally {
      setIsLoading(false);
    }
  };

  // 创建新模板
  const handleCreateTemplate = async () => {
    try {
      const result = await createTemplate({
        user_id: userId,
        name: '新模板',
        content: DEFAULT_TEMPLATE
      });
      
      if (result.success) {
        message.success('模板创建成功');
        loadTemplateList();
        loadTemplateDetail(result.template_id);
      } else {
        message.error(result.error || '创建失败');
      }
    } catch (e) {
      message.error('创建模板失败');
    }
  };

  // 加载模板详情
  const loadTemplateDetail = async (templateId: string) => {
    setIsLoading(true);
    try {
      const result = await fetchTemplateDetail(templateId);
      if (result.success) {
        setCurrentTemplate(result.template);
        setEditorContent(result.template.content);
        validateCurrentTemplate(result.template);
      }
    } catch (e) {
      message.error('加载模板详情失败');
    } finally {
      setIsLoading(false);
    }
  };

  // 校验当前模板
  const validateCurrentTemplate = async (template: UserTemplate) => {
    try {
      const result = await validateTemplate(template);
      if (result.success) {
        setValidationResult(result.validation);
      }
    } catch (e) {
      console.error('Validation failed:', e);
    }
  };

  // 保存模板
  const handleSave = async () => {
    if (!currentTemplate) return;
    
    setIsSaving(true);
    try {
      const updatedTemplate = {
        ...currentTemplate,
        content: editorContent,
        user_id: userId
      };
      
      const result = await updateTemplate(currentTemplate.metadata.id, updatedTemplate);
      
      if (result.success) {
        message.success('保存成功');
        setValidationResult(result.validation);
      } else {
        message.error(result.error || '保存失败');
      }
    } catch (e) {
      message.error('保存模板失败');
    } finally {
      setIsSaving(false);
    }
  };

  // 解析章节
  const parseSections = (content: string): TemplateSection[] => {
    const sections: TemplateSection[] = [];
    const lines = content.split('\n');
    
    lines.forEach((line, index) => {
      const match = line.match(/^(#{2,4})\s+(.+)$/);
      if (match) {
        const level = match[1].length;
        const title = match[2].trim();
        sections.push({
          id: `section_${index}`,
          title,
          required: level === 2 && !! title.match(/^\d+\./),
          can_edit_title: true,
          can_delete: level > 2,
          has_retrieval_mark: false
        });
      }
    });
    
    return sections;
  };

  // 渲染校验状态
  const renderValidationStatus = () => {
    if (!validationResult) return null;
    
    const { valid, score, errors, warnings } = validationResult;
    
    return (
      <Card size="small" style={{ marginBottom: 16 }}>
        <Space direction="vertical" style={{ width: '100%' }}>
          <Space>
            <Text strong>校验结果：</Text>
            {valid ? (
              <Tag color="success" icon={<CheckCircleOutlined />}>通过</Tag>
            ) : (
              <Tag color="error" icon={<ExclamationCircleOutlined />}>未通过</Tag>
            )}
            <Text type="secondary">得分: {score}/100</Text>
          </Space>
          
          {errors.length > 0 && (
            <Alert
              type="error"
              showIcon
              message={`${errors.length} 个错误`}
              description={
                <List
                  size="small"
                  dataSource={errors}
                  renderItem={item => (
                    <List.Item style={{ padding: '4px 0', border: 'none' }}>
                      <Text type="danger">• {item.message}</Text>
                    </List.Item>
                  )}
                />
              }
            />
          )}
          
          {warnings.length > 0 && (
            <Alert
              type="warning"
              showIcon
              message={`${warnings.length} 个警告`}
              description={
                <List
                  size="small"
                  dataSource={warnings}
                  renderItem={item => (
                    <List.Item style={{ padding: '4px 0', border: 'none' }}>
                      <Text type="warning">• {item.message}</Text>
                    </List.Item>
                  )}
                />
              }
            />
          )}
        </Space>
      </Card>
    );
  };

  // 渲染编辑器
  const renderEditor = () => (
    <div style={{ display: 'flex', height: 'calc(100vh - 200px)' }}>
      {/* 左侧：章节导航 */}
      <div style={{ width: 240, borderRight: '1px solid #e8e8e8', padding: 16, overflow: 'auto' }}>
        <Title level={5} style={{ marginBottom: 16 }}>
          <FileTextOutlined /> 章节结构
        </Title>
        
        <List
          size="small"
          dataSource={parseSections(editorContent)}
          renderItem={section => (
            <List.Item
              style={{ 
                cursor: 'pointer',
                backgroundColor: selectedSection === section.id ? '#e6f7ff' : 'transparent',
                padding: '8px 12px',
                borderRadius: 4
              }}
              onClick={() => setSelectedSection(section.id)}
            >
              <Space direction="vertical" size={0} style={{ width: '100%' }}>
                <Text 
                  strong={section.required}
                  type={section.required ? undefined : 'secondary'}
                >
                  {section.title}
                </Text>
                {section.has_retrieval_mark && (
                  <Tag color="blue">检索</Tag>
                )}
              </Space>
            </List.Item>
          )}
        />
      </div>
      
      {/* 中间：编辑器 */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column' }}>
        <div style={{ padding: '8px 16px', borderBottom: '1px solid #e8e8e8' }}>
          <Space>
            <Tooltip title="保存 (Ctrl+S)">
              <Button 
                type="primary" 
                icon={<SaveOutlined />}
                onClick={handleSave}
                loading={isSaving}
              >
                保存
              </Button>
            </Tooltip>
            <Tooltip title="校验">
              <Button 
                icon={<CheckCircleOutlined />}
                onClick={() => currentTemplate && validateCurrentTemplate(currentTemplate)}
              >
                校验
              </Button>
            </Tooltip>
            <Tooltip title="添加变量">
              <Button 
                icon={<PlusOutlined />}
                onClick={() => setShowVariableModal(true)}
              >
                变量
              </Button>
            </Tooltip>
          </Space>
        </div>
        
        <div style={{ flex: 1, padding: 16 }}>
          {renderValidationStatus()}
          
          <TextArea
            value={editorContent}
            onChange={(e) => setEditorContent(e.target.value)}
            style={{ 
              height: '100%', 
              fontFamily: 'monospace',
              fontSize: 14,
              lineHeight: 1.6
            }}
            placeholder="在此编辑模板内容..."
          />
        </div>
      </div>
      
      {/* 右侧：变量和工具 */}
      <div style={{ width: 280, borderLeft: '1px solid #e8e8e8', padding: 16 }}>
        <Tabs defaultActiveKey="variables">
          <Tabs.TabPane tab="变量" key="variables">
            <Space direction="vertical" style={{ width: '100%' }}>
              <Button 
                type="dashed" 
                block 
                icon={<PlusOutlined />}
                onClick={() => setShowVariableModal(true)}
              >
                添加变量
              </Button>
              
              <Divider style={{ margin: '12px 0' }} />
              
              <List
                size="small"
                dataSource={currentTemplate?.variables || []}
                renderItem={variable => (
                  <List.Item
                    actions={[
                      <Button 
                        key="edit" 
                        type="text" 
                        size="small"
                        onClick={() => {
                          setEditingVariable(variable);
                          setShowVariableModal(true);
                        }}
                      >
                        编辑
                      </Button>
                    ]}
                  >
                    <div>
                      <Text strong>{variable.label}</Text>
                      <br />
                      <Text type="secondary" style={{ fontSize: 12 }}>
                        {`{{${variable.name}}}`} · {variable.type}
                      </Text>
                    </div>
                  </List.Item>
                )}
              />
            </Space>
          </Tabs.TabPane>
          
          <Tabs.TabPane tab="占位符" key="placeholders">
            <Space direction="vertical" style={{ width: '100%' }}>
              <Text type="secondary">点击插入占位符</Text>
              {(currentTemplate?.variables || []).map(v => (
                <Button 
                  key={v.name}
                  block
                  size="small"
                  onClick={() => {
                    const insertText = `{{${v.name}}}`;
                    setEditorContent(prev => prev + insertText);
                  }}
                >
                  {v.label} ({`{{${v.name}}}`})
                </Button>
              ))}
            </Space>
          </Tabs.TabPane>
        </Tabs>
      </div>
    </div>
  );

  // 渲染变量编辑弹窗
  const renderVariableModal = () => (
    <Modal
      title={editingVariable ? '编辑变量' : '添加变量'}
      open={showVariableModal}
      onCancel={() => {
        setShowVariableModal(false);
        setEditingVariable(null);
      }}
      footer={null}
    >
      <Form
        layout="vertical"
        initialValues={editingVariable || { type: 'text', required: true }}
        onFinish={(values) => {
          const newVariable: TemplateVariable = {
            name: values.name,
            type: values.type,
            required: values.required,
            label: values.label,
            placeholder: values.placeholder,
            default: values.default,
            description: values.description
          };
          
          if (currentTemplate) {
            const updatedVariables = editingVariable
              ? currentTemplate.variables.map(v => v.name === editingVariable.name ? newVariable : v)
              : [...currentTemplate.variables, newVariable];
            
            setCurrentTemplate({
              ...currentTemplate,
              variables: updatedVariables
            });
          }
          
          setShowVariableModal(false);
          setEditingVariable(null);
          message.success(editingVariable ? '变量已更新' : '变量已添加');
        }}
      >
        <Form.Item
          label="变量名"
          name="name"
          rules={[{ required: true, message: '请输入变量名' }]}
        >
          <Input placeholder="如：equipment_name" />
        </Form.Item>
        
        <Form.Item
          label="显示标签"
          name="label"
          rules={[{ required: true, message: '请输入显示标签' }]}
        >
          <Input placeholder="如：设备名称" />
        </Form.Item>
        
        <Form.Item
          label="变量类型"
          name="type"
          rules={[{ required: true }]}
        >
          <Select>
            <Select.Option value="text">单行文本</Select.Option>
            <Select.Option value="multiline">多行文本</Select.Option>
            <Select.Option value="number">数字</Select.Option>
            <Select.Option value="select">单选</Select.Option>
            <Select.Option value="multiselect">多选</Select.Option>
            <Select.Option value="date">日期</Select.Option>
            <Select.Option value="boolean">布尔值</Select.Option>
          </Select>
        </Form.Item>
        
        <Form.Item
          label="是否必填"
          name="required"
          valuePropName="checked"
        >
          <Switch />
        </Form.Item>
        
        <Form.Item
          label="占位提示"
          name="placeholder"
        >
          <Input placeholder="请输入占位提示" />
        </Form.Item>
        
        <Form.Item
          label="变量说明"
          name="description"
        >
          <Input.TextArea rows={2} placeholder="说明这个变量的用途" />
        </Form.Item>
        
        <Form.Item>
          <Space>
            <Button type="primary" htmlType="submit">
              保存
            </Button>
            <Button onClick={() => {
              setShowVariableModal(false);
              setEditingVariable(null);
            }}>
              取消
            </Button>
          </Space>
        </Form.Item>
      </Form>
    </Modal>
  );

  return (
    <div style={{ padding: 24, background: '#f5f5f5', minHeight: '100vh' }}>
      <Card>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 24 }}>
          <Space>
            <Title level={4} style={{ margin: 0 }}>模板编辑器</Title>
            {currentTemplate && (
              <Badge 
                status={validationResult?.valid ? 'success' : 'error'}
                text={validationResult?.valid ? '已通过校验' : '未通过校验'}
              />
            )}
          </Space>
          
          <Space>
            <Select
              style={{ width: 200 }}
              placeholder="选择模板"
              value={currentTemplate?.metadata.id}
              onChange={loadTemplateDetail}
              loading={isLoading}
            >
              {templateList.map(tpl => (
                <Select.Option key={tpl.id} value={tpl.id}>
                  {tpl.name} {tpl.is_default && <Tag color="blue" >默认</Tag>}
                </Select.Option>
              ))}
            </Select>
            
            <Button type="primary" icon={<PlusOutlined />} onClick={handleCreateTemplate}>
              新建模板
            </Button>
          </Space>
        </div>
        
        {currentTemplate ? (
          renderEditor()
        ) : (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description="请选择一个模板或创建新模板"
          >
            <Button type="primary" onClick={handleCreateTemplate}>
              创建新模板
            </Button>
          </Empty>
        )}
      </Card>
      
      {renderVariableModal()}
    </div>
  );
}
