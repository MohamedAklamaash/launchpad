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
  const [issuing, setIssuing] = useState(false);
  const [copied, setCopied] = useState(false);
  const copyResetRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const handleGenerateKey = async () => {
    setIssuing(true);
    try {
      const { api_key } = await infrastructureApi.issueScriptApiKey();
      setApiKey(api_key);
    } catch (error: unknown) {
      const err = error as { response?: { data?: { error?: string } } };
      toast.error(err.response?.data?.error || 'Failed to generate API key');
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
    if (!o) setApiKey(null);
    onOpenChange(o);
  };

  const onboardingMisconfig = getOnboardingMisconfiguration();
  let refreshScript: ReturnType<typeof resolveOnboardingScript> | null = null;
  if (apiKey && !onboardingMisconfig) {
    try {
      refreshScript = resolveOnboardingScript('refresh', [
        `export LAUNCHPAD_INFRA_ID=${infraId}`,
        `export LAUNCHPAD_CALLBACK_URL=${API_GATEWAY_URL}/api/infrastructures/policy-refresh/callback`,
        `export LAUNCHPAD_API_KEY=${apiKey}`,
      ]);
    } catch {
      refreshScript = null;
    }
  }

  return (
    <Dialog open={open} onOpenChange={close}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle className="text-base font-display font-semibold">Refresh IAM Policy</DialogTitle>
        </DialogHeader>

        {deniedActions && deniedActions.length > 0 && (
          <div className="rounded-xl border border-warning/30 bg-warning/10 p-4 flex items-start gap-3">
            <ShieldAlert className="w-4 h-4 text-warning shrink-0 mt-0.5" />
            <div className="space-y-1">
              <p className="text-sm font-medium text-warning">Missing IAM permissions</p>
              <p className="text-xs text-warning/80">
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
              LaunchpadDeploymentPolicy in place — nothing is recreated.
            </p>
            <Button onClick={handleGenerateKey} disabled={issuing} className="w-full gap-1.5">
              <KeyRound className="w-3.5 h-3.5" /> {issuing ? 'Generating…' : 'Generate API key'}
            </Button>
          </div>
        ) : (
          <div className="space-y-3">
            <div className="rounded-xl border border-warning/30 bg-warning/10 px-4 py-3">
              <p className="text-xs text-warning">
                This key is shown only once. Copy the snippet now — issuing a new key revokes this one.
              </p>
            </div>
            {onboardingMisconfig ? (
              <div className="rounded-xl border border-warning/30 bg-warning/10 p-4 space-y-1">
                <p className="text-xs font-medium text-warning">Script source is misconfigured</p>
                <p className="text-xs text-warning/80">{onboardingMisconfig}</p>
                <div className="flex items-center gap-2 pt-1">
                  <span className="eyebrow w-16 shrink-0">API key</span>
                  <code className="text-xs font-mono text-warning break-all">{apiKey}</code>
                </div>
              </div>
            ) : refreshScript ? (
              <div className="rounded-xl panel p-4 space-y-3">
                <div className="flex items-start gap-3">
                  <Terminal className="w-4 h-4 text-success shrink-0 mt-0.5" />
                  <div className="space-y-0.5 flex-1 min-w-0">
                    <p className="text-sm font-medium text-foreground">{refreshScript.label}</p>
                    <p className="text-xs text-muted-foreground">{refreshScript.description}</p>
                  </div>
                  <button onClick={() => copy(refreshScript!.invocation)} className="shrink-0 text-muted-foreground hover:text-foreground transition-colors">
                    {copied ? <Check className="w-3.5 h-3.5 text-success" /> : <Copy className="w-3.5 h-3.5" />}
                  </button>
                </div>
                <pre className="bg-surface-3 border border-hairline rounded-lg px-3 py-2.5 text-[11px] font-mono text-success overflow-x-auto whitespace-pre">{refreshScript.invocation}</pre>
              </div>
            ) : null}
            <Button variant="outline" onClick={() => close(false)} className="w-full">Done</Button>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
