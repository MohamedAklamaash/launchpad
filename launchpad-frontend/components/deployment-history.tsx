'use client';

import { useCallback, useEffect, useState } from 'react';
import { History, RotateCcw, GitCommitHorizontal, Hammer } from 'lucide-react';
import { applicationApi } from '@/lib/api/applications';
import { Deployment } from '@/types/application';
import { RollbackDialog } from '@/components/rollback-dialog';
import { BuildLogs } from '@/components/build-logs';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';

interface Props {
  appId: string;
  appStatus: string;
  canRollback: boolean;
  onRolledBack: () => void;
  /** Fires once the list call settles, so the parent can gate other owner-only affordances
   * (e.g. Resume auto-deploy) on the same access check instead of a separate role guess. */
  onAccessChange?: (hasAccess: boolean) => void;
}

export function DeploymentHistory({ appId, appStatus, canRollback, onRolledBack, onAccessChange }: Props) {
  const [deployments, setDeployments] = useState<Deployment[] | null>(null);
  const [target, setTarget] = useState<Deployment | null>(null);
  const [logsTarget, setLogsTarget] = useState<Deployment | null>(null);

  const refresh = useCallback(() => {
    applicationApi
      .listDeployments(appId)
      .then((data) => {
        setDeployments(data);
        onAccessChange?.(true);
      })
      .catch((e: unknown) => {
        const error = e as { response?: { data?: { error?: string } } };
        // Owner-only endpoint: an invited viewer gets 403 here. Fail quiet rather than
        // toast an error for a view that simply isn't theirs to see.
        if (error.response) {
          setDeployments([]);
          onAccessChange?.(false);
        }
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [appId]);

  // Re-fetch whenever the app's status changes — a rollback runs on the deploy worker, so
  // the new history row only exists once that finishes, which shows up here as the app
  // cycling through DEPLOYING (or BUILDING for a normal deploy) back to ACTIVE/FAILED.
  useEffect(() => {
    refresh();
  }, [refresh, appStatus]);

  if (!deployments || deployments.length === 0) return null;

  return (
    <div className="rounded-lg border border-border bg-card p-4">
      <p className="mb-3 flex items-center gap-1.5 text-sm font-medium text-foreground">
        <History className="size-3" /> Deployment history
      </p>
      <div className="space-y-1.5 max-h-72 overflow-y-auto pr-1">
        {deployments.map((d) => (
          <div key={d.id} className="flex items-center justify-between gap-3 rounded-lg px-2.5 py-2 hover:bg-surface-2 transition-colors">
            <div className="min-w-0 flex items-center gap-2">
              <GitCommitHorizontal className="size-3.5 text-muted-foreground shrink-0" />
              <div className="min-w-0">
                <p className="text-xs font-mono text-foreground truncate">{d.image_tag}</p>
                <p className="text-xs text-muted-foreground">
                  {d.triggered_by === 'ROLLBACK' ? 'Rollback' : 'Deploy'} · {new Date(d.created_at).toLocaleString()}
                </p>
              </div>
            </div>
            {d.has_build_logs && (
              <button
                onClick={() => setLogsTarget(d)}
                className="shrink-0 flex items-center gap-1 text-xs font-medium text-muted-foreground hover:text-foreground transition-colors outline-none focus-visible:ring-2 focus-visible:ring-ring/60 rounded px-1.5 py-1"
                title="View this build's logs"
              >
                <Hammer className="size-3" /> Build logs
              </button>
            )}
            {canRollback && (
              d.rollback_addressable ? (
                <button
                  onClick={() => setTarget(d)}
                  className="shrink-0 flex items-center gap-1 text-xs font-medium text-muted-foreground hover:text-foreground transition-colors outline-none focus-visible:ring-2 focus-visible:ring-ring/60 rounded px-1.5 py-1"
                  title="Roll back to this deployment"
                >
                  <RotateCcw className="size-3" /> Roll back
                </button>
              ) : (
                <button
                  disabled
                  className="shrink-0 flex items-center gap-1 text-xs font-medium text-muted-foreground/30 cursor-not-allowed rounded px-1.5 py-1"
                  title="This deploy predates per-commit image tagging and can't be rolled back to"
                >
                  <RotateCcw className="size-3" /> Roll back
                </button>
              )
            )}
          </div>
        ))}
      </div>

      <RollbackDialog
        open={target !== null}
        onOpenChange={(o) => { if (!o) setTarget(null); }}
        appId={appId}
        deployment={target}
        onRolledBack={() => {
          refresh();
          onRolledBack();
        }}
      />

      <Dialog open={logsTarget !== null} onOpenChange={(o) => { if (!o) setLogsTarget(null); }}>
        <DialogContent className="sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle className="font-mono text-sm">{logsTarget?.image_tag}</DialogTitle>
          </DialogHeader>
          {logsTarget && <BuildLogs appId={appId} deploymentId={logsTarget.id} />}
        </DialogContent>
      </Dialog>
    </div>
  );
}
