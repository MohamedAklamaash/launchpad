'use client';

import { Suspense, useEffect, useRef } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { motion } from 'framer-motion';
import { LogoMark } from '@/components/logo-mark';
import { useAuthStore } from '@/lib/store/auth';
import { authApi } from '@/lib/api/auth';
import { toast } from 'sonner';

const rise = { initial: { opacity: 0, y: 14 }, animate: { opacity: 1, y: 0, transition: { duration: 0.4, ease: [0.16, 1, 0.3, 1] as const } } };

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
  }, [router, searchParams, setAuth]);

  return (
    <div className="min-h-screen flex items-center justify-center bg-background relative overflow-hidden px-4">
      <div className="brand-glow pointer-events-none absolute inset-0" />
      <motion.div {...rise} className="relative flex flex-col items-center gap-5 text-center">
        <div className="inline-flex items-center gap-2.5">
          <LogoMark size={32} />
          <span className="font-display text-lg font-semibold tracking-tight text-foreground">Launchpad</span>
        </div>
        <div className="w-6 h-6 border-2 border-hairline-strong border-t-brand rounded-full animate-spin" />
        <div className="space-y-1.5">
          <span className="eyebrow">Verifying link</span>
          <p className="text-sm text-muted-foreground">Authenticating…</p>
        </div>
      </motion.div>
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
