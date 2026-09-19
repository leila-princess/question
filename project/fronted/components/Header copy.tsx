'use client';

import { useState, useEffect } from 'react';
import { 
  BellOutlined, 
  UserOutlined, 
  SearchOutlined,
  LogoutOutlined
} from "@ant-design/icons";
import { 
  Avatar, 
  Dropdown, 
  Input,
  Badge,
  Space,
  message
} from "antd";
import { useRouter } from "next/navigation";
import type { MenuProps } from 'antd';

export default function Header() {
  const router = useRouter();
  const [userName, setUserName] = useState('用户');

  useEffect(() => {
    const storedUserName = localStorage.getItem('userName');
    if (storedUserName) {
      setUserName(storedUserName);
    }
  }, []);

  const handleLogout = async () => {
    try {
      await fetch('/api/logout', { method: 'POST' });
    } catch (error) {
    }
    localStorage.removeItem('isLogin');
    localStorage.removeItem('userId');
    localStorage.removeItem('userName');
    localStorage.removeItem('userRole');
    localStorage.removeItem('token');
    message.success('已退出登录');
    router.push('/login');
  };

  const userMenuItems: MenuProps['items'] = [
    {
      key: 'profile',
      label: '个人中心',
      icon: <UserOutlined />,
      onClick: () => router.push('/profile')
    },
    {
      key: 'account-settings',
      label: '账户设置',
      icon: <UserOutlined />,
      onClick: () => router.push('/account-settings')
    },
    {
      type: 'divider',
    },
    {
      key: 'logout',
      label: '退出登录',
      icon: <LogoutOutlined />,
      danger: true,
      onClick: handleLogout
    }
  ];

  return (
    <div style={{ 
      height: 64,
      background: '#fff',
      borderBottom: '1px solid #f0f0f0',
      padding: '0 24px',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'space-between',
      position: 'sticky',
      top: 0,
      zIndex: 100
    }}>
      <Space size="large">
        <div style={{ 
          fontSize: 36, 
          fontWeight: 'bold', 
          color: '#235cff' 
        }}>
          XX原型系统
        </div>
      </Space>
      
      <Space size="middle">
        <Input 
          placeholder="搜索..." 
          prefix={<SearchOutlined />}
          style={{ width: 200, borderRadius: 20 }}
        />
        
        <Badge count={5} size="small">
          <BellOutlined style={{ fontSize: 20, cursor: 'pointer' }} />
        </Badge>
        
        <Dropdown menu={{ items: userMenuItems }} placement="bottomRight" arrow>
          <div style={{ cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 8 }}>
            <Avatar icon={<UserOutlined />} style={{ backgroundColor: '#235cff' }} />
            <span style={{ fontSize: 14 }}>{userName}</span>
          </div>
        </Dropdown>
      </Space>
    </div>
  );
}
