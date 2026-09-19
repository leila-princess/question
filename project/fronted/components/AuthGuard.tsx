'use client';

import { useEffect } from 'react';
import { usePathname, useRouter } from 'next/navigation';

export default function AuthGuard() {
  const router = useRouter();
  const pathname = usePathname();

  useEffect(() => {
    if (pathname === '/login') return;
    if (localStorage.getItem('isLogin') !== 'true') {
      router.replace('/login');
    }
  }, [pathname, router]);

  return null;
}
