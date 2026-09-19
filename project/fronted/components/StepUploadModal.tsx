'use client';

import React, { useState, useEffect } from 'react';
import {
  Modal, Button, Space, Card, Descriptions, Tag, Image,
  Alert, Spin, Typography, Divider, List, Table, Result, Tooltip,
  Steps
} from 'antd';
import {
  CheckCircleOutlined, LoadingOutlined, FileTextOutlined,
  DatabaseOutlined, CloudOutlined, LinkOutlined, PictureOutlined,
  ClockCircleOutlined, CloseCircleOutlined, ExclamationCircleOutlined
} from '@ant-design/icons';
import { useRouter } from 'next/navigation';

const { Title, Text, Paragraph } = Typography;

// 上传步骤状态
interface StepStatus {
  status: 'wait' | 'process' | 'finish' | 'error';
  message?: string;
  data?: any;
  error?: string;
}

// 上传会话状态
interface UploadSession {
  sessionId: string;
  fileId: string;
  docId: string;
  step1: StepStatus;
  step2: StepStatus;
  step3: StepStatus;
  fileInfo?: any;
  chromaInfo?: any;
  neo4jInfo?: any;
}

// 组件属性
interface StepUploadModalProps {
  open: boolean;
  onClose: () => void;
  file: File | null;
  uploadData: {
    category: string;
    label: string;
    title: string;
    abstract: string;
    keywords: string[];
    ics: string;
    ccs: string;
    associations?: any[];
  };
  userId: string;
  onSuccess?: (fileInfo: any) => void;
}

export default function StepUploadModal({
  open,
  onClose,
  file,
  uploadData,
  userId,
  onSuccess
}: StepUploadModalProps) {
  const router = useRouter();
  const [currentStep, setCurrentStep] = useState(0);
  const [session, setSession] = useState<UploadSession | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [overallStatus, setOverallStatus] = useState<'processing' | 'success' | 'error'>('processing');

  // 开始上传流程
  useEffect(() => {
    if (open && file) {
      startUpload();
    }
  }, [open, file]);

  const startUpload = async () => {
    setIsLoading(true);
    setCurrentStep(0);
    setOverallStatus('processing');
    
    const newSession: UploadSession = {
      sessionId: '',
      fileId: '',
      docId: '',
      step1: { status: 'process', message: '正在保存文件...' },
      step2: { status: 'wait' },
      step3: { status: 'wait' }
    };
    
    setSession(newSession);
    
    try {
      // 步骤1：保存到文件数据库
      await executeStep1(newSession);
    } catch (error: any) {
      setOverallStatus('error');
      setSession(prev => prev ? {
        ...prev,
        step1: { ...prev.step1, status: 'error', error: error.message }
      } : null);
    } finally {
      setIsLoading(false);
    }
  };

  const executeStep1 = async (currentSession: UploadSession) => {
    const formData = new FormData();
    formData.append('file', file!);
    formData.append('category', uploadData.category);
    formData.append('label', uploadData.label);
    formData.append('title', uploadData.title);
    formData.append('abstract', uploadData.abstract);
    formData.append('keywords', JSON.stringify(uploadData.keywords));
    formData.append('ics', uploadData.ics);
    formData.append('ccs', uploadData.ccs);
    formData.append('user_id', userId);

    const response = await fetch('/api/fzsj/wjgl/upload-step1', {
      method: 'POST',
      body: formData
    });

    const result = await response.json();

    if (!result.success) {
      throw new Error(result.error || '步骤1失败');
    }

    const updatedSession: UploadSession = {
      ...currentSession,
      sessionId: result.session_id,
      fileId: result.file_id,
      docId: result.doc_id,
      step1: { status: 'finish', message: '文件已保存', data: result },
      step2: { status: 'process', message: '正在同步到向量数据库...' },
      fileInfo: result.file_info
    };

    setSession(updatedSession);
    setCurrentStep(1);

    // 自动开始步骤2
    await executeStep2(updatedSession);
  };

  const executeStep2 = async (currentSession: UploadSession) => {
    try {
      const response = await fetch(`/api/fzsj/wjgl/upload-step2/${currentSession.sessionId}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' }
      });

      const result = await response.json();

      if (!result.success) {
        throw new Error(result.error || '步骤2失败');
      }

      const updatedSession: UploadSession = {
        ...currentSession,
        step2: { status: 'finish', message: '向量数据库已同步', data: result },
        step3: { status: 'process', message: '正在保存到图数据库...' },
        chromaInfo: result.chroma_metadata
      };

      setSession(updatedSession);
      setCurrentStep(2);

      // 自动开始步骤3
      await executeStep3(updatedSession);
    } catch (error: any) {
      setSession(prev => prev ? {
        ...prev,
        step2: { ...prev.step2, status: 'error', error: error.message },
        step3: { status: 'wait' }
      } : null);
      setOverallStatus('error');
    }
  };

  const executeStep3 = async (currentSession: UploadSession) => {
    try {
      const response = await fetch(`/api/fzsj/wjgl/upload-step3/${currentSession.sessionId}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          associations: uploadData.associations || []
        })
      });

      const result = await response.json();

      if (!result.success) {
        throw new Error(result.error || '步骤3失败');
      }

      const finalSession: UploadSession = {
        ...currentSession,
        step3: { status: 'finish', message: '图数据库已更新', data: result },
        neo4jInfo: result.neo4j_node
      };

      setSession(finalSession);
      setCurrentStep(3);
      setOverallStatus('success');

      if (onSuccess) {
        onSuccess({
          fileId: finalSession.fileId,
          docId: finalSession.docId,
          fileInfo: finalSession.fileInfo,
          chromaInfo: finalSession.chromaInfo,
          neo4jInfo: finalSession.neo4jInfo
        });
      }
    } catch (error: any) {
      setSession(prev => prev ? {
        ...prev,
        step3: { ...prev.step3, status: 'error', error: error.message }
      } : null);
      setOverallStatus('error');
    }
  };

  const handleCancel = async () => {
    if (session?.sessionId) {
      try {
        await fetch(`/api/fzsj/wjgl/upload-cancel/${session.sessionId}`, {
          method: 'POST'
        });
      } catch (e) {
        console.error('Cancel upload failed:', e);
      }
    }
    onClose();
  };

  const handleRetry = () => {
    startUpload();
  };

  // 渲染步骤内容
  const renderStepContent = () => {
    if (!session) return null;

    return (
      <div style={{ marginTop: 24 }}>
        {currentStep === 0 && (
          <Card title="步骤1：文件数据库" loading={session.step1.status === 'process'}>
            {session.step1.status === 'finish' && session.fileInfo && (
              <Descriptions column={2} size="small">
                <Descriptions.Item label="文件ID">{session.fileId}</Descriptions.Item>
                <Descriptions.Item label="文档ID">{session.docId}</Descriptions.Item>
                <Descriptions.Item label="文件名">{session.fileInfo.filename}</Descriptions.Item>
                <Descriptions.Item label="文件类型">{session.fileInfo.file_type}</Descriptions.Item>
                <Descriptions.Item label="页数">{session.fileInfo.pages}</Descriptions.Item>
                <Descriptions.Item label="表格数">{session.fileInfo.tables}</Descriptions.Item>
                <Descriptions.Item label="图片数">{session.fileInfo.figures}</Descriptions.Item>
                <Descriptions.Item label="ICS">{session.fileInfo.ics || '-'}</Descriptions.Item>
                <Descriptions.Item label="CCS">{session.fileInfo.ccs || '-'}</Descriptions.Item>
              </Descriptions>
            )}
            
            {session.step1.data?.has_images && (
              <>
                <Divider>图片预览（前5张）</Divider>
                <Image.PreviewGroup>
                  <Space wrap>
                    {session.step1.data.image_preview_urls.map((url: string, idx: number) => (
                      <Image
                        key={idx}
                        src={url}
                        width={100}
                        height={100}
                        style={{ objectFit: 'cover' }}
                        alt={`图片${idx + 1}`}
                      />
                    ))}
                  </Space>
                </Image.PreviewGroup>
              </>
            )}

            {session.step1.error && (
              <Alert type="error" message={session.step1.error} showIcon style={{ marginTop: 16 }} />
            )}
          </Card>
        )}

        {currentStep === 1 && (
          <Card title="步骤2：向量数据库（Chroma）" loading={session.step2.status === 'process'}>
            {session.step2.status === 'finish' && session.chromaInfo && (
              <>
                <Descriptions column={2} size="small">
                  <Descriptions.Item label="Chroma ID">{session.chromaInfo.doc_id}</Descriptions.Item>
                  <Descriptions.Item label="分块数量">{session.chromaInfo.chunks}</Descriptions.Item>
                  <Descriptions.Item label="页数">{session.chromaInfo.pages}</Descriptions.Item>
                  <Descriptions.Item label="文本长度">{session.chromaInfo.text_length} 字符</Descriptions.Item>
                </Descriptions>
                
                {session.chromaInfo.chunk_ids && session.chromaInfo.chunk_ids.length > 0 && (
                  <>
                    <Divider>向量分块ID（前10个）</Divider>
                    <List
                      size="small"
                      bordered
                      dataSource={session.chromaInfo.chunk_ids}
                      renderItem={(id: string) => (
                        <List.Item>
                          <Text code copyable>{id}</Text>
                        </List.Item>
                      )}
                    />
                  </>
                )}
              </>
            )}

            {session.step2.error && (
              <Alert type="error" message={session.step2.error} showIcon style={{ marginTop: 16 }} />
            )}
          </Card>
        )}

        {currentStep === 2 && (
          <Card title="步骤3：图数据库（Neo4j）" loading={session.step3.status === 'process'}>
            {session.step3.status === 'finish' && session.neo4jInfo && (
              <Descriptions column={2} size="small">
                <Descriptions.Item label="节点ID">{session.neo4jInfo.id}</Descriptions.Item>
                <Descriptions.Item label="节点名称">{session.neo4jInfo.name}</Descriptions.Item>
                <Descriptions.Item label="文件类型">{session.neo4jInfo.type}</Descriptions.Item>
                <Descriptions.Item label="标签">{session.neo4jInfo.label}</Descriptions.Item>
                <Descriptions.Item label="状态">{session.neo4jInfo.status}</Descriptions.Item>
                <Descriptions.Item label="MinIO ID">{session.neo4jInfo.minio_id}</Descriptions.Item>
                <Descriptions.Item label="Chroma ID">{session.neo4jInfo.chroma_id || '-'}</Descriptions.Item>
                <Descriptions.Item label="Chroma分块">{session.neo4jInfo.chroma_chunks || 0}</Descriptions.Item>
                <Descriptions.Item label="包含图片">{session.neo4jInfo.has_images ? '是' : '否'}</Descriptions.Item>
                <Descriptions.Item label="文件格式">{session.neo4jInfo.file_type}</Descriptions.Item>
              </Descriptions>
            )}

            {session.step3.error && (
              <Alert type="error" message={session.step3.error} showIcon style={{ marginTop: 16 }} />
            )}
          </Card>
        )}

        {currentStep === 3 && overallStatus === 'success' && (
          <Result
            status="success"
            title="文件上传成功"
            subTitle="文件已成功保存到所有数据库"
            extra={[
              <Button type="primary" key="view" onClick={() => router.push('/ZSGL/WJGL')}>
                查看文件
              </Button>,
              <Button key="close" onClick={onClose}>
                关闭
              </Button>
            ]}
          />
        )}

        {overallStatus === 'error' && (
          <Result
            status="error"
            title="上传失败"
            subTitle="请检查错误信息并重试"
            extra={[
              <Button type="primary" key="retry" onClick={handleRetry}>
                重试
              </Button>,
              <Button key="close" onClick={handleCancel}>
                取消
              </Button>
            ]}
          />
        )}
      </div>
    );
  };

  return (
    <Modal
      title={
        <Space>
          <CloudOutlined />
          <span>分步骤文件上传</span>
        </Space>
      }
      open={open}
      onCancel={handleCancel}
      width={800}
      footer={
        overallStatus === 'success' ? null : (
          <Space>
            {overallStatus === 'error' && (
              <Button type="primary" onClick={handleRetry} icon={<CloudOutlined />}>
                重试
              </Button>
            )}
            <Button onClick={handleCancel} danger={overallStatus === 'processing'}>
              {overallStatus === 'processing' ? '取消上传' : '关闭'}
            </Button>
          </Space>
        )
      }
      maskClosable={false}
      closable={overallStatus !== 'processing'}
    >
      {/* @ts-ignore */}
      <Steps current={currentStep} direction="horizontal" size="small">
        {/* @ts-ignore */}
        <Steps.Step 
          title="文件数据库" 
          icon={session?.step1.status === 'process' ? <LoadingOutlined /> : session?.step1.status === 'finish' ? <CheckCircleOutlined /> : session?.step1.status === 'error' ? <CloseCircleOutlined /> : <FileTextOutlined />}
          status={session?.step1.status}
        />
        {/* @ts-ignore */}
        <Steps.Step 
          title="向量数据库" 
          icon={session?.step2.status === 'process' ? <LoadingOutlined /> : session?.step2.status === 'finish' ? <CheckCircleOutlined /> : session?.step2.status === 'error' ? <CloseCircleOutlined /> : <DatabaseOutlined />}
          status={session?.step2.status}
        />
        {/* @ts-ignore */}
        <Steps.Step 
          title="图数据库" 
          icon={session?.step3.status === 'process' ? <LoadingOutlined /> : session?.step3.status === 'finish' ? <CheckCircleOutlined /> : session?.step3.status === 'error' ? <CloseCircleOutlined /> : <LinkOutlined />}
          status={session?.step3.status}
        />
      </Steps>

      {renderStepContent()}

      {overallStatus === 'processing' && (
        <div style={{ marginTop: 24, textAlign: 'center' }}>
          <Spin tip="正在处理，请稍候..." />
        </div>
      )}
    </Modal>
  );
}
