'use client';

import { Suspense, useEffect, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Github } from 'lucide-react';
import { LogoMark } from '@/components/logo-mark';
import { useAuthStore } from '@/lib/store/auth';
import { authApi } from '@/lib/api/auth';
import { toast } from 'sonner';

type CredsStep = 'login' | 'otp' | 'forgot' | 'verify-reset' | 'reset-done';

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
    } catch (err: any) {
      const msg: string = err.response?.data?.error || err.response?.data?.message || '';
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
    } catch (err: any) {
      toast.error(err.response?.data?.error || 'Invalid or expired OTP');
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
    } catch (err: any) {
      toast.error(err.response?.data?.error || 'Failed to send reset OTP');
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
    } catch (err: any) {
      toast.error(err.response?.data?.error || 'Invalid OTP');
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
    } catch (err: any) {
      toast.error(err.response?.data?.error || 'Failed to reset password');
    } finally {
      setLoading(false);
    }
  };

  const renderCredsContent = () => {
    if (step === 'otp') return (
      <form onSubmit={handleVerifyOtp} className="space-y-4">
        <p className="text-xs text-[#444]">
          Check your email <span className="text-[#888] font-mono">{email}</span> for the OTP.
        </p>
        <div className="space-y-1.5">
          <Label className="text-[#444] text-[10px] uppercase tracking-widest font-mono">OTP Code</Label>
          <Input placeholder="000000" value={otp} onChange={(e) => setOtp(e.target.value)}
            required maxLength={6}
            className="bg-[#050505] border-[#1a1a1a] focus:border-violet-500 h-10 font-mono text-lg tracking-[0.5em] text-center" />
        </div>
        <Button type="submit" disabled={loading} className="w-full bg-violet-600 hover:bg-violet-700 h-10 text-sm font-medium">
          {loading ? 'Verifying…' : 'Verify & Sign In'}
        </Button>
        <button type="button" onClick={() => { setStep('login'); setOtp(''); }}
          className="w-full text-xs text-[#333] hover:text-[#666] transition-colors">
          ← Back to login
        </button>
      </form>
    );

    if (step === 'forgot') return (
      <form onSubmit={handleForgotPassword} className="space-y-4">
        <p className="text-xs text-[#444]">Enter your email to receive a password reset OTP.</p>
        <div className="space-y-1.5">
          <Label className="text-[#444] text-[10px] uppercase tracking-widest font-mono">Email</Label>
          <Input type="email" placeholder="you@example.com" value={email}
            onChange={(e) => setEmail(e.target.value)} required
            className="bg-[#050505] border-[#1a1a1a] focus:border-violet-500 h-10 text-sm" />
        </div>
        <Button type="submit" disabled={loading} className="w-full bg-violet-600 hover:bg-violet-700 h-10 text-sm font-medium">
          {loading ? 'Sending…' : 'Send Reset OTP'}
        </Button>
        <button type="button" onClick={() => setStep('login')}
          className="w-full text-xs text-[#333] hover:text-[#666] transition-colors">
          ← Back to login
        </button>
      </form>
    );

    if (step === 'verify-reset') return (
      <form onSubmit={handleVerifyResetOtp} className="space-y-4">
        <p className="text-xs text-[#444]">Enter the OTP sent to <span className="text-[#888] font-mono">{email}</span>.</p>
        <div className="space-y-1.5">
          <Label className="text-[#444] text-[10px] uppercase tracking-widest font-mono">Reset OTP</Label>
          <Input placeholder="000000" value={otp} onChange={(e) => setOtp(e.target.value)}
            required maxLength={6}
            className="bg-[#050505] border-[#1a1a1a] focus:border-violet-500 h-10 font-mono text-lg tracking-[0.5em] text-center" />
        </div>
        <Button type="submit" disabled={loading} className="w-full bg-violet-600 hover:bg-violet-700 h-10 text-sm font-medium">
          {loading ? 'Verifying…' : 'Verify OTP'}
        </Button>
      </form>
    );

    if (step === 'reset-done') return (
      <form onSubmit={handleResetPassword} className="space-y-4">
        <p className="text-xs text-[#444]">Set your new password.</p>
        <div className="space-y-1.5">
          <Label className="text-[#444] text-[10px] uppercase tracking-widest font-mono">New Password</Label>
          <Input type="password" placeholder="Min 6 characters" value={newPassword}
            onChange={(e) => setNewPassword(e.target.value)} required minLength={6}
            className="bg-[#050505] border-[#1a1a1a] focus:border-violet-500 h-10 text-sm" />
        </div>
        <Button type="submit" disabled={loading} className="w-full bg-violet-600 hover:bg-violet-700 h-10 text-sm font-medium">
          {loading ? 'Resetting…' : 'Reset Password'}
        </Button>
      </form>
    );

    // default: login
    return (
      <form onSubmit={handleLogin} className="space-y-3">
        <div className="space-y-1.5">
          <Label className="text-[#444] text-[10px] uppercase tracking-widest font-mono">Email</Label>
          <Input type="email" placeholder="you@example.com" value={email}
            onChange={(e) => setEmail(e.target.value)} required
            className="bg-[#050505] border-[#1a1a1a] focus:border-violet-500 h-10 text-sm" />
        </div>
        <div className="space-y-1.5">
          <Label className="text-[#444] text-[10px] uppercase tracking-widest font-mono">Password</Label>
          <Input type="password" placeholder="••••••••" value={password}
            onChange={(e) => setPassword(e.target.value)} required
            className="bg-[#050505] border-[#1a1a1a] focus:border-violet-500 h-10 text-sm" />
        </div>
        <Button type="submit" disabled={loading} className="w-full bg-violet-600 hover:bg-violet-700 h-10 text-sm font-medium mt-1">
          {loading ? 'Signing in…' : 'Sign In'}
        </Button>
        <button type="button" onClick={() => setStep('forgot')}
          className="w-full text-xs text-[#333] hover:text-[#666] transition-colors">
          Forgot password?
        </button>
      </form>
    );
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-[#060606]">
      {/* Subtle grid background */}
      <div className="fixed inset-0 bg-[linear-gradient(rgba(255,255,255,0.015)_1px,transparent_1px),linear-gradient(90deg,rgba(255,255,255,0.015)_1px,transparent_1px)] bg-[size:64px_64px] pointer-events-none" />
      <div className="w-full max-w-sm px-4 relative">
        <div className="text-center mb-8">
          <div className="inline-flex items-center justify-center mb-4">
            <LogoMark size={44} />
          </div>
          <h1 className="text-2xl font-semibold tracking-tight text-white mb-1">Launchpad</h1>
          <p className="text-xs text-[#444] font-mono tracking-widest uppercase">Cloud infrastructure</p>
        </div>

        <div className="bg-[#0a0a0a] border border-[#1a1a1a] rounded-2xl overflow-hidden shadow-2xl shadow-black/60">
          <div className="flex border-b border-[#1a1a1a]">
            {(['github', 'creds'] as const).map((t) => (
              <button key={t} onClick={() => { setTab(t); setStep('login'); }}
                className={`flex-1 py-3 text-xs font-medium transition-all ${
                  tab === t ? 'text-white border-b border-violet-500 bg-violet-500/5' : 'text-[#444] hover:text-[#888]'
                }`}>
                {t === 'github' ? 'GitHub' : 'Credentials'}
              </button>
            ))}
          </div>
          <div className="p-5">
            {tab === 'github' ? (
              <div className="space-y-4">
                <Button onClick={() => authApi.githubLogin()} className="w-full bg-white hover:bg-gray-100 text-black font-medium h-10 text-sm gap-2">
                  <Github className="h-4 w-4" />
                  Continue with GitHub
                </Button>
                <p className="text-[11px] text-[#333] text-center font-mono">
                  GitHub users receive <span className="text-violet-400">SUPER_ADMIN</span> access
                </p>
              </div>
            ) : renderCredsContent()}
          </div>
        </div>
      </div>
    </div>
  );
}

export default function LoginPage() {
  return (
    <Suspense>
      <LoginPageInner />
    </Suspense>
  );
}
