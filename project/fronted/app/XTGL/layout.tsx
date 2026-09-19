"use client";
import React from 'react';
import { Layout, Menu } from 'antd';
import { useRouter, usePathname } from 'next/navigation';
import { UserOutlined, DatabaseOutlined, CodeOutlined, CloudOutlined, FileTextOutlined, QuestionCircleOutlined } from '@ant-design/icons';

const { Sider, Content } = Layout;

const menuItems = [
  { key: 'YHGL', icon: <UserOutlined />, label: '用户管理' },
  { key: 'SJBF', icon: <CloudOutlined />, label: '数据备份' },
  { key: 'RZGL', icon: <FileTextOutlined />, label: '日志管理' },
  { key: 'SJKGL', icon: <DatabaseOutlined />, label: '数据库管理' },
  { key: 'MXGL', icon: <CodeOutlined />, label: '模型管理' },
  { key: 'WTGL', icon: <QuestionCircleOutlined />, label: '问题反馈' },
];

export default function XTGLSideLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();

  const currentKey = pathname.split('/').pop() || 'YHGL';

  return (
    <Layout style={{ minHeight: '100vh' }}>
      {/* <Sider width={200} style={{ background: '#fff' }}>
        <Menu
          mode="inline"
          selectedKeys={[currentKey]}
          onClick={({ key }) => router.push(`/XTGL/${key}`)}
          items={menuItems}
          style={{ height: '100%', borderRight: 0 }}
        />
      </Sider> */}
      <Layout style={{ padding: '0' }}>
        <Content style={{ background: '#fff', minHeight: 280 }}>
          {children}
        </Content>
      </Layout>
    </Layout>
  );
}