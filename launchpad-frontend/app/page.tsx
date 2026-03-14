'use client';

import { useEffect, useRef } from 'react';
import Link from 'next/link';
import { LogoMark } from '@/components/logo-mark';
import {
  Cloud, GitBranch, Zap, Shield, Terminal, BarChart3,
  ArrowRight, Check, ChevronRight, Minus, Square, X,
} from 'lucide-react';

// Scroll-reveal hook
function useReveal() {
  useEffect(() => {
    const els = document.querySelectorAll('.reveal');
    const io = new IntersectionObserver(
      (entries) => entries.forEach(e => { if (e.isIntersecting) { e.target.classList.add('visible'); io.unobserve(e.target); } }),
      { threshold: 0.12 }
    );
    els.forEach(el => io.observe(el));
    return () => io.disconnect();
  }, []);
}

const NAV_LINKS = ['Features', 'How it works', 'Stack', 'Pricing'];

const FEATURES = [
  {
    icon: <Zap className="w-5 h-5 text-violet-400" />,
    title: 'One-click deploys',
    desc: 'Push to GitHub and Launchpad handles the rest — build, containerize, and ship to AWS ECS in minutes.',
  },
  {
    icon: <Cloud className="w-5 h-5 text-violet-400" />,
    title: 'Managed AWS infra',
    desc: 'VPC, ECS Fargate, ALB, ECR — all provisioned automatically via Terraform. No AWS expertise needed.',
  },
  {
    icon: <GitBranch className="w-5 h-5 text-violet-400" />,
    title: 'Branch deployments',
    desc: 'Deploy any branch or commit hash. Perfect for staging environments and feature previews.',
  },
  {
    icon: <Shield className="w-5 h-5 text-violet-400" />,
    title: 'Role-based access',
    desc: 'Invite teammates with granular roles — admin, user, or guest. Full audit trail on every action.',
  },
  {
    icon: <Terminal className="w-5 h-5 text-violet-400" />,
    title: 'Env var management',
    desc: 'Manage environment variables per app. Changes are injected into the next deployment automatically.',
  },
  {
    icon: <BarChart3 className="w-5 h-5 text-violet-400" />,
    title: 'Real-time status',
    desc: 'Live deployment status — BUILDING → DEPLOYING → ACTIVE. Errors surface with full build logs.',
  },
];

const STEPS = [
  { n: '01', title: 'Connect your AWS account', desc: 'Paste your IAM role ARN. Launchpad assumes it to provision infrastructure on your behalf.' },
  { n: '02', title: 'Provision infrastructure', desc: 'One click creates a full VPC, ECS cluster, ALB, and ECR registry in your chosen region.' },
  { n: '03', title: 'Deploy your app', desc: 'Point to a GitHub repo and branch. CodeBuild builds the Docker image, ECS runs it.' },
  { n: '04', title: 'Ship & iterate', desc: 'Redeploy on every push. Update env vars, scale resources, or sleep idle apps to save cost.' },
];

const STACK = [
  ['Next.js 15', 'Frontend'],
  ['Django + DRF', 'App service'],
  ['AWS ECS Fargate', 'Runtime'],
  ['AWS CodeBuild', 'CI/CD'],
  ['Terraform', 'IaC'],
  ['PostgreSQL', 'Database'],
  ['Redis', 'Queue'],
  ['RabbitMQ', 'Events'],
];

const PLANS = [
  {
    name: 'Starter',
    price: 'Free',
    sub: 'forever',
    features: ['1 infrastructure', '3 applications', 'Community support'],
    cta: 'Get started',
    highlight: false,
  },
  {
    name: 'Pro',
    price: '$29',
    sub: '/ month',
    features: ['Unlimited infrastructures', 'Unlimited applications', 'Priority support', 'Audit logs'],
    cta: 'Start free trial',
    highlight: true,
  },
  {
    name: 'Enterprise',
    price: 'Custom',
    sub: 'contact us',
    features: ['SSO / SAML', 'Dedicated support', 'SLA guarantee', 'Custom regions'],
    cta: 'Contact sales',
    highlight: false,
    href: 'https://mail.google.com/mail/u/0/?fs=1&to=aklamaash78@gmail.com&su=Hello!&tf=cm',
  },
];

export default function LandingPage() {
  useReveal();
  return (
    <div className="min-h-screen bg-[#060606] text-white font-[var(--font-sans)]">

      {/* ── Nav ── */}
      <nav className="fixed top-0 inset-x-0 z-50 border-b border-[#1a1a1a] bg-[#060606]/80 backdrop-blur-md">
        <div className="max-w-6xl mx-auto px-6 h-14 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <LogoMark size={28} />
            <span className="font-semibold text-sm tracking-tight">Launchpad</span>
          </div>
          <div className="hidden md:flex items-center gap-6">
            {NAV_LINKS.map(l => (
              <a key={l} href={`#${l.toLowerCase().replace(/ /g, '-')}`}
                className="text-xs text-[#888] hover:text-white transition-colors">
                {l}
              </a>
            ))}
          </div>
          <Link href="/login"
            className="h-8 px-4 rounded-lg bg-violet-600 hover:bg-violet-700 text-xs font-medium flex items-center gap-1.5 transition-colors">
            Sign in <ArrowRight className="w-3.5 h-3.5" />
          </Link>
        </div>
      </nav>

      {/* ── Hero ── */}
      <section className="pt-32 pb-20 px-6 text-center relative overflow-hidden">
        {/* grid bg */}
        <div className="absolute inset-0 pointer-events-none"
          style={{ backgroundImage: 'linear-gradient(#1a1a1a 1px,transparent 1px),linear-gradient(90deg,#1a1a1a 1px,transparent 1px)', backgroundSize: '48px 48px', opacity: 0.25 }} />
        {/* glow */}
        <div className="absolute top-1/3 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[600px] h-[300px] bg-violet-600/10 rounded-full blur-3xl pointer-events-none animate-glow" />

        <div className="relative max-w-3xl mx-auto stagger">
          <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full border border-violet-500/30 bg-violet-500/10 text-violet-300 text-xs mb-6 animate-fade-up">
            <span className="w-1.5 h-1.5 rounded-full bg-violet-400 animate-pulse" />
            Now in beta — deploy your first app in under 5 minutes
          </div>

          <h1 className="text-4xl md:text-6xl font-bold tracking-tight leading-tight mb-6 animate-fade-up">
            Deploy to AWS<br />
            <span className="text-violet-400">without the complexity</span>
          </h1>
          <p className="text-[#888] text-base md:text-lg max-w-xl mx-auto mb-10 leading-relaxed animate-fade-up">
            Launchpad provisions your AWS infrastructure and deploys your Docker apps automatically.
            From GitHub push to live URL — no DevOps required.
          </p>

          <div className="flex items-center justify-center gap-3 flex-wrap animate-fade-up">
            <Link href="/login"
              className="h-10 px-6 rounded-xl bg-violet-600 hover:bg-violet-700 text-sm font-medium flex items-center gap-2 transition-all duration-200 hover:scale-[1.03] hover:shadow-lg hover:shadow-violet-600/25">
              Start deploying <ArrowRight className="w-4 h-4" />
            </Link>
            <a href="#how-it-works"
              className="h-10 px-6 rounded-xl border border-[#1a1a1a] hover:border-[#333] text-sm text-[#aaa] hover:text-white flex items-center gap-2 transition-all duration-200 hover:scale-[1.02]">
              See how it works <ChevronRight className="w-4 h-4" />
            </a>
          </div>
        </div>

        {/* Hero — dashboard mockup */}
        <div className="relative mt-16 max-w-5xl mx-auto animate-fade-up animate-float" style={{ animationDelay: '300ms' }}>
          <div className="rounded-2xl border border-[#1a1a1a] bg-[#0d0d0d] overflow-hidden shadow-2xl shadow-black/60">
            {/* title bar — proper window controls */}
            <div className="flex items-center gap-1.5 px-4 py-3 border-b border-[#1a1a1a]">
              <button className="w-3 h-3 rounded-full bg-red-500/70 hover:bg-red-500 flex items-center justify-center group transition-colors">
                <X className="w-1.5 h-1.5 text-red-900 opacity-0 group-hover:opacity-100" />
              </button>
              <button className="w-3 h-3 rounded-full bg-amber-500/70 hover:bg-amber-500 flex items-center justify-center group transition-colors">
                <Minus className="w-1.5 h-1.5 text-amber-900 opacity-0 group-hover:opacity-100" />
              </button>
              <button className="w-3 h-3 rounded-full bg-emerald-500/70 hover:bg-emerald-500 flex items-center justify-center group transition-colors">
                <Square className="w-1 h-1 text-emerald-900 opacity-0 group-hover:opacity-100" />
              </button>
              <span className="ml-3 text-xs text-[#444] font-mono">launchpad.app/dashboard</span>
            </div>

            {/* app shell */}
            <div className="flex h-[480px] text-left">
              {/* sidebar */}
              <div className="w-[200px] shrink-0 border-r border-[#1a1a1a] flex flex-col p-3 gap-1">
                <div className="flex items-center gap-2 px-2 py-2 mb-2">
                  <LogoMark size={24} className="shrink-0" />
                  <span className="text-xs font-semibold">Launchpad</span>
                </div>
                {[
                  { label: 'Infrastructures', active: true },
                  { label: 'Applications', active: false },
                  { label: 'Settings', active: false },
                ].map(item => (
                  <div key={item.label}
                    className={`px-2 py-1.5 rounded-lg text-xs flex items-center gap-2 ${item.active ? 'bg-violet-600/10 text-violet-300' : 'text-[#555]'}`}>
                    <span className={`w-1.5 h-1.5 rounded-full ${item.active ? 'bg-violet-400' : 'bg-[#333]'}`} />
                    {item.label}
                  </div>
                ))}
                <div className="mt-auto px-2 py-2 flex items-center gap-2">
                  <div className="w-6 h-6 rounded-full bg-[#1a1a1a] flex items-center justify-center text-[10px] text-[#555]">A</div>
                  <span className="text-[10px] text-[#444]">admin</span>
                  <span className="ml-auto text-[10px] px-1.5 py-0.5 rounded bg-violet-600/20 text-violet-400">super_admin</span>
                </div>
              </div>

              {/* main content */}
              <div className="flex-1 overflow-hidden p-5 flex flex-col gap-4">
                <div className="flex items-center justify-between">
                  <div>
                    <h2 className="text-sm font-semibold">Infrastructures</h2>
                    <p className="text-[10px] text-[#555] mt-0.5">2 environments provisioned</p>
                  </div>
                  <div className="h-7 px-3 rounded-lg bg-violet-600 text-[10px] text-white flex items-center gap-1">
                    <span>+</span> New infrastructure
                  </div>
                </div>

                <div className="grid grid-cols-3 gap-3">
                  {[
                    { label: 'Infrastructures', val: '2', dot: 'bg-emerald-500' },
                    { label: 'Applications', val: '5', dot: 'bg-blue-500 animate-pulse' },
                    { label: 'Deployments', val: '12', dot: 'bg-violet-500' },
                  ].map(s => (
                    <div key={s.label} className="bg-[#0a0a0a] border border-[#1a1a1a] rounded-xl p-3">
                      <div className="flex items-center gap-1.5 mb-1">
                        <span className={`w-1.5 h-1.5 rounded-full ${s.dot}`} />
                        <span className="text-[10px] text-[#555]">{s.label}</span>
                      </div>
                      <span className="text-lg font-bold">{s.val}</span>
                    </div>
                  ))}
                </div>

                <div className="grid grid-cols-2 gap-3 flex-1">
                  {[
                    { name: 'production-us', region: 'us-east-1', apps: 3, dot: 'bg-emerald-500' },
                    { name: 'staging-eu', region: 'eu-west-1', apps: 2, dot: 'bg-emerald-500' },
                  ].map(infra => (
                    <div key={infra.name} className="bg-[#0a0a0a] border border-[#1a1a1a] rounded-xl p-4 flex flex-col gap-3">
                      <div className="flex items-start justify-between">
                        <div>
                          <div className="flex items-center gap-1.5 mb-0.5">
                            <span className={`w-1.5 h-1.5 rounded-full ${infra.dot}`} />
                            <span className="text-xs font-medium">{infra.name}</span>
                          </div>
                          <span className="text-[10px] text-[#555] font-mono">{infra.region}</span>
                        </div>
                        <span className="text-[10px] px-2 py-0.5 rounded-full bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">ACTIVE</span>
                      </div>
                      <div className="flex gap-2 flex-wrap">
                        {Array.from({ length: infra.apps }).map((_, i) => (
                          <div key={i} className="h-6 px-2 rounded-md bg-[#111] border border-[#1a1a1a] text-[10px] text-[#555] flex items-center gap-1">
                            <span className="w-1 h-1 rounded-full bg-blue-500 animate-pulse" />
                            app-{i + 1}
                          </div>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>

                <div className="bg-[#0a0a0a] border border-[#1a1a1a] rounded-xl p-3">
                  <p className="text-[10px] text-[#555] mb-2 uppercase tracking-widest font-mono">Recent deployments</p>
                  <div className="space-y-1.5">
                    {[
                      { app: 'api-service', status: 'ACTIVE', time: '2m ago', dot: 'bg-emerald-500' },
                      { app: 'frontend-app', status: 'BUILDING', time: '5m ago', dot: 'bg-blue-500 animate-pulse' },
                      { app: 'worker-service', status: 'ACTIVE', time: '12m ago', dot: 'bg-emerald-500' },
                    ].map(d => (
                      <div key={d.app} className="flex items-center gap-2">
                        <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${d.dot}`} />
                        <span className="text-[10px] font-mono text-[#aaa] flex-1">{d.app}</span>
                        <span className="text-[10px] text-[#555]">{d.time}</span>
                        <span className={`text-[10px] px-1.5 py-0.5 rounded font-mono ${d.status === 'ACTIVE' ? 'text-emerald-400 bg-emerald-500/10' : 'text-blue-400 bg-blue-500/10'}`}>{d.status}</span>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* ── Features ── */}
      <section id="features" className="py-24 px-6">
        <div className="max-w-6xl mx-auto">
          <div className="text-center mb-14 reveal">
            <p className="text-xs uppercase tracking-widest text-violet-400 font-mono mb-3">Features</p>
            <h2 className="text-3xl font-bold tracking-tight">Everything you need to ship</h2>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4 stagger">
            {FEATURES.map(f => (
              <div key={f.title} className="bg-[#0d0d0d] border border-[#1a1a1a] rounded-xl p-5 hover:border-violet-500/30 hover:-translate-y-1 transition-all duration-200 reveal">
                <div className="w-9 h-9 rounded-lg bg-violet-600/10 border border-violet-500/20 flex items-center justify-center mb-4">
                  {f.icon}
                </div>
                <h3 className="text-sm font-semibold mb-2">{f.title}</h3>
                <p className="text-xs text-[#666] leading-relaxed">{f.desc}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ── How it works ── */}
      <section id="how-it-works" className="py-24 px-6 border-t border-[#1a1a1a]">
        <div className="max-w-4xl mx-auto">
          <div className="text-center mb-14 reveal">
            <p className="text-xs uppercase tracking-widest text-violet-400 font-mono mb-3">How it works</p>
            <h2 className="text-3xl font-bold tracking-tight">From zero to deployed in 4 steps</h2>
          </div>
          <div className="space-y-4 stagger">
            {STEPS.map((s, i) => (
              <div key={s.n} className="flex gap-5 items-start bg-[#0d0d0d] border border-[#1a1a1a] rounded-xl p-5 hover:border-violet-500/20 transition-all duration-200 reveal">
                <span className="text-2xl font-bold text-[#222] font-mono shrink-0 w-10">{s.n}</span>
                <div>
                  <h3 className="text-sm font-semibold mb-1">{s.title}</h3>
                  <p className="text-xs text-[#666] leading-relaxed">{s.desc}</p>
                </div>
                {i < STEPS.length - 1 && (
                  <div className="ml-auto shrink-0 self-center">
                    <ChevronRight className="w-4 h-4 text-[#333]" />
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ── Stack ── */}
      <section id="stack" className="py-24 px-6 border-t border-[#1a1a1a]">
        <div className="max-w-4xl mx-auto text-center">
          <p className="text-xs uppercase tracking-widest text-violet-400 font-mono mb-3 reveal">Stack</p>
          <h2 className="text-3xl font-bold tracking-tight mb-12 reveal">Built on battle-tested tech</h2>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 stagger">
            {STACK.map(([name, role]) => (
              <div key={name} className="bg-[#0d0d0d] border border-[#1a1a1a] rounded-xl px-4 py-4 text-left hover:border-violet-500/20 hover:-translate-y-0.5 transition-all duration-200 reveal">
                <p className="text-sm font-medium mb-0.5">{name}</p>
                <p className="text-xs text-[#555]">{role}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ── Pricing ── */}
      <section id="pricing" className="py-24 px-6 border-t border-[#1a1a1a]">
        <div className="max-w-5xl mx-auto">
          <div className="text-center mb-14 reveal">
            <p className="text-xs uppercase tracking-widest text-violet-400 font-mono mb-3">Pricing</p>
            <h2 className="text-3xl font-bold tracking-tight">Simple, transparent pricing</h2>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4 stagger">
            {PLANS.map(p => (
              <div key={p.name}
                className={`rounded-xl border p-6 flex flex-col hover:-translate-y-1 transition-all duration-200 reveal ${p.highlight ? 'border-violet-500/50 bg-violet-600/5 shadow-lg shadow-violet-600/10' : 'border-[#1a1a1a] bg-[#0d0d0d]'}`}>
                <p className="text-xs text-[#666] mb-1">{p.name}</p>
                <div className="flex items-baseline gap-1 mb-1">
                  <span className="text-3xl font-bold">{p.price}</span>
                  <span className="text-xs text-[#555]">{p.sub}</span>
                </div>
                <div className="my-5 border-t border-[#1a1a1a]" />
                <ul className="space-y-2.5 flex-1 mb-6">
                  {p.features.map(f => (
                    <li key={f} className="flex items-center gap-2 text-xs text-[#aaa]">
                      <Check className="w-3.5 h-3.5 text-violet-400 shrink-0" />
                      {f}
                    </li>
                  ))}
                </ul>
                <a href={(p as any).href ?? '/login'}
                  className={`h-9 rounded-lg text-xs font-medium flex items-center justify-center transition-colors ${p.highlight ? 'bg-violet-600 hover:bg-violet-700 text-white' : 'border border-[#1a1a1a] hover:border-[#333] text-[#aaa] hover:text-white'}`}>
                  {p.cta}
                </a>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ── CTA ── */}
      <section className="py-24 px-6 border-t border-[#1a1a1a]">
        <div className="max-w-2xl mx-auto text-center reveal">
          <h2 className="text-3xl font-bold tracking-tight mb-4">Ready to launch?</h2>
          <p className="text-[#666] text-sm mb-8">
            Connect your AWS account and deploy your first app in under 5 minutes.
          </p>
          <Link href="/login"
            className="inline-flex h-10 px-8 rounded-xl bg-violet-600 hover:bg-violet-700 text-sm font-medium items-center gap-2 transition-all duration-200 hover:scale-[1.04] hover:shadow-lg hover:shadow-violet-600/30">
            Get started for free <ArrowRight className="w-4 h-4" />
          </Link>
        </div>
      </section>

      {/* ── Footer ── */}
      <footer className="border-t border-[#1a1a1a] py-8 px-6">
        <div className="max-w-6xl mx-auto flex flex-col sm:flex-row items-center justify-between gap-4">
          <div className="flex items-center gap-2">
            <LogoMark size={24} />
            <span className="text-xs font-medium">Launchpad</span>
          </div>
          <p className="text-xs text-[#444]">
            © {new Date().getFullYear()} Launchpad. Built by{' '}
            <span className="text-[#666]">Mohamed Aklamaash</span>. All rights reserved.
          </p>
          <Link href="/login" className="text-xs text-[#555] hover:text-white transition-colors">
            Sign in →
          </Link>
        </div>
      </footer>

    </div>
  );
}
