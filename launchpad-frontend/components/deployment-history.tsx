'use client';

import { useEffect, useState } from 'react';
import { History, RotateCcw, GitCommitHorizontal } from 'lucide-react';
import { applicationApi } from '@/lib/api/applications';
import { Deployment } from '@/types/application';
import { RollbackDialog } from '@/components/rollback-dialog';

interface Props {
  appId: string;
  canRollback: boolean;
  onRolledBack: () => void;
}

export function DeploymentHistory({ appId, canRollback, onRolledBack }: Props) {
  const [deployments, setDeployments] = useState<Deployment[] | null>(null);
  const [target, setTarget] = useState<Deployment | null>(null);

  useEffect(() => {
    applicationApi
      .listDeployments(appId)
      .then(setDeployments)
      .catch((e: unknown) => {
        const error = e as { response?: { data?: { error?: string } } };
        // Owner-only endpoint: an invited viewer gets 403 here. Fail quiet rather than
        // toast an error for a view that simply isn't theirs to see.
        if (error.response) setDeployments([]);
      });
  }, [appId]);

  if (!deployments || deployments.length === 0) return null;

  return (
    <div className="rounded-xl panel p-4">
      <p className="eyebrow mb-3 flex items-center gap-1.5">
        <History className="w-3 h-3" /> Deployment history
      </p>
      <div className="space-y-1.5 max-h-72 overflow-y-auto pr-1">
        {deployments.map((d) => (
          <div key={d.id} className="flex items-center justify-between gap-3 rounded-lg px-2.5 py-2 hover:bg-surface-2 transition-colors">
            <div className="min-w-0 flex items-center gap-2">
              <GitCommitHorizontal className="w-3.5 h-3.5 text-muted-foreground shrink-0" />
              <div className="min-w-0">
                <p className="text-xs font-mono text-foreground truncate">{d.image_tag}</p>
                <p className="text-[10px] text-muted-foreground">
                  {d.triggered_by === 'ROLLBACK' ? 'Rollback' : 'Deploy'} · {new Date(d.created_at).toLocaleString()}
                </p>
              </div>
            </div>
            {canRollback && (
              <button
                onClick={() => setTarget(d)}
                className="shrink-0 flex items-center gap-1 text-[10px] font-mono uppercase tracking-widest text-muted-foreground/70 hover:text-brand transition-colors outline-none focus-visible:ring-2 focus-visible:ring-ring/60 rounded px-1.5 py-1"
                title="Roll back to this deployment"
              >
                <RotateCcw className="w-3 h-3" /> Roll back
              </button>
            )}
          </div>
        ))}
      </div>

      <RollbackDialog
        open={target !== null}
        onOpenChange={(o) => { if (!o) setTarget(null); }}
        appId={appId}
        deployment={target}
        onRolledBack={onRolledBack}
      />
    </div>
  );
}
