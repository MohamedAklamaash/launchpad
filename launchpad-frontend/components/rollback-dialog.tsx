'use client';

import { useEffect, useState } from 'react';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { History, Plus, Minus, ShieldAlert } from 'lucide-react';
import { applicationApi } from '@/lib/api/applications';
import { Deployment, RollbackPreview } from '@/types/application';
import { toast } from 'sonner';

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  appId: string;
  deployment: Deployment | null;
  onRolledBack: () => void;
}

export function RollbackDialog({ open, onOpenChange, appId, deployment, onRolledBack }: Props) {
  const [preview, setPreview] = useState<RollbackPreview | null>(null);
  const [loading, setLoading] = useState(false);
  const [confirming, setConfirming] = useState(false);

  useEffect(() => {
    if (!open || !deployment) {
      setPreview(null);
      return;
    }
    setLoading(true);
    applicationApi
      .getRollbackPreview(appId, deployment.id)
      .then(setPreview)
      .catch((e: unknown) => {
        const error = e as { response?: { data?: { error?: string } } };
        toast.error(error.response?.data?.error || 'Failed to load rollback preview');
        onOpenChange(false);
      })
      .finally(() => setLoading(false));
  }, [open, deployment, appId, onOpenChange]);

  const confirm = async () => {
    if (!deployment) return;
    setConfirming(true);
    try {
      await applicationApi.rollback(appId, deployment.id);
      toast.success('Rollback queued — auto-deploy is paused until you resume it');
      onOpenChange(false);
      onRolledBack();
    } catch (e: unknown) {
      const error = e as { response?: { data?: { error?: string } } };
      toast.error(error.response?.data?.error || 'Failed to queue rollback');
    } finally {
      setConfirming(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle className="text-base font-display font-semibold flex items-center gap-2">
            <History className="w-4 h-4 text-brand" /> Roll back deployment
          </DialogTitle>
        </DialogHeader>

        {loading || !preview ? (
          <div className="h-32 rounded-xl panel animate-pulse" />
        ) : (
          <div className="space-y-4">
            <div className="rounded-xl panel p-4 space-y-2.5">
              <div className="flex items-center justify-between">
                <span className="text-xs text-muted-foreground">Image</span>
                <code className="text-xs font-mono text-foreground break-all text-right">{preview.image_tag}</code>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-xs text-muted-foreground">Resources</span>
                <span className="text-xs font-mono text-foreground">
                  {preview.cpu} vCPU · {preview.memory} GB · port {preview.port}
                </span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-xs text-muted-foreground">Deployed</span>
                <span className="text-xs font-mono text-foreground">
                  {new Date(preview.deployed_at).toLocaleString()}
                </span>
              </div>
            </div>

            {(preview.added_keys.length > 0 || preview.removed_keys.length > 0 || preview.values_changed) ? (
              <div className="rounded-xl border border-warning/30 bg-warning/10 p-4 space-y-2">
                <div className="flex items-start gap-2">
                  <ShieldAlert className="w-4 h-4 text-warning shrink-0 mt-0.5" />
                  <p className="text-xs font-medium text-warning">Environment config has changed since this deploy</p>
                </div>
                <p className="text-[11px] text-warning/80">
                  Rolling back restores the image and resources above. Environment variable values are always
                  read fresh from the app&apos;s current config — a rollback never restores an old secret.
                </p>
                <div className="space-y-1 pt-1">
                  {preview.added_keys.map((k) => (
                    <div key={`add-${k}`} className="flex items-center gap-1.5 text-[11px] font-mono">
                      <Plus className="w-3 h-3 text-success shrink-0" />
                      <span className="text-foreground">{k}</span>
                      <span className="text-muted-foreground/60">(not present at that deploy)</span>
                    </div>
                  ))}
                  {preview.removed_keys.map((k) => (
                    <div key={`rm-${k}`} className="flex items-center gap-1.5 text-[11px] font-mono">
                      <Minus className="w-3 h-3 text-destructive shrink-0" />
                      <span className="text-foreground">{k}</span>
                      <span className="text-muted-foreground/60">(present at that deploy, missing now)</span>
                    </div>
                  ))}
                  {preview.values_changed && preview.added_keys.length === 0 && preview.removed_keys.length === 0 && (
                    <p className="text-[11px] text-warning/80">One or more shared keys have a different value now.</p>
                  )}
                </div>
              </div>
            ) : (
              <p className="text-xs text-muted-foreground">Environment config is unchanged since this deploy.</p>
            )}
          </div>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button onClick={confirm} disabled={confirming || loading || !preview}>
            {confirming ? 'Queuing…' : 'Confirm rollback'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
