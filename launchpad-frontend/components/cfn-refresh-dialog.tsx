'use client';

import { useState } from 'react';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { ExternalLink, Loader2, ShieldAlert, ShieldCheck } from 'lucide-react';
import { infrastructureApi } from '@/lib/api/infrastructures';
import { CfnRefreshResponse } from '@/types/infrastructure';

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  infraId: string;
  /** Denied actions from a 422 policy_refresh_required response, if this was triggered by one. */
  deniedActions?: string[];
}

interface ApiErrorShape {
  response?: { status?: number; headers?: Record<string, string>; data?: { error?: string; retry_after_seconds?: number } };
}

/**
 * CloudFormation sibling to PolicyRefreshDialog. Unlike the script path (which hands the
 * customer a snippet to run), refresh here is a single backend call
 * (`cloudformation:CreateChangeSet`) that returns a console link — the customer reviews
 * and executes the change set themselves, same "customer always applies the widening"
 * posture as the script. See plan/F8-cloudformation-onboarding.md §5.
 *
 * CreateChangeSet is mutating (it leaves a named change set behind in the customer's
 * account, and burns one of the F0 rate-budget's 10-per-300s slots), so it only fires on
 * an explicit click — never from an effect on open — mirroring how
 * PolicyRefreshDialog's handleGenerateKey gates issueScriptApiKey (which revokes prior
 * keys) behind a button rather than firing it automatically.
 */
export function CfnRefreshDialog({ open, onOpenChange, infraId, deniedActions }: Props) {
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<CfnRefreshResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const handlePrepare = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await infrastructureApi.cfnRefresh(infraId);
      setResult(data);
    } catch (e: unknown) {
      const err = e as ApiErrorShape;
      const status = err.response?.status;
      const message = err.response?.data?.error;
      if (status === 429) {
        const retryAfter = err.response?.headers?.['retry-after'];
        setError(message || (retryAfter ? `Too many requests — try again in ${retryAfter}s.` : 'Too many requests — try again later.'));
      } else if (status === 403) {
        const retrySeconds = err.response?.data?.retry_after_seconds;
        setError(message || (retrySeconds ? `AssumeRole failed — try again in ${retrySeconds}s.` : 'AssumeRole failed.'));
      } else if (status === 400) {
        setError(message || 'This infrastructure was not onboarded via CloudFormation.');
      } else if (status === 404) {
        setError(message || 'CloudFormation onboarding is not available on this deployment.');
      } else {
        setError(message || 'Failed to start the policy refresh.');
      }
    } finally {
      setLoading(false);
    }
  };

  const close = (o: boolean) => {
    if (!o) {
      setResult(null);
      setError(null);
    }
    onOpenChange(o);
  };

  return (
    <Dialog open={open} onOpenChange={close}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle className="text-base font-display font-semibold">Refresh IAM Policy (CloudFormation)</DialogTitle>
        </DialogHeader>

        {deniedActions && deniedActions.length > 0 && (
          <div className="rounded-xl border border-warning/30 bg-warning/10 p-4 flex items-start gap-3">
            <ShieldAlert className="w-4 h-4 text-warning shrink-0 mt-0.5" />
            <div className="space-y-1">
              <p className="text-sm font-medium text-warning">Missing IAM permissions</p>
              <p className="text-xs text-warning/80">
                Launchpad&apos;s role in your AWS account is missing: {deniedActions.join(', ')}. Prepare a change set
                below, review it, and execute it, then try again.
              </p>
            </div>
          </div>
        )}

        {error && (
          <div className="rounded-xl border border-destructive/30 bg-destructive/10 p-4 flex items-start gap-3">
            <ShieldAlert className="w-4 h-4 text-destructive shrink-0 mt-0.5" />
            <p className="text-xs text-destructive">{error}</p>
          </div>
        )}

        {!result ? (
          <div className="space-y-3">
            <p className="text-xs text-muted-foreground">
              Prepares a CloudFormation change set against your already-onboarded stack. Nothing is applied until
              you review and execute it yourself in the AWS console.
            </p>
            <Button onClick={handlePrepare} disabled={loading} className="w-full gap-1.5">
              {loading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <ShieldCheck className="w-3.5 h-3.5" />}
              {loading ? 'Preparing…' : 'Prepare change set'}
            </Button>
          </div>
        ) : (
          <div className="space-y-3">
            <div className="rounded-xl panel-inset px-4 py-3 space-y-1.5">
              <div className="flex items-center gap-2">
                <span className="eyebrow w-24 shrink-0">Stack</span>
                <code className="text-xs font-mono text-foreground break-all">{result.stack_name}</code>
              </div>
              <div className="flex items-center gap-2">
                <span className="eyebrow w-24 shrink-0">Template</span>
                <span className="text-xs text-foreground">
                  {result.target_compute_type === 'eks' ? 'Kubernetes (ecs_fargate + eks)' : 'ECS Fargate'}
                </span>
              </div>
            </div>
            <p className="text-xs text-muted-foreground">
              Review the proposed change set in the AWS console and execute it when ready — Launchpad never applies
              a widening change on your behalf. You&apos;ll be prompted to acknowledge <span className="font-mono">CAPABILITY_NAMED_IAM</span>.
            </p>
            <a href={result.review_url} target="_blank" rel="noopener noreferrer">
              <Button className="w-full gap-1.5">
                <ExternalLink className="w-3.5 h-3.5" /> Review change set in AWS console
              </Button>
            </a>
            <Button variant="outline" onClick={() => close(false)} className="w-full gap-1.5">
              <ShieldCheck className="w-3.5 h-3.5" /> Done
            </Button>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
