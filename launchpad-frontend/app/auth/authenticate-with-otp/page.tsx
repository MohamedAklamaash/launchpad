'use client';

import { Suspense, useEffect, useRef } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { useAuthStore } from '@/lib/store/auth';
import { authApi } from '@/lib/api/auth';
import { toast } from 'sonner';

function AuthenticateWithOtpInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const setAuth = useAuthStore((s) => s.setAuth);
  const ran = useRef(false);

  useEffect(() => {
    if (ran.current) return;
    ran.current = true;

    const email = searchParams.get('email');
    const otp = searchParams.get('otp');

    if (!email || !otp) {
      toast.error('Invalid authentication link');
      router.replace('/login');
      return;
    }

    authApi.verifyOtp(email, otp)
      .then((res) => {
        setAuth(res.user, res.access_token, res.refresh_token);
        toast.success('Authenticated successfully');
        router.replace('/dashboard');
      })
      .catch((err) => {
        const msg = err.response?.data?.error || 'Invalid or expired link';
        toast.error(msg);
        router.replace(`/login?email=${encodeURIComponent(email)}`);
      });
  }, []);

  return (
    <div className="min-h-screen flex items-center justify-center bg-[#0a0a0a]">
      <div className="text-center space-y-3">
        <div className="w-6 h-6 border-2 border-[#333] border-t-violet-500 rounded-full animate-spin mx-auto" />
        <p className="text-[#666] text-sm font-mono">Authenticating…</p>
      </div>
    </div>
  );
}

export default function AuthenticateWithOtpPage() {
  return (
    <Suspense>
      <AuthenticateWithOtpInner />
    </Suspense>
  );
}
