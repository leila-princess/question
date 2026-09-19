// pages/_app.js 或 app/layout.js (Next.js 13+)
import { SessionProvider } from 'next-auth/react'
import { useEffect } from 'react'
import { useRouter } from 'next/router'

export default function App({ Component, pageProps }) {
  const router = useRouter()
  
  useEffect(() => {
    // 预加载核心页面（不影响现有功能）
    const preloadCorePages = () => {
      // 列出应用的核心页面路径
      const corePages = [
        '/',
      '/select',
      '/select_FF',
      '/select_XM',
      '/select_ZB',
      '/qa',
      '/Div_Management',
      '/experiment',
      '/experiment_gz',
      '/experimentsc',
      '/knowledge_management',
      '/visual_management',
      '/user',
      '/settings',
      '/login',
      ]
      
      // 预加载每个页面
      corePages.forEach(page => {
        try {
          router.prefetch(page)
        } catch (error) {
          console.warn(`预加载页面 ${page} 失败:`, error.message)
        }
      })
    }
    
    // 在页面加载完成后开始预加载
    if (document.readyState === 'complete') {
      preloadCorePages()
    } else {
      window.addEventListener('load', preloadCorePages)
      return () => window.removeEventListener('load', preloadCorePages)
    }
  }, [router])
  
  return (
    <SessionProvider session={pageProps.session}>
      <Component {...pageProps} />
    </SessionProvider>
  )
}