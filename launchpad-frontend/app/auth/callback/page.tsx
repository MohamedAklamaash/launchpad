'use client';

import { Suspense, useEffect, useRef } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { LogoMark } from '@/components/logo-mark';
import { useAuthStore } from '@/lib/store/auth';
import { authApi } from '@/lib/api/auth';
import { ORGS_ENABLED } from '@/lib/orgs-enabled';
import { postLoginPath } from '@/lib/post-login';

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

          router.push(ORGS_ENABLED ? postLoginPath() : '/dashboard');
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
    <div className="flex min-h-dvh items-center justify-center bg-background px-4">
      <div role="status" className="reveal flex flex-col items-center gap-4 text-center">
        <LogoMark size={28} />
        <div className="size-5 animate-spin rounded-full border-2 border-border border-t-brand" />
        <div className="space-y-1">
          <h1 className="text-sm font-medium text-foreground">Signing you in</h1>
          <p className="text-sm text-muted-foreground">This takes a moment.</p>
        </div>
      </div>
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
