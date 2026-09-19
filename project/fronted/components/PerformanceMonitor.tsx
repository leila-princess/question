// components/PerformanceMonitor.tsx
"use client"; 
import { useEffect, Suspense } from 'react';
import { usePathname, useSearchParams } from 'next/navigation';
import { onCLS, onFCP, onLCP, onTTFB } from 'web-vitals';

function PerformanceMonitorContent() {
  const pathname = usePathname();
  const searchParams = useSearchParams();

  useEffect(() => {
    // 监控页面性能
    const reportWebVitals = () => {
      onCLS(console.log);  // 累积布局偏移
      onFCP(console.log);  // 首次内容绘制
      onLCP(console.log);  // 最大内容绘制
      onTTFB(console.log); // 首字节时间
    };

    // 页面加载时报告性能指标
    reportWebVitals();

    // 监听路由变化
    const handleRouteChange = () => {
      console.log('🚦 路由已切换:', `${pathname}${searchParams ? '?' + searchParams.toString() : ''}`, performance.now());
      // 路由切换后重新报告性能指标
      setTimeout(reportWebVitals, 0);
    };

    // 使用 Intersection Observer 监听页面可见性变化
    const handleVisibilityChange = () => {
      if (document.visibilityState === 'visible') {
        handleRouteChange();
      }
    };

    document.addEventListener('visibilitychange', handleVisibilityChange);
    
    // 监听 popstate 事件（浏览器前进后退）
    window.addEventListener('popstate', handleRouteChange);

    return () => {
      document.removeEventListener('visibilitychange', handleVisibilityChange);
      window.removeEventListener('popstate', handleRouteChange);
    };
  }, [pathname, searchParams]);

  return null;
}

export default function PerformanceMonitor() {
  return (
    <Suspense fallback={null}>
      <PerformanceMonitorContent />
    </Suspense>
  );
}