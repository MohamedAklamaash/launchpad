'use client';

import { useEffect, useRef } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { ReactLenis } from 'lenis/react';
import {
  motion, useScroll, useTransform, useSpring, useMotionValue,
  useReducedMotion, useInView, animate,
} from 'framer-motion';
import { LogoMark } from '@/components/logo-mark';
import {
  Cloud, GitBranch, Zap, Shield, Terminal, BarChart3,
  ArrowRight, Check, ChevronRight, X,
} from 'lucide-react';

const EASE = [0.16, 1, 0.3, 1] as const;

const NAV_LINKS = ['Features', 'How it works', 'Stack', 'Pricing'];

const FEATURES = [
  {
    icon: Zap,
    title: 'One command to production',
    desc: 'Push to a branch. Launchpad builds the image, pushes it to ECR, and rolls it out on ECS Fargate — no pipeline to wire up.',
  },
  {
    icon: Cloud,
    title: 'Infra in your own account',
    desc: 'VPC, Fargate, load balancer, and registry, provisioned by Terraform under a role you control. Walk away and every resource is still yours.',
  },
  {
    icon: GitBranch,
    title: 'A URL for every branch',
    desc: 'Deploy any branch or commit for staging and previews, then tear it down when the PR merges. No shared-environment traffic jams.',
  },
  {
    icon: Shield,
    title: 'Roles, and a paper trail',
    desc: 'Invite teammates as admin, user, or guest. Every deploy and every change is attributed and logged — nothing happens off the record.',
  },
  {
    icon: Terminal,
    title: 'Env vars without the rebuild',
    desc: 'Set variables per app in the dashboard. They are injected on the next deploy — no image rebuild, no redeploy dance.',
  },
  {
    icon: BarChart3,
    title: 'Status you can actually read',
    desc: 'Watch BUILDING to DEPLOYING to ACTIVE as it happens. When a build breaks, the logs are one click away, not buried in CloudWatch.',
  },
];

const STATS: { to?: number; suffix?: string; display?: string; label: string }[] = [
  { to: 5, suffix: '', label: 'Minutes to your first deploy' },
  { to: 100, suffix: '%', label: 'Runs in the AWS account you own' },
  { to: 9, suffix: '', label: 'AWS services provisioned for you' },
];

const STEPS = [
  { n: '01', title: 'Point us at your AWS account', desc: 'Run one script to create a scoped IAM role. Launchpad assumes it — you never hand over a key.' },
  { n: '02', title: 'Provision an environment', desc: 'One click stands up a full VPC, ECS cluster, load balancer, and registry in the region you pick.' },
  { n: '03', title: 'Connect a repo and deploy', desc: 'Choose a GitHub repo and branch. CodeBuild builds the Docker image; ECS runs it behind your ALB.' },
  { n: '04', title: 'Ship on every push', desc: 'Redeploy automatically on commit. Update env vars, resize, or sleep idle apps to stop paying for them.' },
];

const STACK = [
  ['Next.js', 'Dashboard'],
  ['Django + DRF', 'Control plane'],
  ['ECS Fargate', 'Runtime'],
  ['AWS CodeBuild', 'Builds'],
  ['Terraform', 'Provisioning'],
  ['PostgreSQL', 'System of record'],
  ['Redis', 'Deploy queue'],
  ['RabbitMQ', 'Event bus'],
];

interface Plan {
  name: string;
  price: string;
  sub: string;
  features: string[];
  cta: string;
  highlight: boolean;
  href?: string;
}

const PLANS: Plan[] = [
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
    sub: 'let’s talk',
    features: ['SSO / SAML', 'Dedicated support', 'SLA guarantee', 'Custom regions'],
    cta: 'Contact sales',
    highlight: false,
    href: 'https://mail.google.com/mail/u/0/?fs=1&to=aklamaash78@gmail.com&su=Hello!&tf=cm',
  },
];

function Reveal({
  children, className, delay = 0, y = 24,
}: { children: React.ReactNode; className?: string; delay?: number; y?: number }) {
  const reduce = useReducedMotion();
  return (
    <motion.div
      className={className}
      initial={{ opacity: 0, y: reduce ? 0 : y }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, amount: 0.3 }}
      transition={{ duration: 0.6, delay, ease: EASE }}
    >
      {children}
    </motion.div>
  );
}

function MagneticCta({
  children, href, className,
}: { children: React.ReactNode; href: string; className?: string }) {
  const reduce = useReducedMotion();
  const ref = useRef<HTMLAnchorElement>(null);
  const mvX = useMotionValue(0);
  const mvY = useMotionValue(0);
  const x = useSpring(mvX, { stiffness: 300, damping: 20, mass: 0.5 });
  const y = useSpring(mvY, { stiffness: 300, damping: 20, mass: 0.5 });

  const onMove = (e: React.PointerEvent<HTMLAnchorElement>) => {
    if (reduce || !ref.current) return;
    const r = ref.current.getBoundingClientRect();
    mvX.set((e.clientX - (r.left + r.width / 2)) * 0.35);
    mvY.set((e.clientY - (r.top + r.height / 2)) * 0.35);
  };
  const reset = () => { mvX.set(0); mvY.set(0); };

  return (
    <motion.a
      ref={ref}
      href={href}
      onPointerMove={onMove}
      onPointerLeave={reset}
      style={{ x, y }}
      whileTap={{ scale: 0.96 }}
      className={className}
    >
      {children}
    </motion.a>
  );
}

function TiltCard({ children, className }: { children: React.ReactNode; className?: string }) {
  const reduce = useReducedMotion();
  const ref = useRef<HTMLDivElement>(null);
  const rx = useMotionValue(0);
  const ry = useMotionValue(0);
  const rotateX = useSpring(rx, { stiffness: 200, damping: 18 });
  const rotateY = useSpring(ry, { stiffness: 200, damping: 18 });

  const onMove = (e: React.PointerEvent<HTMLDivElement>) => {
    if (reduce || !ref.current) return;
    const r = ref.current.getBoundingClientRect();
    const px = (e.clientX - r.left) / r.width - 0.5;
    const py = (e.clientY - r.top) / r.height - 0.5;
    ry.set(px * 7);
    rx.set(-py * 7);
  };
  const reset = () => { rx.set(0); ry.set(0); };

  return (
    <motion.div
      ref={ref}
      onPointerMove={onMove}
      onPointerLeave={reset}
      style={{ rotateX, rotateY, transformPerspective: 900 }}
      className={className}
    >
      {children}
    </motion.div>
  );
}

function Counter({ to, suffix = '' }: { to: number; suffix?: string }) {
  const reduce = useReducedMotion();
  const ref = useRef<HTMLSpanElement>(null);
  const inView = useInView(ref, { once: true, amount: 0.6 });

  useEffect(() => {
    if (!inView || !ref.current) return;
    if (reduce) { ref.current.textContent = `${to}${suffix}`; return; }
    const controls = animate(0, to, {
      duration: 1.4,
      ease: EASE,
      onUpdate: (v) => { if (ref.current) ref.current.textContent = `${Math.round(v)}${suffix}`; },
    });
    return () => controls.stop();
  }, [inView, to, suffix, reduce]);

  return <span ref={ref}>0{suffix}</span>;
}

function StackMarquee() {
  const reduce = useReducedMotion();
  const row = [...STACK, ...STACK];
  return (
    <div className="relative overflow-hidden [mask-image:linear-gradient(90deg,transparent,#000_12%,#000_88%,transparent)]">
      <motion.div
        className="flex gap-3 w-max"
        animate={reduce ? undefined : { x: ['0%', '-50%'] }}
        transition={{ duration: 26, repeat: Infinity, ease: 'linear' }}
      >
        {row.map(([name, role], i) => (
          <div key={`${name}-${i}`} className="shrink-0 rounded-xl panel px-5 py-4 min-w-[190px]">
            <p className="text-sm font-medium text-foreground">{name}</p>
            <p className="mt-0.5 text-xs text-muted-foreground">{role}</p>
          </div>
        ))}
      </motion.div>
    </div>
  );
}

function ScrollProgress() {
  const { scrollYProgress } = useScroll();
  const scaleX = useSpring(scrollYProgress, { stiffness: 120, damping: 30, restDelta: 0.001 });
  return <motion.div style={{ scaleX }} className="fixed top-0 inset-x-0 h-0.5 bg-brand origin-left z-[60]" />;
}

function BuildBar() {
  const reduce = useReducedMotion();
  return (
    <div className="h-0.5 mt-1.5 ml-3.5 overflow-hidden rounded-full bg-surface-3">
      <motion.div
        className="h-full w-1/3 rounded-full bg-brand"
        animate={reduce ? { x: 0 } : { x: ['-110%', '330%'] }}
        transition={{ duration: 1.6, repeat: Infinity, ease: 'easeInOut' }}
      />
    </div>
  );
}

function Shimmer() {
  const reduce = useReducedMotion();
  if (reduce) return null;
  return (
    <motion.div
      aria-hidden
      className="pointer-events-none absolute inset-0 z-20"
      initial={{ x: '-130%' }}
      animate={{ x: '130%' }}
      transition={{ delay: 1, duration: 1.2, ease: 'easeInOut' }}
      style={{ background: 'linear-gradient(105deg, transparent 42%, oklch(1 0 0 / 7%) 50%, transparent 58%)' }}
    />
  );
}

function LandingContent() {
  const heroRef = useRef<HTMLDivElement>(null);
  const stepsRef = useRef<HTMLDivElement>(null);

  const { scrollYProgress: heroProg } = useScroll({ target: heroRef, offset: ['start start', 'end start'] });
  const mockY = useTransform(heroProg, [0, 1], [0, 140]);
  const mockScale = useTransform(heroProg, [0, 1], [1, 0.93]);
  const mockOpacity = useTransform(heroProg, [0, 0.85], [1, 0.25]);
  const gridY = useTransform(heroProg, [0, 1], [0, 80]);

  const { scrollYProgress: stepsProg } = useScroll({ target: stepsRef, offset: ['start center', 'end center'] });
  const lineScaleY = useSpring(stepsProg, { stiffness: 120, damping: 30, restDelta: 0.001 });

  return (
    <div className="min-h-screen bg-background text-foreground">
      <ScrollProgress />

      <nav className="fixed top-0 inset-x-0 z-50 border-b border-hairline bg-background/70 backdrop-blur-xl">
        <div className="max-w-6xl mx-auto px-6 h-14 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <LogoMark size={28} />
            <span className="font-display font-semibold text-sm tracking-tight">Launchpad</span>
          </div>
          <div className="hidden md:flex items-center gap-7">
            {NAV_LINKS.map((l) => (
              <a
                key={l}
                href={`#${l.toLowerCase().replace(/ /g, '-')}`}
                className="text-xs text-muted-foreground hover:text-foreground transition-colors"
              >
                {l}
              </a>
            ))}
          </div>
          <Link
            href="/login"
            className="h-8 px-4 rounded-lg bg-primary text-primary-foreground text-xs font-medium flex items-center gap-1.5 transition-all hover:brightness-110 shadow-sm shadow-brand/20"
          >
            Sign in <ArrowRight className="w-3.5 h-3.5" />
          </Link>
        </div>
      </nav>

      <section ref={heroRef} className="pt-36 pb-24 px-6 text-center relative overflow-hidden">
        <motion.div style={{ y: gridY }} className="absolute inset-0 pointer-events-none">
          <div
            className="absolute inset-0"
            style={{
              backgroundImage: 'linear-gradient(oklch(1 0 0 / 5%) 1px,transparent 1px),linear-gradient(90deg,oklch(1 0 0 / 5%) 1px,transparent 1px)',
              backgroundSize: '48px 48px',
              maskImage: 'radial-gradient(120% 90% at 50% 0%, #000 40%, transparent 78%)',
            }}
          />
        </motion.div>
        <div
          className="absolute top-[28%] left-1/2 -translate-x-1/2 -translate-y-1/2 w-[680px] h-[340px] rounded-full blur-3xl pointer-events-none animate-glow"
          style={{ background: 'radial-gradient(circle, var(--brand-soft), transparent 70%)' }}
        />

        <div className="relative max-w-3xl mx-auto">
          <Reveal delay={0} y={14}>
            <span className="inline-flex items-center gap-2 px-3 py-1 rounded-full border border-brand/30 bg-brand-soft text-brand text-xs mb-7">
              <span className="w-1.5 h-1.5 rounded-full bg-brand animate-pulse" />
              Beta — your first deploy in under five minutes
            </span>
          </Reveal>

          <Reveal delay={0.08}>
            <h1 className="text-4xl md:text-6xl font-display font-semibold tracking-tight leading-[1.05] mb-6">
              Push to GitHub.<br />
              <span className="text-brand">Live on AWS in minutes.</span>
            </h1>
          </Reveal>

          <Reveal delay={0.16}>
            <p className="text-muted-foreground text-base md:text-lg max-w-xl mx-auto mb-10 leading-relaxed">
              Launchpad provisions a real production environment in your own AWS account — VPC,
              Fargate, load balancer, registry — then ships every commit to a live URL. You hold the
              keys; we handle the plumbing.
            </p>
          </Reveal>

          <Reveal delay={0.24}>
            <div className="flex items-center justify-center gap-3 flex-wrap">
              <MagneticCta
                href="/login"
                className="h-11 px-6 rounded-xl bg-primary text-primary-foreground text-sm font-medium flex items-center gap-2 shadow-lg shadow-brand/25 hover:brightness-110 transition-[filter]"
              >
                Start deploying <ArrowRight className="w-4 h-4" />
              </MagneticCta>
              <a
                href="#how-it-works"
                className="h-11 px-6 rounded-xl border border-hairline-strong text-sm text-muted-foreground hover:text-foreground hover:border-brand/40 flex items-center gap-2 transition-colors"
              >
                See how it works <ChevronRight className="w-4 h-4" />
              </a>
            </div>
          </Reveal>
        </div>

        <motion.div style={{ y: mockY, scale: mockScale, opacity: mockOpacity }} className="relative mt-20 max-w-5xl mx-auto">
          <motion.div
            initial={{ opacity: 0, y: 48 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.9, delay: 0.35, ease: EASE }}
            className="relative rounded-2xl border border-hairline-strong bg-surface-1 overflow-hidden shadow-2xl shadow-black/60"
          >
            <Shimmer />
            <div className="flex items-center gap-1.5 px-4 py-3 border-b border-hairline">
              <span className="w-3 h-3 rounded-full bg-destructive/70 flex items-center justify-center group">
                <X className="w-1.5 h-1.5 text-black/60 opacity-0 group-hover:opacity-100" />
              </span>
              <span className="w-3 h-3 rounded-full bg-warning/70" />
              <span className="w-3 h-3 rounded-full bg-success/70" />
              <span className="ml-3 text-xs text-muted-foreground/60 font-mono">launchpad.app/dashboard</span>
            </div>

            <div className="flex h-[480px] text-left">
              <div className="w-[200px] shrink-0 border-r border-hairline flex flex-col p-3 gap-1">
                <div className="flex items-center gap-2 px-2 py-2 mb-2">
                  <LogoMark size={24} className="shrink-0" />
                  <span className="text-xs font-display font-semibold">Launchpad</span>
                </div>
                {[
                  { label: 'Infrastructures', active: true },
                  { label: 'Applications', active: false },
                  { label: 'Settings', active: false },
                ].map((item) => (
                  <div
                    key={item.label}
                    className={`px-2 py-1.5 rounded-lg text-xs flex items-center gap-2 ${item.active ? 'bg-brand-soft text-brand' : 'text-muted-foreground/60'}`}
                  >
                    <span className={`w-1.5 h-1.5 rounded-full ${item.active ? 'bg-brand' : 'bg-surface-3'}`} />
                    {item.label}
                  </div>
                ))}
                <div className="mt-auto px-2 py-2 flex items-center gap-2">
                  <div className="w-6 h-6 rounded-full bg-surface-3 flex items-center justify-center text-[10px] text-muted-foreground">A</div>
                  <span className="text-[10px] text-muted-foreground/60">admin</span>
                  <span className="ml-auto text-[10px] px-1.5 py-0.5 rounded bg-brand-soft text-brand">super_admin</span>
                </div>
              </div>

              <div className="flex-1 overflow-hidden p-5 flex flex-col gap-4">
                <div className="flex items-center justify-between">
                  <div>
                    <h2 className="text-sm font-display font-semibold">Infrastructures</h2>
                    <p className="text-[10px] text-muted-foreground/60 mt-0.5">2 environments provisioned</p>
                  </div>
                  <div className="h-7 px-3 rounded-lg bg-primary text-[10px] text-primary-foreground flex items-center gap-1">
                    <span>+</span> New infrastructure
                  </div>
                </div>

                <div className="grid grid-cols-3 gap-3">
                  {[
                    { label: 'Infrastructures', num: 2, dot: 'bg-success' },
                    { label: 'Applications', num: 5, dot: 'bg-brand animate-pulse' },
                    { label: 'Deployments', num: 12, dot: 'bg-azure' },
                  ].map((s) => (
                    <div key={s.label} className="bg-surface-2 border border-hairline rounded-xl p-3">
                      <div className="flex items-center gap-1.5 mb-1">
                        <span className={`w-1.5 h-1.5 rounded-full ${s.dot}`} />
                        <span className="text-[10px] text-muted-foreground/60">{s.label}</span>
                      </div>
                      <span className="text-lg font-display font-semibold tabular-nums"><Counter to={s.num} /></span>
                    </div>
                  ))}
                </div>

                <div className="grid grid-cols-2 gap-3 flex-1">
                  {[
                    { name: 'production-us', region: 'us-east-1', apps: 3 },
                    { name: 'staging-eu', region: 'eu-west-1', apps: 2 },
                  ].map((infra) => (
                    <div key={infra.name} className="bg-surface-2 border border-hairline rounded-xl p-4 flex flex-col gap-3">
                      <div className="flex items-start justify-between">
                        <div>
                          <div className="flex items-center gap-1.5 mb-0.5">
                            <span className="w-1.5 h-1.5 rounded-full bg-success" />
                            <span className="text-xs font-medium">{infra.name}</span>
                          </div>
                          <span className="text-[10px] text-muted-foreground/60 font-mono">{infra.region}</span>
                        </div>
                        <span className="text-[10px] px-2 py-0.5 rounded-full bg-success/10 text-success border border-success/20">ACTIVE</span>
                      </div>
                      <div className="flex gap-2 flex-wrap">
                        {Array.from({ length: infra.apps }).map((_, i) => (
                          <div key={i} className="h-6 px-2 rounded-md bg-surface-3 border border-hairline text-[10px] text-muted-foreground/70 flex items-center gap-1">
                            <span className="w-1 h-1 rounded-full bg-brand animate-pulse" />
                            app-{i + 1}
                          </div>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>

                <div className="bg-surface-2 border border-hairline rounded-xl p-3">
                  <p className="text-[10px] text-muted-foreground/60 mb-2 uppercase tracking-widest font-mono">Recent deployments</p>
                  <div className="space-y-1.5">
                    {[
                      { app: 'api-service', status: 'ACTIVE', time: '2m ago', dot: 'bg-success', cls: 'text-success bg-success/10' },
                      { app: 'frontend-app', status: 'BUILDING', time: '5m ago', dot: 'bg-brand animate-pulse', cls: 'text-brand bg-brand-soft' },
                      { app: 'worker-service', status: 'ACTIVE', time: '12m ago', dot: 'bg-success', cls: 'text-success bg-success/10' },
                    ].map((d) => (
                      <div key={d.app}>
                        <div className="flex items-center gap-2">
                          <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${d.dot}`} />
                          <span className="text-[10px] font-mono text-muted-foreground flex-1">{d.app}</span>
                          <span className="text-[10px] text-muted-foreground/60">{d.time}</span>
                          <span className={`text-[10px] px-1.5 py-0.5 rounded font-mono ${d.cls}`}>{d.status}</span>
                        </div>
                        {d.status === 'BUILDING' && <BuildBar />}
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            </div>
          </motion.div>
        </motion.div>
      </section>

      <section className="px-6 pb-24">
        <div className="max-w-4xl mx-auto grid grid-cols-1 sm:grid-cols-3 gap-3">
          {STATS.map((s, i) => (
            <Reveal key={s.label} delay={i * 0.06}>
              <div className="rounded-xl panel px-6 py-7 text-center h-full">
                <p className="text-4xl md:text-5xl font-display font-semibold text-foreground tabular-nums leading-none">
                  {s.display ? s.display : <Counter to={s.to!} suffix={s.suffix} />}
                </p>
                <p className="mt-3 text-xs text-muted-foreground leading-relaxed">{s.label}</p>
              </div>
            </Reveal>
          ))}
        </div>
      </section>

      <section id="features" className="py-24 px-6 border-t border-hairline">
        <div className="max-w-6xl mx-auto">
          <Reveal className="text-center mb-14">
            <p className="eyebrow mb-3">Capabilities</p>
            <h2 className="text-3xl md:text-4xl font-display font-semibold tracking-tight">The parts you&apos;d rather not build</h2>
          </Reveal>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {FEATURES.map((f, i) => (
              <Reveal key={f.title} delay={(i % 3) * 0.06}>
                <TiltCard className="group h-full rounded-xl panel p-5 transition-colors hover:border-brand/30 hover:bg-surface-2">
                  <div className="w-9 h-9 rounded-lg bg-brand-soft border border-brand/20 flex items-center justify-center mb-4">
                    <f.icon className="w-5 h-5 text-brand" />
                  </div>
                  <h3 className="text-sm font-semibold mb-2">{f.title}</h3>
                  <p className="text-xs text-muted-foreground leading-relaxed">{f.desc}</p>
                </TiltCard>
              </Reveal>
            ))}
          </div>
        </div>
      </section>

      <section id="how-it-works" className="py-24 px-6 border-t border-hairline">
        <div className="max-w-3xl mx-auto">
          <Reveal className="text-center mb-14">
            <p className="eyebrow mb-3">How it works</p>
            <h2 className="text-3xl md:text-4xl font-display font-semibold tracking-tight">Four steps. One afternoon.</h2>
          </Reveal>
          <div ref={stepsRef} className="relative">
            <div className="absolute left-[27px] top-6 bottom-6 w-px bg-hairline hidden sm:block" />
            <motion.div style={{ scaleY: lineScaleY }} className="absolute left-[27px] top-6 bottom-6 w-px bg-brand origin-top hidden sm:block" />
            <div className="space-y-4">
              {STEPS.map((s, i) => (
                <Reveal key={s.n} delay={i * 0.05}>
                  <div className="flex gap-4 sm:gap-5 items-start">
                    <span className="relative z-10 shrink-0 w-14 h-14 rounded-xl bg-surface-2 border border-hairline-strong flex items-center justify-center font-mono text-sm text-brand">
                      {s.n}
                    </span>
                    <div className="flex-1 rounded-xl panel p-5 hover:border-brand/25 transition-colors">
                      <h3 className="text-sm font-semibold mb-1">{s.title}</h3>
                      <p className="text-xs text-muted-foreground leading-relaxed">{s.desc}</p>
                    </div>
                  </div>
                </Reveal>
              ))}
            </div>
          </div>
        </div>
      </section>

      <section id="stack" className="py-24 px-6 border-t border-hairline">
        <div className="max-w-5xl mx-auto">
          <Reveal className="text-center mb-12">
            <p className="eyebrow mb-3">Under the hood</p>
            <h2 className="text-3xl md:text-4xl font-display font-semibold tracking-tight">No magic. Proven tools.</h2>
          </Reveal>
          <Reveal delay={0.1}>
            <StackMarquee />
          </Reveal>
        </div>
      </section>

      <section id="pricing" className="py-24 px-6 border-t border-hairline">
        <div className="max-w-5xl mx-auto">
          <Reveal className="text-center mb-14">
            <p className="eyebrow mb-3">Pricing</p>
            <h2 className="text-3xl md:text-4xl font-display font-semibold tracking-tight">Priced to get out of the way</h2>
          </Reveal>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4 items-stretch">
            {PLANS.map((p, i) => (
              <Reveal key={p.name} delay={i * 0.07}>
                <motion.div
                  whileHover={{ y: -6 }}
                  transition={{ type: 'spring', stiffness: 300, damping: 22 }}
                  className={`h-full rounded-2xl border p-6 flex flex-col ${p.highlight ? 'border-brand/50 bg-brand-soft shadow-lg shadow-brand/10' : 'border-hairline bg-surface-1'}`}
                >
                  <div className="flex items-center justify-between mb-1">
                    <p className="text-xs text-muted-foreground">{p.name}</p>
                    {p.highlight && <span className="text-[10px] font-mono uppercase tracking-[0.14em] text-brand px-2 py-0.5 rounded-full border border-brand/30">Popular</span>}
                  </div>
                  <div className="flex items-baseline gap-1 mb-1">
                    <span className="text-3xl font-display font-semibold">{p.price}</span>
                    <span className="text-xs text-muted-foreground">{p.sub}</span>
                  </div>
                  <div className="my-5 border-t border-hairline" />
                  <ul className="space-y-2.5 flex-1 mb-6">
                    {p.features.map((f) => (
                      <li key={f} className="flex items-center gap-2 text-xs text-foreground/80">
                        <Check className="w-3.5 h-3.5 text-brand shrink-0" />
                        {f}
                      </li>
                    ))}
                  </ul>
                  <a
                    href={p.href ?? '/login'}
                    className={`h-9 rounded-lg text-xs font-medium flex items-center justify-center transition-all ${p.highlight ? 'bg-primary text-primary-foreground hover:brightness-110' : 'border border-hairline-strong text-muted-foreground hover:text-foreground hover:border-brand/40'}`}
                  >
                    {p.cta}
                  </a>
                </motion.div>
              </Reveal>
            ))}
          </div>
        </div>
      </section>

      <section className="py-28 px-6 border-t border-hairline relative overflow-hidden">
        <div className="absolute inset-0 brand-glow opacity-70 pointer-events-none" />
        <Reveal className="relative max-w-2xl mx-auto text-center">
          <h2 className="text-3xl md:text-5xl font-display font-semibold tracking-tight mb-4">Ship something today.</h2>
          <p className="text-muted-foreground text-sm md:text-base mb-9 max-w-md mx-auto">
            Connect your AWS account and put your first app in production in five minutes. No sales call, no lock-in.
          </p>
          <MagneticCta
            href="/login"
            className="inline-flex h-11 px-8 rounded-xl bg-primary text-primary-foreground text-sm font-medium items-center gap-2 shadow-lg shadow-brand/30 hover:brightness-110 transition-[filter]"
          >
            Get started — it&apos;s free <ArrowRight className="w-4 h-4" />
          </MagneticCta>
        </Reveal>
      </section>

      <footer className="border-t border-hairline py-8 px-6">
        <div className="max-w-6xl mx-auto flex flex-col sm:flex-row items-center justify-between gap-4">
          <div className="flex items-center gap-2">
            <LogoMark size={24} />
            <span className="text-xs font-medium">Launchpad</span>
          </div>
          <p className="text-xs text-muted-foreground/70">
            © {new Date().getFullYear()} Launchpad. Built by{' '}
            <span className="text-muted-foreground">Mohamed Aklamaash</span>. All rights reserved.
          </p>
          <Link href="/login" className="text-xs text-muted-foreground hover:text-foreground transition-colors">
            Sign in →
          </Link>
        </div>
      </footer>
    </div>
  );
}

export default function LandingPage() {
  const router = useRouter();
  const reduce = useReducedMotion();
  useEffect(() => {
    if (localStorage.getItem('access_token')) router.replace('/dashboard');
  }, [router]);
  if (reduce) return <LandingContent />;
  return (
    <ReactLenis root options={{ lerp: 0.09 }}>
      <LandingContent />
    </ReactLenis>
  );
}
