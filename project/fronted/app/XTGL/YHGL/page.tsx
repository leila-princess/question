"use client";
import React, { useState, useEffect, useMemo } from 'react';
import { Button, Card, Form, Input, Select, Table, Tag, Modal, message, Space, Avatar, Popconfirm } from 'antd';
import { UserOutlined, PlusOutlined, EditOutlined, DeleteOutlined, LockOutlined, UnlockOutlined, CheckCircleOutlined, CloseCircleOutlined } from '@ant-design/icons';
import { useTablePagination } from '@/utils/useTablePagination';
import { ROLE_OPTIONS, ROLE_KM_MANAGER, normalizeUserRole } from '@/utils/permissions';

const { Option } = Select;

interface User {
  id: string;
  username: string;
  name: string;
  email: string;
  phone: string;
  department: string;
  role: string;
  status: 'active' | 'disabled' | 'locked';
  lastLogin: string;
  createTime: string;
  /** 登录密码（仅新增/重置时使用，不会从服务端返回） */
  password?: string;
}

export default function YHGLPage() {
  const [loading, setLoading] = useState(false);
  const [users, setUsers] = useState<User[]>([]);
  const [modalVisible, setModalVisible] = useState(false);
  const [modalType, setModalType] = useState<'add' | 'edit'>('add');
  const [selectedUser, setSelectedUser] = useState<User | null>(null);
  const [searchKeyword, setSearchKeyword] = useState('');
  const [form] = Form.useForm();

  const fetchUsers = async () => {
    try {
      setLoading(true);
        const res = await fetch('/api/xtgl/yhgl/users');
      if (res.ok) {
        const result = await res.json();
        const mappedUsers = result.data.map((u: any) => ({
          id: u.id || u.user_id,
          username: u.user_id,
          name: u.name,
          role: u.role,
          status: u.status,
          email: u.email || '',
          phone: u.phone || '',
          department: u.department || '',
          lastLogin: u.lastLogin || '',
          createTime: u.created_at || '',
        }));
        setUsers(mappedUsers);
      }
    } catch (error) {
      message.error('获取用户列表失败');
    } finally {
      setLoading(false);
    }
  };

  const handleFormSubmit = async (values: any) => {
    try {
      if (modalType === 'add') {
        const res = await fetch('/api/xtgl/yhgl/users', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            user_id: values.username,
            name: values.name,
            role: values.role,
            status: 'active',
            department: values.department,
            password: values.password,
          }),
        });
        if (res.ok) {
          message.success('用户添加成功');
          fetchUsers();
        } else {
          message.error('添加失败');
        }
      } else {
        const res = await fetch(`/api/xtgl/yhgl/users/${selectedUser?.id}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            user_id: values.username,
            name: values.name,
            role: values.role,
            status: values.status || selectedUser?.status,
            department: values.department,
            ...(values.password ? { password: values.password } : {}),
          }),
        });
        if (res.ok) {
          message.success('用户信息更新成功');
          fetchUsers();
        } else {
          message.error('更新失败');
        }
      }
      setModalVisible(false);
      form.resetFields();
    } catch (error) {
      message.error('操作失败');
    }
  };

  const handleDeleteUser = async (id: string) => {
    try {
      const res = await fetch(`/api/xtgl/yhgl/users/${id}`, { method: 'DELETE' });
      if (res.ok) {
        message.success('用户删除成功');
        fetchUsers();
      } else {
        message.error('删除失败');
      }
    } catch (error) {
      message.error('操作失败');
    }
  };

  const handleLockUser = async (id: string) => {
    try {
      const res = await fetch(`/api/xtgl/yhgl/users/${id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status: 'locked' })
      });
      if (res.ok) {
        message.success('用户已禁用');
        fetchUsers();
      }
    } catch (error) {
      message.error('操作失败');
    }
  };

  const handleUnlockUser = async (id: string) => {
    try {
      const res = await fetch(`/api/xtgl/yhgl/users/${id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status: 'active' })
      });
      if (res.ok) {
        message.success('用户已启用');
        fetchUsers();
      }
    } catch (error) {
      message.error('操作失败');
    }
  };

  useEffect(() => {
    fetchUsers();
  }, []);

  const columns = [
    {
      title: '用户信息',
      dataIndex: 'username',
      key: 'username',
      width: 180,
      render: (_: any, record: User) => (
        <div style={{ display: 'flex', alignItems: 'center' }}>
          <Avatar icon={<UserOutlined />} style={{ marginRight: 8 }} />
          <div>
            <div style={{ fontWeight: 500 }}>{record.name}</div>
            <div style={{ fontSize: 12, color: '#666' }}>{record.username}</div>
          </div>
        </div>
      ),
    },
    { title: '部门', dataIndex: 'department', key: 'department', width: 120 },
    {
      title: '角色',
      dataIndex: 'role',
      key: 'role',
      width: 120,
      render: (role: string) => {
        const displayRole = normalizeUserRole(role);
        return (
          <Tag color={displayRole === '系统管理员' ? 'red' : displayRole === ROLE_KM_MANAGER ? 'blue' : 'default'}>
            {displayRole}
          </Tag>
        );
      },
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 100,
      render: (status: string) => {
        const statusConfig = {
          active: { color: 'success', text: '正常', icon: <CheckCircleOutlined /> },
          disabled: { color: 'default', text: '禁用', icon: <CloseCircleOutlined /> },
          locked: { color: 'warning', text: '锁定', icon: <LockOutlined /> }
        };
        const config = statusConfig[status as keyof typeof statusConfig] || statusConfig.disabled;
        return <Tag color={config.color} icon={config.icon}>{config.text}</Tag>;
      },
    },
    {
      title: '最后登录',
      dataIndex: 'lastLogin',
      key: 'lastLogin',
      width: 150,
      render: (time: string) => time ? time : '-',
    },
    {
      title: '操作',
      key: 'action',
      width: 200,
      render: (_: any, record: User) => (
        <Space size="small">
          <Button type="link" icon={<EditOutlined />} onClick={() => {
            setSelectedUser(record);
            setModalType('edit');
            form.setFieldsValue(record);
            setModalVisible(true);
          }}>编辑</Button>
          {record.status === 'active' ? (
            <Button type="link" danger icon={<LockOutlined />} onClick={() => handleLockUser(record.id)}>禁用</Button>
          ) : (
            <Button type="link" icon={<UnlockOutlined />} onClick={() => handleUnlockUser(record.id)}>启用</Button>
          )}
          <Popconfirm title="确定要删除这个用户吗？" onConfirm={() => handleDeleteUser(record.id)} okText="确定" cancelText="取消">
            <Button type="link" danger icon={<DeleteOutlined />}>删除</Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  const filteredUsers = useMemo(() => {
    const kw = searchKeyword.trim().toLowerCase();
    if (!kw) return users;
    return users.filter((u) =>
      [u.username, u.name, u.department, u.role, u.email, u.phone]
        .some((field) => String(field || '').toLowerCase().includes(kw)),
    );
  }, [users, searchKeyword]);

  const tablePagination = useTablePagination(filteredUsers.length, {
    resetDeps: [searchKeyword],
  });

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
          <Space>
            <Input
              placeholder="搜索用户..."
              style={{ width: 200 }}
              value={searchKeyword}
              onChange={(e) => setSearchKeyword(e.target.value)}
              allowClear
            />
          </Space>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => {
            setSelectedUser(null);
            setModalType('add');
            form.resetFields();
            setModalVisible(true);
          }}>新增用户</Button>
        </div>
        <Table
          columns={columns}
          dataSource={filteredUsers}
          rowKey="id"
          loading={loading}
          pagination={tablePagination}
        />
      </Card>

      <Modal
        title={modalType === 'add' ? '新增用户' : '编辑用户'}
        open={modalVisible}
        onCancel={() => { setModalVisible(false); form.resetFields(); }}
        footer={null}
        width={600}
        destroyOnHidden
      >
        <Form form={form} layout="vertical" onFinish={handleFormSubmit} style={{ marginTop: 24 }}>
          <Form.Item label="用户名" name="username" rules={[{ required: true }]}>
            <Input placeholder="请输入用户名" />
          </Form.Item>
          <Form.Item label="姓名" name="name" rules={[{ required: true }]}>  
            <Input placeholder="请输入真实姓名" />
          </Form.Item>
          <Form.Item label="部门" name="department" rules={[{ required: true }]}>  
            <Input placeholder="请输入部门" />
          </Form.Item>
          <Form.Item
            label="密码"
            name="password"
            rules={modalType === 'add' ? [{ required: true, message: '请输入登录密码' }] : []}
          >
            <Input.Password placeholder="请输入登录密码" />
          </Form.Item>
          <Form.Item label="角色" name="role" rules={[{ required: true }]}>  
            <Select placeholder="请选择角色">
              {ROLE_OPTIONS.map((role) => (
                <Option key={role} value={role}>{role}</Option>
              ))}
            </Select>
          </Form.Item>
          <Form.Item style={{ marginBottom: 0, textAlign: 'right' }}>
            <Space>
              <Button onClick={() => { setModalVisible(false); form.resetFields(); }}>取消</Button>
              <Button type="primary" htmlType="submit">{modalType === 'add' ? '新增' : '保存'}</Button>
            </Space>
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
