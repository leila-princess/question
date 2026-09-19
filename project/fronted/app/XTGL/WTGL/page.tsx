"use client";
import React, { useState, useEffect } from 'react';
import { Button, Card, Form, Input, Table, Tag, Modal, message, Space, Popconfirm } from 'antd';
import { PlusOutlined, EditOutlined, DeleteOutlined, MessageOutlined } from '@ant-design/icons';

const { TextArea } = Input;

interface Problem {
  id: number;
  title: string;
  content: string;
  user_id: string;
  user_name: string;
  created_at: string;
  replied: boolean;
  reply_content: string;
  reply_user_id: string;
  reply_user_name: string;
  reply_at: string;
}

export default function WTGLPage() {
  const [loading, setLoading] = useState(false);
  const [problems, setProblems] = useState<Problem[]>([]);
  const [modalVisible, setModalVisible] = useState(false);
  const [modalType, setModalType] = useState<'add' | 'edit'>('add');
  const [replyModalVisible, setReplyModalVisible] = useState(false);
  const [selectedProblem, setSelectedProblem] = useState<Problem | null>(null);
  const [form] = Form.useForm();
  const [replyForm] = Form.useForm();

  const userId = typeof window !== 'undefined' ? localStorage.getItem('userId') || '' : '';
  const userName = typeof window !== 'undefined' ? localStorage.getItem('userName') || '' : '';

  const fetchProblems = async () => {
    try {
      setLoading(true);
      const res = await fetch('/api/xtgl/wtgl/problems');
      if (res.ok) {
        const result = await res.json();
        if (result.success) {
          setProblems(result.data || []);
        }
      }
    } catch (error) {
      message.error('获取问题列表失败');
    } finally {
      setLoading(false);
    }
  };

  const handleFormSubmit = async (values: any) => {
    try {
      const payload = {
        title: values.title,
        content: values.content,
        user_id: userId,
        user_name: userName,
      };

      let res;
      if (modalType === 'add') {
        res = await fetch('/api/xtgl/wtgl/problems', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
      } else {
        res = await fetch(`/api/xtgl/wtgl/problems/${selectedProblem?.id}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
      }

      if (res.ok) {
        const result = await res.json();
        if (result.success) {
          message.success(modalType === 'add' ? '问题创建成功' : '问题更新成功');
          fetchProblems();
          setModalVisible(false);
          form.resetFields();
        } else {
          message.error(result.error || '操作失败');
        }
      }
    } catch (error) {
      message.error('操作失败');
    }
  };

  const handleReplySubmit = async (values: any) => {
    try {
      const payload = {
        reply_content: values.reply_content,
        reply_user_id: userId,
        reply_user_name: userName,
      };

      const res = await fetch(`/api/xtgl/wtgl/problems/${selectedProblem?.id}/reply`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });

      if (res.ok) {
        const result = await res.json();
        if (result.success) {
          message.success('回复成功');
          fetchProblems();
          setReplyModalVisible(false);
          replyForm.resetFields();
        } else {
          message.error(result.error || '回复失败');
        }
      }
    } catch (error) {
      message.error('回复失败');
    }
  };

  const handleDelete = async (id: number) => {
    try {
      const res = await fetch(`/api/xtgl/wtgl/problems/${id}`, { method: 'DELETE' });
      if (res.ok) {
        const result = await res.json();
        if (result.success) {
          message.success('删除成功');
          fetchProblems();
        } else {
          message.error(result.error || '删除失败');
        }
      }
    } catch (error) {
      message.error('删除失败');
    }
  };

  useEffect(() => {
    fetchProblems();
  }, []);

  const columns = [
    {
      title: '问题标题',
      dataIndex: 'title',
      key: 'title',
      width: 200,
      ellipsis: true,
    },
    {
      title: '问题内容',
      dataIndex: 'content',
      key: 'content',
      width: 200,
      ellipsis: true,
    },
    {
      title: '是否回答',
      dataIndex: 'replied',
      key: 'replied',
      width: 100,
      render: (replied: boolean) => (
        <Tag color={replied ? 'green' : 'orange'}>
          {replied ? '已回答' : '未回答'}
        </Tag>
      ),
    },
    {
      title: '回答内容',
      dataIndex: 'reply_content',
      key: 'reply_content',
      width: 200,
      ellipsis: true,
      render: (text: string) => text || '-',
    },
    {
      title: '提问人',
      dataIndex: 'user_name',
      key: 'user_name',
      width: 100,
    },
    {
      title: '提问时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 160,
      render: (text: string) => text ? new Date(text).toLocaleString('zh-CN') : '-',
    },
    {
      title: '回复人',
      dataIndex: 'reply_user_name',
      key: 'reply_user_name',
      width: 100,
      render: (text: string) => text || '-',
    },
    {
      title: '回复时间',
      dataIndex: 'reply_at',
      key: 'reply_at',
      width: 160,
      render: (text: string) => text ? new Date(text).toLocaleString('zh-CN') : '-',
    },
    {
      title: '操作',
      key: 'action',
      width: 200,
      fixed: 'right' as const,
      render: (_: any, record: Problem) => (
        <Space size="small">
          <Button
            type="link"
            icon={<EditOutlined />}
            onClick={() => {
              setSelectedProblem(record);
              setModalType('edit');
              form.setFieldsValue({
                title: record.title,
                content: record.content,
              });
              setModalVisible(true);
            }}
          >
            修改
          </Button>
          <Button
            type="link"
            icon={<MessageOutlined />}
            onClick={() => {
              setSelectedProblem(record);
              replyForm.resetFields();
              setReplyModalVisible(true);
            }}
          >
            回复
          </Button>
          <Popconfirm
            title="确定要删除这个问题吗？"
            onConfirm={() => handleDelete(record.id)}
            okText="确定"
            cancelText="取消"
          >
            <Button type="link" danger icon={<DeleteOutlined />}>
              删除
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <div
      style={{
        height: '100%',
        minHeight: 0,
        overflow: 'hidden',
        boxSizing: 'border-box',
        padding: 24,
        display: 'flex',
        flexDirection: 'column',
      }}
    >
      <Card
        style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}
        styles={{ body: { flex: 1, minHeight: 0, overflow: 'auto' } }}
      >
        <div style={{ marginBottom: 16, display: 'flex', justifyContent: 'space-between' }}>
          <div style={{ fontSize: 18, fontWeight: 600 }}>问题管理</div>
          <Button
            type="primary"
            icon={<PlusOutlined />}
            onClick={() => {
              setSelectedProblem(null);
              setModalType('add');
              form.resetFields();
              setModalVisible(true);
            }}
          >
            新增问题
          </Button>
        </div>
        <Table
          columns={columns}
          dataSource={problems}
          rowKey="id"
          loading={loading}
          scroll={{ x: 1400 }}
          pagination={{ pageSize: 10 }}
        />
      </Card>

      <Modal
        title={modalType === 'add' ? '新增问题' : '修改问题'}
        open={modalVisible}
        onCancel={() => {
          setModalVisible(false);
          form.resetFields();
        }}
        footer={null}
        width={600}
        destroyOnHidden
      >
        <Form
          form={form}
          layout="vertical"
          onFinish={handleFormSubmit}
          style={{ marginTop: 24 }}
        >
          <Form.Item
            label="问题标题"
            name="title"
            rules={[{ required: true, message: '请输入问题标题' }]}
          >
            <Input placeholder="请输入问题标题" />
          </Form.Item>
          <Form.Item
            label="问题内容"
            name="content"
            rules={[{ required: true, message: '请输入问题内容' }]}
          >
            <TextArea rows={4} placeholder="请输入问题内容" />
          </Form.Item>
          <Form.Item style={{ marginBottom: 0, textAlign: 'right' }}>
            <Space>
              <Button onClick={() => {
                setModalVisible(false);
                form.resetFields();
              }}>
                取消
              </Button>
              <Button type="primary" htmlType="submit">
                {modalType === 'add' ? '新增' : '保存'}
              </Button>
            </Space>
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="回复问题"
        open={replyModalVisible}
        onCancel={() => {
          setReplyModalVisible(false);
          replyForm.resetFields();
        }}
        footer={null}
        width={600}
        destroyOnHidden
      >
        <Form
          form={replyForm}
          layout="vertical"
          onFinish={handleReplySubmit}
          style={{ marginTop: 24 }}
        >
          <Form.Item
            label="回复内容"
            name="reply_content"
            rules={[{ required: true, message: '请输入回复内容' }]}
          >
            <TextArea rows={4} placeholder="请输入回复内容" />
          </Form.Item>
          <Form.Item style={{ marginBottom: 0, textAlign: 'right' }}>
            <Space>
              <Button onClick={() => {
                setReplyModalVisible(false);
                replyForm.resetFields();
              }}>
                取消
              </Button>
              <Button type="primary" htmlType="submit">
                回复
              </Button>
            </Space>
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
