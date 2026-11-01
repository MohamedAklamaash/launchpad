'use client';

import { Suspense, useEffect, useState } from 'react';
import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Github, ArrowLeft } from 'lucide-react';
import { LogoMark } from '@/components/logo-mark';
import { ThemeToggle } from '@/components/theme-toggle';
import { useAuthStore } from '@/lib/store/auth';
import { authApi } from '@/lib/api/auth';
import { ORGS_ENABLED } from '@/lib/orgs-enabled';
import { postLoginPath, rememberNext, safeNextPath } from '@/lib/post-login';
import { toast } from 'sonner';

type CredsStep = 'login' | 'otp' | 'forgot' | 'verify-reset' | 'reset-done';

function LoginPageInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const setAuth = useAuthStore((s) => s.setAuth);

  const prefillEmail = searchParams.get('email') || '';
  const nextParam = ORGS_ENABLED ? safeNextPath(searchParams.get('next')) : null;
  const afterLogin = () => (ORGS_ENABLED ? postLoginPath(nextParam) : '/dashboard');

  const [step, setStep] = useState<CredsStep>('login');
  const [email, setEmail] = useState(prefillEmail);
  const [password, setPassword] = useState('');
  const [otp, setOtp] = useState('');
  const [resetToken, setResetToken] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [otpPassword, setOtpPassword] = useState('');
  const [resetRequired, setResetRequired] = useState(false);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (isAuthenticated) router.push(ORGS_ENABLED ? postLoginPath(safeNextPath(searchParams.get('next'))) : '/dashboard');
    const error = searchParams.get('error');
    if (error) toast.error(decodeURIComponent(error));
    if (ORGS_ENABLED && searchParams.get('reason') === 'reauth') {
      toast.info('Sign in again to confirm this change.', { id: 'reauth-notice' });
    }
  }, [isAuthenticated, router, searchParams]);

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    try {
      setResetRequired(false);
      const res = await authApi.credentialsLogin(email, password);
      setAuth(res.user, res.access_token, res.refresh_token);
      router.push(afterLogin());
    } catch (err: unknown) {
      const error = err as { response?: { data?: { error?: string; message?: string } } };
      const msg: string = error.response?.data?.error || error.response?.data?.message || '';
      if (msg.toLowerCase().includes('otp pending')) {
        toast.info('Enter the code we emailed you to finish signing in.');
        setStep('otp');
      } else if (msg.toLowerCase().includes('password reset required')) {
        setResetRequired(true);
      } else {
        toast.error(msg || 'Wrong email or password.');
      }
    } finally {
      setLoading(false);
    }
  };

  const handleVerifyOtp = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    try {
      const res = await authApi.verifyOtp(email, otp, otpPassword || undefined);
      setAuth(res.user, res.access_token, res.refresh_token);
      router.push(afterLogin());
    } catch (err: unknown) {
      const error = err as { response?: { data?: { error?: string } } };
      toast.error(error.response?.data?.error || 'That code is wrong or has expired.');
    } finally {
      setLoading(false);
    }
  };

  const handleForgotPassword = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    try {
      await authApi.forgotPassword(email);
      toast.success('Code sent. Check your email.');
      setStep('verify-reset');
    } catch (err: unknown) {
      const error = err as { response?: { data?: { error?: string } } };
      toast.error(error.response?.data?.error || 'Could not send the code. Try again.');
    } finally {
      setLoading(false);
    }
  };

  const handleVerifyResetOtp = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    try {
      const { token } = await authApi.verifyResetOtp(email, otp);
      setResetToken(token);
      setOtp('');
      setStep('reset-done');
    } catch (err: unknown) {
      const error = err as { response?: { data?: { error?: string } } };
      toast.error(error.response?.data?.error || 'That code is wrong or has expired.');
    } finally {
      setLoading(false);
    }
  };

  const handleResetPassword = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    try {
      await authApi.resetPassword(resetToken, newPassword);
      toast.success('Password updated. Sign in with the new one.');
      setStep('login');
      setNewPassword('');
    } catch (err: unknown) {
      const error = err as { response?: { data?: { error?: string } } };
      toast.error(error.response?.data?.error || 'Could not update the password. Try again.');
    } finally {
      setLoading(false);
    }
  };

  const renderStep = () => {
    if (step === 'otp') return (
      <StepShell title="Check your email" description={<>We sent a 6-digit code to <span className="font-mono text-foreground">{email}</span>.</>}>
        <form onSubmit={handleVerifyOtp} className="space-y-4">
          <Field id="otp" label="Code">
            <Input id="otp" value={otp} onChange={(e) => setOtp(e.target.value)} required maxLength={6}
              inputMode="numeric" autoComplete="one-time-code" placeholder="000000"
              className="h-10 text-center font-mono text-lg tracking-[0.4em]" />
          </Field>
          {ORGS_ENABLED && (
            <Field id="otp-password" label="Password (optional)" hint="6 to 72 characters. Set one now to sign in with email next time.">
              <Input id="otp-password" type="password" value={otpPassword}
                onChange={(e) => setOtpPassword(e.target.value)} minLength={6} maxLength={72}
                autoComplete="new-password" />
            </Field>
          )}
          <Button type="submit" size="lg" disabled={loading} className="w-full">
            {loading ? 'Verifying…' : 'Verify and sign in'}
          </Button>
          <BackToLogin onClick={() => { setStep('login'); setOtp(''); }} />
        </form>
      </StepShell>
    );

    if (step === 'forgot') return (
      <StepShell title="Reset your password" description="Enter your email and we'll send a 6-digit code.">
        <form onSubmit={handleForgotPassword} className="space-y-4">
          <Field id="email" label="Email">
            <Input id="email" type="email" value={email} autoComplete="email"
              onChange={(e) => setEmail(e.target.value)} required />
          </Field>
          <Button type="submit" size="lg" disabled={loading} className="w-full">
            {loading ? 'Sending…' : 'Send code'}
          </Button>
          <BackToLogin onClick={() => setStep('login')} />
        </form>
      </StepShell>
    );

    if (step === 'verify-reset') return (
      <StepShell title="Enter the code" description={<>We sent a 6-digit code to <span className="font-mono text-foreground">{email}</span>.</>}>
        <form onSubmit={handleVerifyResetOtp} className="space-y-4">
          <Field id="reset-otp" label="Code">
            <Input id="reset-otp" value={otp} onChange={(e) => setOtp(e.target.value)} required maxLength={6}
              inputMode="numeric" autoComplete="one-time-code" placeholder="000000"
              className="h-10 text-center font-mono text-lg tracking-[0.4em]" />
          </Field>
          <Button type="submit" size="lg" disabled={loading} className="w-full">
            {loading ? 'Verifying…' : 'Continue'}
          </Button>
          <BackToLogin onClick={() => { setStep('login'); setOtp(''); }} />
        </form>
      </StepShell>
    );

    if (step === 'reset-done') return (
      <StepShell title="Choose a new password">
        <form onSubmit={handleResetPassword} className="space-y-4">
          <Field id="new-password" label="New password" hint="At least 6 characters.">
            <Input id="new-password" type="password" value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)} required minLength={6}
              autoComplete="new-password" />
          </Field>
          <Button type="submit" size="lg" disabled={loading} className="w-full">
            {loading ? 'Saving…' : 'Update password'}
          </Button>
        </form>
      </StepShell>
    );

    return (
      <StepShell title="Sign in to Launchpad" description="Use GitHub, or the email and password on your account.">
        <div className="space-y-5">
          <Button
            type="button"
            variant="outline"
            size="lg"
            className="w-full gap-2"
            onClick={() => { if (ORGS_ENABLED) rememberNext(nextParam); authApi.githubLogin(); }}
          >
            <Github className="size-4" />
            Continue with GitHub
          </Button>

          <div className="flex items-center gap-3 text-xs text-muted-foreground" role="separator" aria-label="or">
            <span className="h-px flex-1 bg-border" />
            or
            <span className="h-px flex-1 bg-border" />
          </div>

          <form onSubmit={handleLogin} className="space-y-4">
            <Field id="email" label="Email">
              <Input id="email" type="email" value={email} autoComplete="email" placeholder="you@company.com"
                onChange={(e) => setEmail(e.target.value)} required />
            </Field>
            <Field id="password" label="Password">
              <Input id="password" type="password" value={password} autoComplete="current-password"
                onChange={(e) => setPassword(e.target.value)} required />
            </Field>
            {resetRequired && (
              <div role="alert" className="rounded-md border border-warning/30 bg-warning/10 px-3 py-2.5 text-xs text-foreground">
                <p>Your password needs resetting. The old one no longer works.</p>
                <button type="button" onClick={() => setStep('forgot')}
                  className="mt-1 font-medium text-brand underline-offset-2 hover:underline">
                  Reset it with a code by email
                </button>
              </div>
            )}
            <Button type="submit" size="lg" disabled={loading} className="w-full">
              {loading ? 'Signing in…' : 'Sign in'}
            </Button>
            <button type="button" onClick={() => setStep('forgot')}
              className="w-full rounded-md py-1 text-xs text-muted-foreground transition-colors hover:text-foreground">
              Forgot your password?
            </button>
          </form>
        </div>
      </StepShell>
    );
  };

  return (
    <div className="flex min-h-dvh flex-col bg-background">
      <header className="flex h-14 items-center justify-between px-4 sm:px-6">
        <Link href="/" className="flex items-center gap-2.5 rounded-md text-sm font-semibold tracking-tight text-foreground">
          <LogoMark size={22} />
          Launchpad
        </Link>
        <ThemeToggle />
      </header>

      <main className="flex flex-1 items-start justify-center px-4 pb-16 pt-10 sm:items-center sm:pt-0">
        <div className="reveal w-full max-w-[22rem]">{renderStep()}</div>
      </main>

      <footer className="px-4 pb-6 text-center text-xs text-muted-foreground">
        Your apps run in your own AWS account.
      </footer>
    </div>
  );
}

function StepShell({ title, description, children }: { title: string; description?: React.ReactNode; children: React.ReactNode }) {
  return (
    <section>
      <h1 className="text-xl font-semibold tracking-tight text-foreground">{title}</h1>
      {description && <p className="mt-1.5 text-sm text-muted-foreground">{description}</p>}
      <div className="mt-6">{children}</div>
    </section>
  );
}

function Field({ id, label, hint, children }: { id: string; label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>{label}</Label>
      {children}
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
    </div>
  );
}

function BackToLogin({ onClick }: { onClick: () => void }) {
  return (
    <button type="button" onClick={onClick}
      className="inline-flex w-full items-center justify-center gap-1.5 rounded-md py-1 text-xs text-muted-foreground transition-colors hover:text-foreground">
      <ArrowLeft className="size-3.5" /> Back to sign in
    </button>
  );
}

export default function LoginPage() {
  return (
    <Suspense>
      <LoginPageInner />
    </Suspense>
  );
}
