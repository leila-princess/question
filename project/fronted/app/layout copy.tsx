"use client";
import { Inter } from "next/font/google";
import "./globals.css";
import { AntdRegistry } from '@ant-design/nextjs-registry';
import { ConfigProvider, Layout } from "antd";
import theme from "./theme/themeConfig";
import { useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import PerformanceMonitor from '@/components/PerformanceMonitor';
import GlobalNav from '@/components/GlobalNav';
import Header from '@/components/Header';
import Footer from '@/components/Footer';

const { Content } = Layout;
const inter = Inter({ subsets: ["latin"] });

export const dynamic = 'force-dynamic';
export default function RootLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const [collapsed, setCollapsed] = useState(false);

  useEffect(() => {
    if (typeof window !== "undefined" && pathname !== "/login") {
      if (localStorage.getItem("isLogin") !== "true") {
        router.push("/login");
      }
    }
  }, [pathname, router]);
  
  const isLoginPage = pathname === "/login";
  
  return (
    <html lang="zh-CN">
      <body className={inter.className} suppressHydrationWarning={true}>
        <PerformanceMonitor />
        <ConfigProvider theme={theme}>
          {isLoginPage ? (
            children
          ) : (
            <Layout style={{ minHeight: "100vh" }}>
              <Layout.Sider
                width={240}
                collapsedWidth={64}
                collapsed={collapsed}
                onCollapse={setCollapsed}
                theme="light"
                style={{
                  position: 'fixed',
                  left: 0,
                  top: 0,
                  bottom: 0,
                  zIndex: 1000,
                  borderRight: '1px solid #f0f0f0',
                  overflow: 'hidden',
                }}
                trigger={null}
              >
                <GlobalNav collapsed={collapsed} onCollapse={setCollapsed} />
              </Layout.Sider>
              
              <Layout style={{ 
                marginLeft: collapsed ? 64 : 240, 
                transition: 'margin-left 0.2s',
                minHeight: '100vh',
                display: 'flex',
                flexDirection: 'column'
              }}>
                <Header />
                <Content style={{ 
                  flex: 1,
                  margin: '5px',
                  padding: 5,
                  background: '#fff',
                  borderRadius: 8,
                  overflow: 'auto'
                }}>
                  {children}
                </Content>
                {/* <Footer /> */}
              </Layout>
            </Layout>
          )}
        </ConfigProvider>
      </body>
    </html>
  );
}
