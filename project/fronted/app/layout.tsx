import type { Metadata } from 'next';
import Header from '@/components/Header';
import AuthGuard from '@/components/AuthGuard';
import { ReactNode } from 'react';

export const metadata: Metadata = {
  title: 'XX原型系统',
  description: 'XX原型系统',
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="zh-CN" style={{ height: '100%' }}>
      <body
        style={{
          margin: 0,
          height: '100%',
          overflow: 'hidden',
          backgroundColor: '#f5f5f5',
        }}
      >
        <div
          style={{
            display: 'flex',
            flexDirection: 'column',
            height: '100%',
            minHeight: 0,
            backgroundColor: '#f5f5f5',
          }}
        >
          <AuthGuard />
          <Header />
          <main
            style={{
              flex: 1,
              minHeight: 0,
              padding: '2px',
              overflow: 'auto',
            }}
          >
            {children}
          </main>
        </div>
      </body>
    </html>
  );
}