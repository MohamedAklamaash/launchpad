'use client';

import { Suspense, useEffect, useRef, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { motion } from 'framer-motion';
import { LogoMark } from '@/components/logo-mark';
import { useAuthStore } from '@/lib/store/auth';
import { authApi } from '@/lib/api/auth';
import { ORGS_ENABLED } from '@/lib/orgs-enabled';
import { postLoginPath } from '@/lib/post-login';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { toast } from 'sonner';

const rise = { initial: { opacity: 0, y: 14 }, animate: { opacity: 1, y: 0, transition: { duration: 0.4, ease: [0.16, 1, 0.3, 1] as const } } };

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
        toast.success('Authenticated successfully');
        router.replace(ORGS_ENABLED ? postLoginPath() : '/dashboard');
      })
      .catch((err) => {
        const msg = err.response?.data?.error || 'Invalid or expired link';
        toast.error(msg);
        router.replace(`/login?email=${encodeURIComponent(email)}`);
      });
  };

  useEffect(() => {
    if (ran.current) return;
    ran.current = true;

    if (!email || !otp) {
      toast.error('Invalid authentication link');
      router.replace('/login');
      return;
    }

    if (!ORGS_ENABLED) submit();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="min-h-screen flex items-center justify-center bg-background relative overflow-hidden px-4">
      <div className="brand-glow pointer-events-none absolute inset-0" />
      <motion.div {...rise} className="relative flex flex-col items-center gap-5 text-center">
        <div className="inline-flex items-center gap-2.5">
          <LogoMark size={32} />
          <span className="font-display text-lg font-semibold tracking-tight text-foreground">Launchpad</span>
        </div>
        {ORGS_ENABLED && email && otp && !submitting ? (
          <form
            onSubmit={(e) => { e.preventDefault(); submit(password || undefined); }}
            className="w-full max-w-xs space-y-4 text-left"
          >
            <div className="space-y-1.5 text-center">
              <span className="eyebrow">Confirm your email</span>
              <p className="text-sm text-muted-foreground">
                Choose a password for <span className="font-mono text-foreground">{email}</span>, or leave it blank to continue without one.
              </p>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="new-password" className="eyebrow">Set your password (optional)</Label>
              <Input id="new-password" type="password" value={password} minLength={6} maxLength={72}
                autoComplete="new-password" placeholder="6–72 characters"
                onChange={(e) => setPassword(e.target.value)} />
            </div>
            <Button type="submit" size="lg" className="w-full">Verify &amp; continue</Button>
          </form>
        ) : (
          <>
            <div className="w-6 h-6 border-2 border-hairline-strong border-t-brand rounded-full animate-spin" />
            <div className="space-y-1.5">
              <span className="eyebrow">Verifying link</span>
              <p className="text-sm text-muted-foreground">Authenticating…</p>
            </div>
          </>
        )}
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
