'use client';

import { useEffect } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { useAuthStore } from '@/lib/store/auth';
import { authApi } from '@/lib/api/auth';

export default function AuthCallback() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const setAuth = useAuthStore((state) => state.setAuth);

  useEffect(() => {
    const handleCallback = async () => {
      const accessToken = searchParams.get('access_token');
      const refreshToken = searchParams.get('refresh_token');
      const error = searchParams.get('error');

      if (error) {
        router.push(`/login?error=${encodeURIComponent(error)}`);
        return;
      }

      if (accessToken && refreshToken) {
        try {
          // Store tokens temporarily
          localStorage.setItem('access_token', accessToken);
          localStorage.setItem('refresh_token', refreshToken);

          // Fetch user data
          const user = await authApi.getCurrentUser();
          
          // Set auth state
          setAuth(user, accessToken, refreshToken);

          // Redirect to dashboard
          router.push('/dashboard');
        } catch (err) {
          console.error('Auth callback error:', err);
          router.push('/login?error=Authentication failed');
        }
      } else {
        router.push('/login?error=Missing tokens');
      }
    };

    handleCallback();
  }, [searchParams, router, setAuth]);

  return (
    <div className="min-h-screen flex items-center justify-center bg-[#0a0a0a]">
      <div className="text-center">
        <div className="w-8 h-8 border-2 border-[#333] border-t-violet-500 rounded-full animate-spin mx-auto mb-4" />
        <p className="text-[#a3a3a3]">Completing authentication...</p>
      </div>
    </div>
  );
}
