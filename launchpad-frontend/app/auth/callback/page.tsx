'use client';

import { Suspense, useEffect, useRef } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { motion } from 'framer-motion';
import { LogoMark } from '@/components/logo-mark';
import { useAuthStore } from '@/lib/store/auth';
import { authApi } from '@/lib/api/auth';

const rise = { initial: { opacity: 0, y: 14 }, animate: { opacity: 1, y: 0, transition: { duration: 0.4, ease: [0.16, 1, 0.3, 1] as const } } };

function AuthCallbackInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const setAuth = useAuthStore((state) => state.setAuth);
  const handledRef = useRef(false);

  useEffect(() => {
    // The tokens live only in the URL fragment, which this effect clears after its first
    // read. React StrictMode re-invokes effects once in dev, and that second run would
    // see an already-cleared fragment and misreport "missing tokens" — guard against it.
    if (handledRef.current) return;
    handledRef.current = true;

    const handleCallback = async () => {
      const error = searchParams.get('error');

      if (error) {
        router.push(`/login?error=${encodeURIComponent(error)}`);
        return;
      }

      // Tokens arrive in the URL fragment, not the query string, so they're never sent
      // to any server or logged in a Referer header. useSearchParams only sees the query
      // string, so the fragment is read directly off window.location.
      const hashParams = new URLSearchParams(window.location.hash.slice(1));
      const accessToken = hashParams.get('access_token');
      const refreshToken = hashParams.get('refresh_token');
      // Scrub the tokens out of the visible URL/history now that they've been read.
      window.history.replaceState(null, '', window.location.pathname);

      if (accessToken && refreshToken) {
        try {
          localStorage.setItem('access_token', accessToken);
          localStorage.setItem('refresh_token', refreshToken);

          const user = await authApi.getCurrentUser();
          setAuth(user, accessToken, refreshToken);

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
    <div className="min-h-screen flex items-center justify-center bg-background relative overflow-hidden px-4">
      <div className="brand-glow pointer-events-none absolute inset-0" />
      <motion.div {...rise} className="relative flex flex-col items-center gap-5 text-center">
        <div className="inline-flex items-center gap-2.5">
          <LogoMark size={32} />
          <span className="font-display text-lg font-semibold tracking-tight text-foreground">Launchpad</span>
        </div>
        <div className="w-8 h-8 border-2 border-hairline-strong border-t-brand rounded-full animate-spin" />
        <div className="space-y-1.5">
          <span className="eyebrow">Signing in</span>
          <p className="text-sm text-muted-foreground">Completing authentication…</p>
        </div>
      </motion.div>
    </div>
  );
}

export default function AuthCallback() {
  return (
    <Suspense>
      <AuthCallbackInner />
    </Suspense>
  );
}
