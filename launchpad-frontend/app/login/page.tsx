'use client';

import { Suspense, useEffect, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { motion } from 'framer-motion';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Github, Mail, Lock, KeyRound, ArrowLeft, Rocket, ShieldCheck, Zap } from 'lucide-react';
import { LogoMark } from '@/components/logo-mark';
import { useAuthStore } from '@/lib/store/auth';
import { authApi } from '@/lib/api/auth';
import { toast } from 'sonner';

type CredsStep = 'login' | 'otp' | 'forgot' | 'verify-reset' | 'reset-done';

const rise = { initial: { opacity: 0, y: 14 }, animate: { opacity: 1, y: 0, transition: { duration: 0.4, ease: [0.16, 1, 0.3, 1] as const } } };

function LoginPageInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const setAuth = useAuthStore((s) => s.setAuth);

  const prefillEmail = searchParams.get('email') || '';

  const [tab, setTab] = useState<'github' | 'creds'>(prefillEmail ? 'creds' : 'github');
  const [step, setStep] = useState<CredsStep>('login');
  const [email, setEmail] = useState(prefillEmail);
  const [password, setPassword] = useState('');
  const [otp, setOtp] = useState('');
  const [resetToken, setResetToken] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (isAuthenticated) router.push('/dashboard');
    const error = searchParams.get('error');
    if (error) toast.error(decodeURIComponent(error));
  }, [isAuthenticated, router, searchParams]);

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    try {
      const res = await authApi.credentialsLogin(email, password);
      setAuth(res.user, res.access_token, res.refresh_token);
      router.push('/dashboard');
    } catch (err: unknown) {
      const error = err as { response?: { data?: { error?: string; message?: string } } };
      const msg: string = error.response?.data?.error || error.response?.data?.message || '';
      if (msg.toLowerCase().includes('otp pending')) {
        toast.info('Verify your email OTP to continue');
        setStep('otp');
      } else {
        toast.error(msg || 'Invalid credentials');
      }
    } finally {
      setLoading(false);
    }
  };

  const handleVerifyOtp = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    try {
      const res = await authApi.verifyOtp(email, otp);
      setAuth(res.user, res.access_token, res.refresh_token);
      router.push('/dashboard');
    } catch (err: unknown) {
      const error = err as { response?: { data?: { error?: string } } };
      toast.error(error.response?.data?.error || 'Invalid or expired OTP');
    } finally {
      setLoading(false);
    }
  };

  const handleForgotPassword = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    try {
      await authApi.forgotPassword(email);
      toast.success('Reset OTP sent to your email');
      setStep('verify-reset');
    } catch (err: unknown) {
      const error = err as { response?: { data?: { error?: string } } };
      toast.error(error.response?.data?.error || 'Failed to send reset OTP');
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
      toast.error(error.response?.data?.error || 'Invalid OTP');
    } finally {
      setLoading(false);
    }
  };

  const handleResetPassword = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    try {
      await authApi.resetPassword(resetToken, newPassword);
      toast.success('Password reset! Please log in.');
      setStep('login');
      setNewPassword('');
    } catch (err: unknown) {
      const error = err as { response?: { data?: { error?: string } } };
      toast.error(error.response?.data?.error || 'Failed to reset password');
    } finally {
      setLoading(false);
    }
  };

  const renderCredsContent = () => {
    if (step === 'otp') return (
      <form onSubmit={handleVerifyOtp} className="space-y-4">
        <p className="text-xs text-muted-foreground">
          Check your email <span className="text-foreground font-mono">{email}</span> for the OTP.
        </p>
        <Field icon={<KeyRound className="w-3.5 h-3.5" />} label="OTP Code">
          <Input placeholder="000000" value={otp} onChange={(e) => setOtp(e.target.value)}
            required maxLength={6}
            className="bg-transparent border-0 h-9 font-mono text-lg tracking-[0.5em] text-center placeholder:text-muted-foreground/50 focus-visible:ring-0 pl-0" />
        </Field>
        <Button type="submit" size="lg" disabled={loading} className="w-full">
          {loading ? 'Verifying…' : 'Verify & Sign In'}
        </Button>
        <BackToLogin onClick={() => { setStep('login'); setOtp(''); }} />
      </form>
    );

    if (step === 'forgot') return (
      <form onSubmit={handleForgotPassword} className="space-y-4">
        <p className="text-xs text-muted-foreground">Enter your email to receive a password reset OTP.</p>
        <Field icon={<Mail className="w-3.5 h-3.5" />} label="Email">
          <Input type="email" placeholder="you@example.com" value={email}
            onChange={(e) => setEmail(e.target.value)} required
            className="bg-transparent border-0 h-9 text-sm placeholder:text-muted-foreground/50 focus-visible:ring-0 pl-0" />
        </Field>
        <Button type="submit" size="lg" disabled={loading} className="w-full">
          {loading ? 'Sending…' : 'Send Reset OTP'}
        </Button>
        <BackToLogin onClick={() => setStep('login')} />
      </form>
    );

    if (step === 'verify-reset') return (
      <form onSubmit={handleVerifyResetOtp} className="space-y-4">
        <p className="text-xs text-muted-foreground">Enter the OTP sent to <span className="text-foreground font-mono">{email}</span>.</p>
        <Field icon={<KeyRound className="w-3.5 h-3.5" />} label="Reset OTP">
          <Input placeholder="000000" value={otp} onChange={(e) => setOtp(e.target.value)}
            required maxLength={6}
            className="bg-transparent border-0 h-9 font-mono text-lg tracking-[0.5em] text-center placeholder:text-muted-foreground/50 focus-visible:ring-0 pl-0" />
        </Field>
        <Button type="submit" size="lg" disabled={loading} className="w-full">
          {loading ? 'Verifying…' : 'Verify OTP'}
        </Button>
      </form>
    );

    if (step === 'reset-done') return (
      <form onSubmit={handleResetPassword} className="space-y-4">
        <p className="text-xs text-muted-foreground">Set your new password.</p>
        <Field icon={<Lock className="w-3.5 h-3.5" />} label="New Password">
          <Input type="password" placeholder="Min 6 characters" value={newPassword}
            onChange={(e) => setNewPassword(e.target.value)} required minLength={6}
            className="bg-transparent border-0 h-9 text-sm placeholder:text-muted-foreground/50 focus-visible:ring-0 pl-0" />
        </Field>
        <Button type="submit" size="lg" disabled={loading} className="w-full">
          {loading ? 'Resetting…' : 'Reset Password'}
        </Button>
      </form>
    );

    return (
      <form onSubmit={handleLogin} className="space-y-4">
        <div className="space-y-1">
          <Field icon={<Mail className="w-3.5 h-3.5" />} label="Email">
            <Input type="email" placeholder="you@example.com" value={email}
              onChange={(e) => setEmail(e.target.value)} required
              className="bg-transparent border-0 h-9 text-sm placeholder:text-muted-foreground/50 focus-visible:ring-0 pl-0" />
          </Field>
          <Field icon={<Lock className="w-3.5 h-3.5" />} label="Password">
            <Input type="password" placeholder="••••••••" value={password}
              onChange={(e) => setPassword(e.target.value)} required
              className="bg-transparent border-0 h-9 text-sm placeholder:text-muted-foreground/50 focus-visible:ring-0 pl-0" />
          </Field>
        </div>
        <Button type="submit" size="lg" disabled={loading} className="w-full">
          {loading ? 'Signing in…' : 'Sign In'}
        </Button>
        <button type="button" onClick={() => setStep('forgot')}
          className="w-full text-xs text-muted-foreground hover:text-foreground transition-colors">
          Forgot password?
        </button>
      </form>
    );
  };

  return (
    <div className="min-h-screen bg-background lg:grid lg:grid-cols-[1.05fr_1fr]">
      <BrandPanel />

      <main className="relative flex items-center justify-center overflow-hidden px-4 py-12">
        <div className="brand-glow pointer-events-none absolute inset-0 lg:hidden" />
        <motion.div {...rise} className="relative w-full max-w-sm">
          <div className="mb-8 flex flex-col items-center text-center lg:hidden">
            <div className="mb-3 inline-flex items-center gap-2.5">
              <LogoMark size={38} />
              <span className="font-display text-2xl font-semibold tracking-tight text-foreground">Launchpad</span>
            </div>
            <span className="eyebrow">Cloud Infrastructure</span>
          </div>

          <div className="mb-6 hidden lg:block">
            <span className="eyebrow">Sign in</span>
            <h1 className="mt-2 font-display text-2xl font-semibold tracking-tight text-foreground">Welcome back</h1>
            <p className="mt-1 text-sm text-muted-foreground">Pick up where your deploys left off.</p>
          </div>

          <div className="panel overflow-hidden rounded-xl">
            <div className="flex border-b border-hairline">
              {(['github', 'creds'] as const).map((t) => (
                <button key={t} onClick={() => { setTab(t); setStep('login'); }}
                  className={`relative flex-1 py-3 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60 ${tab === t
                    ? 'text-foreground'
                    : 'text-muted-foreground hover:text-foreground'
                    }`}>
                  {t === 'github' ? 'GitHub' : 'Credentials'}
                  {tab === t && (
                    <motion.span layoutId="login-tab" className="absolute inset-0 -z-10 border-b border-brand bg-brand-soft" transition={{ type: 'spring', stiffness: 400, damping: 32 }} />
                  )}
                </button>
              ))}
            </div>
            <div className="p-5">
              {tab === 'github' ? (
                <div className="space-y-4">
                  <Button onClick={() => authApi.githubLogin()} size="lg" className="w-full gap-2">
                    <Github className="h-4 w-4" />
                    Continue with GitHub
                  </Button>
                  <p className="text-center font-mono text-[11px] text-muted-foreground">
                    GitHub users receive <span className="text-brand">SUPER_ADMIN</span> access
                  </p>
                </div>
              ) : renderCredsContent()}
            </div>
          </div>

          <p className="mt-6 flex items-center justify-center gap-1.5 text-center text-[11px] text-muted-foreground/70">
            <Lock className="h-3 w-3" /> Credentials are encrypted in transit
          </p>
        </motion.div>
      </main>
    </div>
  );
}

const panelStagger = {
  animate: { transition: { staggerChildren: 0.12, delayChildren: 0.1 } },
};
const panelItem = {
  initial: { opacity: 0, y: 16 },
  animate: { opacity: 1, y: 0, transition: { duration: 0.5, ease: [0.16, 1, 0.3, 1] as const } },
};

const BULLETS = [
  { icon: Rocket, title: 'Push to ship', desc: 'Git push builds the image and rolls it out on ECS Fargate.' },
  { icon: ShieldCheck, title: 'Your account, your keys', desc: 'Everything runs in the AWS account you own. No lock-in.' },
  { icon: Zap, title: 'Live in minutes', desc: 'A full VPC, load balancer, and registry — provisioned for you.' },
];

function BrandPanel() {
  return (
    <aside className="relative hidden flex-col justify-between overflow-hidden border-r border-hairline p-12 lg:flex">
      <div className="brand-glow pointer-events-none absolute inset-0" />
      <div
        className="pointer-events-none absolute inset-0"
        style={{
          backgroundImage: 'linear-gradient(oklch(1 0 0 / 4%) 1px,transparent 1px),linear-gradient(90deg,oklch(1 0 0 / 4%) 1px,transparent 1px)',
          backgroundSize: '46px 46px',
          maskImage: 'radial-gradient(120% 80% at 20% 0%, #000 30%, transparent 75%)',
        }}
      />
      <motion.div variants={panelStagger} initial="initial" animate="animate" className="relative">
        <motion.div variants={panelItem} className="flex items-center gap-2.5">
          <LogoMark size={34} />
          <span className="font-display text-xl font-semibold tracking-tight text-foreground">Launchpad</span>
        </motion.div>
      </motion.div>

      <motion.div variants={panelStagger} initial="initial" animate="animate" className="relative max-w-md">
        <motion.span variants={panelItem} className="eyebrow">Cloud infrastructure, on autopilot</motion.span>
        <motion.h2 variants={panelItem} className="mt-3 font-display text-3xl font-semibold leading-tight tracking-tight text-foreground">
          Deploy to AWS in minutes,<br />not sprints.
        </motion.h2>
        <motion.ul variants={panelStagger} className="mt-8 space-y-5">
          {BULLETS.map((b) => (
            <motion.li key={b.title} variants={panelItem} className="flex gap-3.5">
              <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-brand/20 bg-brand-soft text-brand">
                <b.icon className="h-4 w-4" />
              </span>
              <div>
                <p className="text-sm font-medium text-foreground">{b.title}</p>
                <p className="mt-0.5 text-xs leading-relaxed text-muted-foreground">{b.desc}</p>
              </div>
            </motion.li>
          ))}
        </motion.ul>

        <motion.div variants={panelItem} className="mt-10 rounded-xl panel-inset p-4 font-mono text-xs">
          <div className="mb-3 flex gap-1.5">
            <span className="h-2.5 w-2.5 rounded-full bg-destructive/70" />
            <span className="h-2.5 w-2.5 rounded-full bg-warning/70" />
            <span className="h-2.5 w-2.5 rounded-full bg-success/70" />
          </div>
          <p className="text-muted-foreground">$ <span className="text-foreground">git push origin main</span></p>
          <p className="mt-1.5 text-brand">→ building image</p>
          <p className="text-azure">→ pushing to ECR</p>
          <p className="flex items-center gap-1.5 text-success">
            ✓ live at api.your-app.dev
            <motion.span
              className="inline-block h-3 w-1.5 bg-success"
              animate={{ opacity: [1, 1, 0, 0] }}
              transition={{ duration: 1, repeat: Infinity, ease: 'linear' }}
            />
          </p>
        </motion.div>
      </motion.div>

      <motion.div variants={panelStagger} initial="initial" animate="animate" className="relative">
        <motion.p variants={panelItem} className="flex items-center gap-2 text-xs text-muted-foreground/70">
          <span className="relative flex h-1.5 w-1.5">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-success opacity-75" />
            <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-success" />
          </span>
          All systems operational
        </motion.p>
      </motion.div>
    </aside>
  );
}

function Field({ icon, label, children }: { icon: React.ReactNode; label: string; children: React.ReactNode }) {
  return (
    <div className="group bg-surface-1 border border-hairline px-4 py-2.5 transition-colors focus-within:border-brand/40 focus-within:bg-surface-2 first:rounded-t-xl last:rounded-b-xl">
      <div className="flex items-center gap-2 mb-0.5">
        <span className="text-muted-foreground/70 group-focus-within:text-brand transition-colors">{icon}</span>
        <span className="eyebrow">{label}</span>
      </div>
      {children}
    </div>
  );
}

function BackToLogin({ onClick }: { onClick: () => void }) {
  return (
    <button type="button" onClick={onClick}
      className="w-full inline-flex items-center justify-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors">
      <ArrowLeft className="w-3.5 h-3.5" /> Back to login
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
