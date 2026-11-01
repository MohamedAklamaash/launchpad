'use client';

import { useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { notFound, useRouter } from 'next/navigation';
import { LogoMark } from '@/components/logo-mark';
import { Button } from '@/components/ui/button';
import { buttonVariants } from '@/components/ui/button-variants';
import { ORGS_ENABLED } from '@/lib/orgs-enabled';
import { apiErrorMessage, goToDashboard, organizationApi, switchToOrg } from '@/lib/api/organizations';
import {
  ACCEPT_PATH,
  clearInviteToken,
  readStashedInviteToken,
  stashInviteToken,
  takeTokenFromUrl,
} from '@/lib/invite-token';
import { loginUrlWithNext } from '@/lib/post-login';

type View =
  | { kind: 'working'; label: string }
  | { kind: 'missing' }
  | { kind: 'invalid' }
  | { kind: 'already-member'; message: string }
  | { kind: 'joined-no-switch' }
  | { kind: 'retry'; message: string };

function AcceptInvitation() {
  const router = useRouter();
  const started = useRef(false);
  const [view, setView] = useState<View>({ kind: 'working', label: 'Checking your invitation…' });

  const accept = async (token: string) => {
    setView({ kind: 'working', label: 'Joining the organization…' });
    try {
      const joined = await organizationApi.acceptInvitation(token);
      clearInviteToken(sessionStorage);
      try {
        await switchToOrg(joined.organization_id, true);
      } catch {
        setView({ kind: 'joined-no-switch' });
        return;
      }
      goToDashboard();
    } catch (err) {
      const status = (err as { response?: { status?: number } }).response?.status;
      if (status === 400) {
        clearInviteToken(sessionStorage);
        setView({ kind: 'invalid' });
      } else if (status === 409) {
        clearInviteToken(sessionStorage);
        setView({ kind: 'already-member', message: apiErrorMessage(err, 'You are already a member of this organization.') });
      } else {
        // Token stays in sessionStorage so Retry (or a reload) can use it.
        setView({ kind: 'retry', message: apiErrorMessage(err, 'Could not reach the server. Try again.') });
      }
    }
  };

  useEffect(() => {
    // StrictMode runs effects twice in dev and the second run would find the fragment
    // already scrubbed.
    if (started.current) return;
    started.current = true;

    // First statement that touches the token: read it and scrub it from the URL.
    const fromUrl = takeTokenFromUrl(window);
    if (fromUrl) stashInviteToken(sessionStorage, fromUrl);
    const token = fromUrl ?? readStashedInviteToken(sessionStorage);

    if (!token) {
      setView({ kind: 'missing' });
      return;
    }

    if (!localStorage.getItem('access_token')) {
      // The token stays in sessionStorage. Only the plain path travels through login.
      router.replace(loginUrlWithNext(ACCEPT_PATH));
      return;
    }

    void accept(token);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const retry = () => {
    const token = readStashedInviteToken(sessionStorage);
    if (token) void accept(token);
    else setView({ kind: 'missing' });
  };

  return (
    <div className="flex min-h-dvh items-center justify-center bg-background px-4">
      <div className="reveal flex w-full max-w-sm flex-col items-center gap-6 text-center">
        <div className="inline-flex items-center gap-2.5">
          <LogoMark size={28} />
          <span className="text-sm font-semibold tracking-tight text-foreground">Launchpad</span>
        </div>

        {view.kind === 'working' && (
          <>
            <div className="size-5 animate-spin rounded-full border-2 border-border border-t-brand" />
            <p className="text-sm text-muted-foreground" role="status">{view.label}</p>
          </>
        )}

        {view.kind === 'missing' && (
          <Notice title="No invitation found" body="Open the link from your invitation email again. It can only be used from that link.">
            <Link href="/dashboard" className={buttonVariants({ variant: 'outline' })}>Go to dashboard</Link>
          </Notice>
        )}

        {view.kind === 'invalid' && (
          <Notice
            title="This invitation can't be used"
            body="The link is invalid or has expired, or it was sent to a different email address than the one you are signed in with. Ask the organization for a new invitation."
          >
            <Link href="/dashboard" className={buttonVariants({ variant: 'outline' })}>Go to dashboard</Link>
          </Notice>
        )}

        {view.kind === 'already-member' && (
          <Notice title="Already a member" body={view.message}>
            <Link href="/dashboard" className={buttonVariants()}>Go to dashboard</Link>
          </Notice>
        )}

        {view.kind === 'joined-no-switch' && (
          <Notice title="You joined the organization" body="Switching into it didn't work. Use the organization switcher in the dashboard header.">
            <Button onClick={goToDashboard}>Go to dashboard</Button>
          </Notice>
        )}

        {view.kind === 'retry' && (
          <Notice title="Something went wrong" body={view.message}>
            <Button onClick={retry}>Try again</Button>
          </Notice>
        )}
      </div>
    </div>
  );
}

function Notice({ title, body, children }: { title: string; body: string; children: React.ReactNode }) {
  return (
    <div className="space-y-4" role="alert">
      <div className="space-y-1.5">
        <h1 className="text-xl font-semibold tracking-tight text-foreground">{title}</h1>
        <p className="text-sm text-muted-foreground">{body}</p>
      </div>
      {children}
    </div>
  );
}

export default function AcceptInvitationPage() {
  if (!ORGS_ENABLED) notFound();
  return <AcceptInvitation />;
}
