'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { AlertTriangle, Check, CircleAlert, Flame, Loader2, XCircle } from 'lucide-react';
import { infrastructureApi } from '@/lib/api/infrastructures';
import { NukeStatus, NukeStepStatus } from '@/types/infrastructure';
import { toast } from 'sonner';

const POLL_MS = 3000;

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  infraId: string;
  infraName: string;
  /** Called once a run reaches COMPLETED — the infrastructure row is gone server-side. */
  onNuked: () => void;
}

const STEP_ICON: Record<NukeStepStatus, React.ReactNode> = {
  pending: <span className="w-3.5 h-3.5 rounded-full border border-hairline shrink-0" />,
  running: <Loader2 className="w-3.5 h-3.5 text-azure animate-spin shrink-0" />,
  success: <Check className="w-3.5 h-3.5 text-success shrink-0" />,
  failed: <XCircle className="w-3.5 h-3.5 text-destructive shrink-0" />,
  policy_refresh_required: <CircleAlert className="w-3.5 h-3.5 text-warning shrink-0" />,
};

export function NukeInfrastructureDialog({ open, onOpenChange, infraId, infraName, onNuked }: Props) {
  const [confirmText, setConfirmText] = useState('');
  const [starting, setStarting] = useState(false);
  const [status, setStatus] = useState<NukeStatus | null>(null);
  const [checkingExisting, setCheckingExisting] = useState(false);
  const notifiedRef = useRef(false);

  const poll = useCallback(async () => {
    try {
      const next = await infrastructureApi.getNukeStatus(infraId);
      setStatus(next);
    } catch {
      // A transient poll failure keeps the last known state on screen; the interval retries.
    }
  }, [infraId]);

  // On open, a run may already exist for this infra — e.g. the owner navigated away
  // mid-run, or is reopening after an earlier failure — in which case the progress
  // view (with Retry, if it failed) is what they need, not the confirm form again.
  useEffect(() => {
    if (!open) {
      setConfirmText('');
      setStatus(null);
      setCheckingExisting(false);
      notifiedRef.current = false;
      return;
    }
    let cancelled = false;
    setCheckingExisting(true);
    infrastructureApi.getNukeStatus(infraId)
      .then((existing) => { if (!cancelled) setStatus(existing); })
      .catch(() => { /* 404: no run yet — stay on the confirm form */ })
      .finally(() => { if (!cancelled) setCheckingExisting(false); });
    return () => { cancelled = true; };
  }, [open, infraId]);

  useEffect(() => {
    if (!open) return;
    if (!status || status.status === 'PENDING' || status.status === 'RUNNING') {
      const interval = setInterval(poll, POLL_MS);
      return () => clearInterval(interval);
    }
  }, [open, status, poll]);

  useEffect(() => {
    if (status?.status === 'COMPLETED' && !notifiedRef.current) {
      notifiedRef.current = true;
      onNuked();
    }
  }, [status, onNuked]);

  const start = async () => {
    setStarting(true);
    try {
      const result = await infrastructureApi.startNuke(infraId, confirmText);
      setStatus(result);
    } catch (error: unknown) {
      const err = error as { response?: { data?: { error?: string } } };
      toast.error(err.response?.data?.error || 'Failed to start nuke');
    } finally {
      setStarting(false);
    }
  };

  const retry = () => start();

  const nameMatches = confirmText === infraName;
  const isTerminalFailure = status?.status === 'FAILED';
  const isRunning = status?.status === 'PENDING' || status?.status === 'RUNNING';

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o && isRunning) return; onOpenChange(o); }}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle className="text-base font-display font-semibold flex items-center gap-2">
            <Flame className="w-4 h-4 text-destructive" /> Nuke Infrastructure
          </DialogTitle>
        </DialogHeader>

        {checkingExisting && !status && (
          <div className="flex items-center gap-2 py-6 justify-center text-muted-foreground">
            <Loader2 className="w-4 h-4 animate-spin" />
            <p className="text-xs">Checking for an existing run…</p>
          </div>
        )}

        {!status && !checkingExisting && (
          <div className="space-y-4">
            <div className="rounded-xl border border-destructive/30 bg-destructive/5 p-3 space-y-2">
              <p className="text-xs text-foreground font-medium">This destroys everything, not just the environment:</p>
              <ul className="text-[11px] text-muted-foreground space-y-1 list-disc pl-4">
                <li>Every application on this infrastructure — unlike Delete, this does not refuse when apps exist.</li>
                <li>Every database, with <span className="text-foreground">no final snapshot</span> — including any snapshot left by an earlier database delete.</li>
                <li>Custom domains, certificates, logs, and all Terraform-managed AWS resources.</li>
                <li>The shared deployment role and Terraform state, but only if this is the last Launchpad infrastructure in this AWS account — otherwise they&rsquo;re kept and reported.</li>
              </ul>
              <p className="text-[11px] text-destructive/90">This cannot be undone.</p>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="nuke-confirm" className="text-[11px] text-muted-foreground">
                Type <span className="font-mono text-foreground">{infraName}</span> to confirm
              </Label>
              <Input
                id="nuke-confirm"
                value={confirmText}
                onChange={(e) => setConfirmText(e.target.value)}
                autoComplete="off"
                placeholder={infraName}
              />
            </div>
            <div className="flex gap-2 justify-end">
              <Button variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
              <Button variant="destructive" onClick={start} disabled={!nameMatches || starting}>
                {starting ? 'Starting…' : 'Nuke'}
              </Button>
            </div>
          </div>
        )}

        {status && (
          <div className="space-y-4">
            <div className="space-y-2">
              {status.steps.map((step) => (
                <div key={step.key} className="flex items-start gap-2.5 rounded-lg border border-hairline bg-surface-1 px-3 py-2">
                  {STEP_ICON[step.status]}
                  <div className="min-w-0 flex-1">
                    <p className="text-xs text-foreground">{step.label}</p>
                    {step.status === 'failed' && typeof step.detail === 'string' && (
                      <p className="text-[11px] text-destructive mt-0.5 break-words">{step.detail}</p>
                    )}
                    {step.status === 'policy_refresh_required' && (
                      <p className="text-[11px] text-warning mt-0.5">
                        Policy refresh required — re-run the Refresh policy script, then retry.
                      </p>
                    )}
                  </div>
                </div>
              ))}
            </div>

            {status.status === 'COMPLETED' && (
              <div className="flex items-center gap-2 rounded-lg border border-success/30 bg-success/10 px-3 py-2.5">
                <Check className="w-4 h-4 text-success shrink-0" />
                <p className="text-xs text-success">Everything removed. This infrastructure is gone.</p>
              </div>
            )}

            {isTerminalFailure && status.leftovers.length > 0 && (
              <div className="rounded-lg border border-destructive/30 bg-destructive/5 p-3 space-y-2">
                <p className="text-xs text-foreground font-medium flex items-center gap-1.5">
                  <AlertTriangle className="w-3.5 h-3.5 text-destructive" /> {status.leftovers.length} resource(s) still present
                </p>
                <ul className="text-[11px] text-muted-foreground space-y-1">
                  {status.leftovers.map((leftover, i) => (
                    <li key={i} className="font-mono break-all">
                      <span className="text-foreground">{leftover.type}</span>: {leftover.id}
                      <span className="text-muted-foreground/70"> — {leftover.reason}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}

            <div className="flex gap-2 justify-end">
              {!isRunning && (
                <Button variant="outline" onClick={() => onOpenChange(false)}>
                  {status.status === 'COMPLETED' ? 'Close' : 'Cancel'}
                </Button>
              )}
              {isTerminalFailure && (
                <Button variant="destructive" onClick={retry} disabled={starting}>
                  {starting ? 'Retrying…' : 'Retry'}
                </Button>
              )}
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
