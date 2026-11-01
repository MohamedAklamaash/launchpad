'use client';

import { useRef, useState } from 'react';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Check, Copy, KeyRound, ShieldAlert, Terminal } from 'lucide-react';
import { infrastructureApi } from '@/lib/api/infrastructures';
import { getOnboardingMisconfiguration, resolveOnboardingScript } from '@/lib/onboarding-scripts';
import { toast } from 'sonner';

const API_GATEWAY_URL = process.env.NEXT_PUBLIC_API_GATEWAY_URL || 'http://localhost:8000';

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  infraId: string;
  /** Denied actions from a 422 policy_refresh_required response, if this was triggered by one. */
  deniedActions?: string[];
}

export function PolicyRefreshDialog({ open, onOpenChange, infraId, deniedActions }: Props) {
  const [apiKey, setApiKey] = useState<string | null>(null);
  const [platformPrincipal, setPlatformPrincipal] = useState<{ accountId: string; user: string } | null>(null);
  const [issuing, setIssuing] = useState(false);
  const [copied, setCopied] = useState(false);
  const copyResetRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const handleGenerateKey = async () => {
    setIssuing(true);
    try {
      // Read-only fetch before the key issue: issuing revokes any prior key, so a
      // failure here must not burn one.
      const infra = await infrastructureApi.get(infraId);
      const { api_key } = await infrastructureApi.issueScriptApiKey();
      setPlatformPrincipal({ accountId: infra.platform_account_id, user: infra.platform_user });
      setApiKey(api_key);
    } catch (error: unknown) {
      const err = error as { response?: { data?: { error?: string } } };
      toast.error(err.response?.data?.error || 'Could not generate an API key');
    } finally {
      setIssuing(false);
    }
  };

  const copy = (text: string) => {
    navigator.clipboard.writeText(text).then(() => {
      if (copyResetRef.current) clearTimeout(copyResetRef.current);
      setCopied(true);
      copyResetRef.current = setTimeout(() => setCopied(false), 1500);
    });
  };

  const close = (o: boolean) => {
    if (!o) {
      setApiKey(null);
      setPlatformPrincipal(null);
    }
    onOpenChange(o);
  };

  const onboardingMisconfig = getOnboardingMisconfiguration();
  let refreshScript: ReturnType<typeof resolveOnboardingScript> | null = null;
  if (apiKey && platformPrincipal && !onboardingMisconfig) {
    try {
      refreshScript = resolveOnboardingScript('refresh', [
        `export LAUNCHPAD_INFRA_ID=${infraId}`,
        `export LAUNCHPAD_CALLBACK_URL=${API_GATEWAY_URL}/api/infrastructures/policy-refresh/callback`,
        `export LAUNCHPAD_API_KEY=${apiKey}`,
        `export LAUNCHPAD_PLATFORM_ACCOUNT_ID=${platformPrincipal.accountId}`,
        `export LAUNCHPAD_PLATFORM_USER=${platformPrincipal.user}`,
      ]);
    } catch {
      refreshScript = null;
    }
  }

  return (
    <Dialog open={open} onOpenChange={close}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle className="text-base font-semibold">Refresh IAM policy</DialogTitle>
        </DialogHeader>

        {deniedActions && deniedActions.length > 0 && (
          <div className="flex items-start gap-3 rounded-lg border border-warning/30 bg-warning/10 p-4">
            <ShieldAlert className="size-4 text-warning shrink-0 mt-0.5" />
            <div className="space-y-1">
              <p className="text-sm font-medium text-foreground">Missing IAM permissions</p>
              <p className="text-xs text-muted-foreground">
                Launchpad&apos;s role in your AWS account is missing: {deniedActions.join(', ')}. Run the refresh
                script below, then try again.
              </p>
            </div>
          </div>
        )}

        {!apiKey ? (
          <div className="space-y-3">
            <p className="text-xs text-muted-foreground">
              Generates a per-user API key and the refresh snippet. Re-running the script updates
              LaunchpadDeploymentPolicy in place. Nothing is recreated.
            </p>
            <Button onClick={handleGenerateKey} disabled={issuing} className="w-full gap-1.5">
              <KeyRound className="size-3.5" /> {issuing ? 'Generating…' : 'Generate API key'}
            </Button>
          </div>
        ) : (
          <div className="space-y-3">
            <div className="rounded-lg border border-warning/30 bg-warning/10 px-4 py-3">
              <p className="text-xs text-warning">
                This key is shown once. Copy the snippet now; issuing a new key revokes this one.
              </p>
            </div>
            {onboardingMisconfig ? (
              <div className="rounded-lg border border-warning/30 bg-warning/10 p-4 space-y-1">
                <p className="text-xs font-medium text-foreground">Script source is misconfigured</p>
                <p className="text-xs text-muted-foreground">{onboardingMisconfig}</p>
                <div className="flex items-center gap-2 pt-1">
                  <span className="w-16 shrink-0 text-xs text-muted-foreground">API key</span>
                  <code className="break-all font-mono text-xs text-foreground">{apiKey}</code>
                </div>
              </div>
            ) : refreshScript ? (
              <div className="rounded-lg border border-border bg-card p-4 space-y-3">
                <div className="flex items-start gap-3">
                  <Terminal className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
                  <div className="space-y-0.5 flex-1 min-w-0">
                    <p className="text-sm font-medium text-foreground">{refreshScript.label}</p>
                    <p className="text-xs text-muted-foreground">{refreshScript.description}</p>
                  </div>
                  <button type="button" aria-label="Copy command" onClick={() => copy(refreshScript!.invocation)} className="shrink-0 rounded-sm text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60">
                    {copied ? <Check className="size-3.5 text-success" /> : <Copy className="size-3.5" />}
                  </button>
                </div>
                <pre className="rounded-md border border-border bg-surface-1 px-3 py-2.5 font-mono text-xs text-foreground overflow-x-auto whitespace-pre">{refreshScript.invocation}</pre>
              </div>
            ) : null}
            <Button variant="outline" onClick={() => close(false)} className="w-full">Done</Button>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
