'use client';

import { useEffect } from 'react';
import { useRouter } from 'next/navigation';

export function AuthRedirect() {
  const router = useRouter();

  useEffect(() => {
    if (localStorage.getItem('access_token')) router.replace('/dashboard');
  }, [router]);

  return null;
}
