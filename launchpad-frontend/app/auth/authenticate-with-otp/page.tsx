'use client';

import { Suspense, useEffect, useRef, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { LogoMark } from '@/components/logo-mark';
import { useAuthStore } from '@/lib/store/auth';
import { authApi } from '@/lib/api/auth';
import { ORGS_ENABLED } from '@/lib/orgs-enabled';
import { postLoginPath } from '@/lib/post-login';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { toast } from 'sonner';

function AuthenticateWithOtpInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const setAuth = useAuthStore((s) => s.setAuth);
  const ran = useRef(false);
  const [password, setPassword] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const email = searchParams.get('email');
  const otp = searchParams.get('otp');

  // With orgs on, the link opens a form so the invited user can choose a password before
  // the code is spent. With orgs off it verifies immediately, as it always has.
  const submit = (newPassword?: string) => {
    if (!email || !otp) return;
    setSubmitting(true);
    authApi.verifyOtp(email, otp, newPassword)
      .then((res) => {
        setAuth(res.user, res.access_token, res.refresh_token);
        toast.success('You\'re signed in.');
        router.replace(ORGS_ENABLED ? postLoginPath() : '/dashboard');
      })
      .catch((err) => {
        const msg = err.response?.data?.error || 'This link is invalid or has expired.';
        toast.error(msg);
        router.replace(`/login?email=${encodeURIComponent(email)}`);
      });
  };

  useEffect(() => {
    if (ran.current) return;
    ran.current = true;

    if (!email || !otp) {
      toast.error('This sign-in link is not valid.');
      router.replace('/login');
      return;
    }

    if (!ORGS_ENABLED) submit();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="flex min-h-dvh items-center justify-center bg-background px-4">
      <div className="reveal flex w-full max-w-[22rem] flex-col items-center gap-6 text-center">
        <LogoMark size={28} />
        {ORGS_ENABLED && email && otp && !submitting ? (
          <form
            onSubmit={(e) => { e.preventDefault(); submit(password || undefined); }}
            className="w-full space-y-5 text-left"
          >
            <div className="space-y-1.5 text-center">
              <h1 className="text-xl font-semibold tracking-tight text-foreground">Confirm your email</h1>
              <p className="text-sm text-muted-foreground">
                Choose a password for <span className="font-mono text-foreground">{email}</span>, or leave it blank to continue without one.
              </p>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="new-password">Password (optional)</Label>
              <Input id="new-password" type="password" value={password} minLength={6} maxLength={72}
                autoComplete="new-password"
                onChange={(e) => setPassword(e.target.value)} />
              <p className="text-xs text-muted-foreground">6 to 72 characters.</p>
            </div>
            <Button type="submit" size="lg" className="w-full">Continue</Button>
          </form>
        ) : (
          <div role="status" className="flex flex-col items-center gap-4">
            <div className="size-5 animate-spin rounded-full border-2 border-border border-t-brand" />
            <div className="space-y-1">
              <h1 className="text-sm font-medium text-foreground">Checking your link</h1>
              <p className="text-sm text-muted-foreground">This takes a moment.</p>
            </div>
          </div>
        )}
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
