'use client';

import React, { useState } from 'react';
import {
  Card, Button, Upload, Form, Input, Select, Space, message,
  Tag, Divider, Typography
} from 'antd';
import { UploadOutlined, CloudOutlined, FileTextOutlined } from '@ant-design/icons';
import StepUploadModal from '@/components/StepUploadModal';

const { Title, Text } = Typography;
const { TextArea } = Input;

// 文件类型选项
const CATEGORY_OPTIONS = [
  { value: '国家标准', label: '国家标准' },
  { value: '国家军用标准', label: '国家军用标准' },
  { value: '航天标准', label: '航天标准' },
  { value: '航空标准', label: '航空标准' },
  { value: '船舶标准', label: '船舶标准' },
  { value: '汽车行业', label: '汽车行业' },
  { value: '计量技术规范', label: '计量技术规范' },
  { value: '计量检定规程', label: '计量检定规程' },
  { value: '技术文件', label: '技术文件' },
  { value: '其他', label: '其他' }
];

export default function StepUploadDemoPage() {
  const [form] = Form.useForm();
  const [file, setFile] = useState<File | null>(null);
  const [uploadModalOpen, setUploadModalOpen] = useState(false);
  const [uploadData, setUploadData] = useState({
    category: '其他',
    label: '',
    title: '',
    abstract: '',
    keywords: [],
    ics: '',
    ccs: ''
  });
  const [userId] = useState('user_001');

  const handleFileChange = (info: any) => {
    if (info.file.status === 'done' || info.file.status === 'uploading') {
      setFile(info.file.originFileObj);
    }
  };

  const handleSubmit = async () => {
    try {
      const values = await form.validateFields();
      
      if (!file) {
        message.error('请选择要上传的文件');
        return;
      }

      setUploadData(values);
      setUploadModalOpen(true);
    } catch (error) {
      console.error('Form validation failed:', error);
    }
  };

  const handleUploadSuccess = (fileInfo: any) => {
    message.success('文件上传成功！');
    console.log('Upload success:', fileInfo);
    
    // 重置表单
    form.resetFields();
    setFile(null);
    setUploadModalOpen(false);
  };

  return (
    <div style={{ padding: 24, background: '#f5f5f5', minHeight: '100vh' }}>
      <Card>
        <Title level={4}>分步骤文件上传演示</Title>
        <Text type="secondary">演示新的分步骤上传流程，避免超时问题</Text>
        
        <Divider />

        <Form
          form={form}
          layout="vertical"
          initialValues={{
            category: '其他'
          }}
        >
          <Form.Item
            label="选择文件"
            required
            tooltip="支持 PDF、Word、图片等格式"
          >
            <Upload
              beforeUpload={() => false}
              onChange={handleFileChange}
              maxCount={1}
              accept=".pdf,.doc,.docx,.jpg,.jpeg,.png"
            >
              <Button icon={<UploadOutlined />}>
                选择文件
              </Button>
            </Upload>
            {file && (
              <Tag color="blue" style={{ marginTop: 8 }}>
                <FileTextOutlined /> {file.name}
              </Tag>
            )}
          </Form.Item>

          <Form.Item
            label="文件类型"
            name="category"
            rules={[{ required: true }]}
          >
            <Select
              options={CATEGORY_OPTIONS}
              placeholder="请选择文件类型"
            />
          </Form.Item>

          <Form.Item
            label="文件标题"
            name="title"
            rules={[{ required: true, message: '请输入文件标题' }]}
          >
            <Input placeholder="请输入文件标题" />
          </Form.Item>

          <Form.Item
            label="文件标签"
            name="label"
          >
            <Input placeholder="可选，用于分类" />
          </Form.Item>

          <Form.Item
            label="文件摘要"
            name="abstract"
          >
            <TextArea rows={3} placeholder="可选，输入文件摘要" />
          </Form.Item>

          <Form.Item
            label="关键词"
            name="keywords"
          >
            <Select
              mode="tags"
              placeholder="输入关键词，按回车确认"
              style={{ width: '100%' }}
            />
          </Form.Item>

          <Form.Item
            label="ICS分类号"
            name="ics"
          >
            <Input placeholder="可选，如：01.120" />
          </Form.Item>

          <Form.Item
            label="CCS分类号"
            name="ccs"
          >
            <Input placeholder="可选，如：A00" />
          </Form.Item>

          <Form.Item>
            <Space>
              <Button
                type="primary"
                icon={<CloudOutlined />}
                onClick={handleSubmit}
                disabled={!file}
              >
                开始上传
              </Button>
              <Button onClick={() => { form.resetFields(); setFile(null); }}>
                重置
              </Button>
            </Space>
          </Form.Item>
        </Form>

        <Divider />

        <Card type="inner" title="分步骤上传说明">
          <Space direction="vertical" style={{ width: '100%' }}>
            <Text>
              <strong>步骤1：文件数据库</strong> - 保存到MinIO并创建Neo4j基础节点，提取文档元数据（页数、图片、表格等）
            </Text>
            <Text>
              <strong>步骤2：向量数据库</strong> - 同步到Chroma，进行文本分块和向量化，支持语义检索
            </Text>
            <Text>
              <strong>步骤3：图数据库</strong> - 创建Neo4j关系和完整属性，支持知识图谱查询
            </Text>
          </Space>
        </Card>
      </Card>

      {/* 分步骤上传Modal */}
      <StepUploadModal
        open={uploadModalOpen}
        onClose={() => setUploadModalOpen(false)}
        file={file}
        uploadData={uploadData}
        userId={userId}
        onSuccess={handleUploadSuccess}
      />
    </div>
  );
}
