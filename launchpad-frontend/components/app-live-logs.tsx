'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { PolicyRefreshDialog } from '@/components/policy-refresh-dialog';
import {
  ArrowDownToLine, ChevronDown, Copy, Download, Lock, RefreshCw, ScrollText, Search,
} from 'lucide-react';
import { applicationApi } from '@/lib/api/applications';
import { RuntimeLogContainer, RuntimeLogEvent } from '@/types/application';
import { ComputeType } from '@/types/infrastructure';
import { toast } from 'sonner';

// Live tail cadence. The endpoint budgets 30 calls/60s per user across every app/tab
// (shared/ratelimit; settings.RATE_BUDGET_RUNTIME_LOGS_*) — 5s (12/min) leaves headroom
// for a manual refresh, a cursor drain, or a second tab open without tripping 429.
const LIVE_POLL_MS = 5_000;
const MAX_BACKOFF_MS = 60_000;
// A live tick only asks for the last minute; if that minute alone is >100 events
// (filter_log_events' page size) we drain a couple of follow-up pages rather than only
// ever showing the oldest 100 of a busy minute — bounded so one noisy app can't turn a
// single tick into an unbounded chain of calls.
const MAX_LIVE_DRAINS = 2;
const BUFFER_CAP = 2000;
const WINDOW_OPTIONS = [15, 30, 60];
const SCROLL_BOTTOM_THRESHOLD = 48;

interface SeqEvent extends RuntimeLogEvent {
  seq: number;
  key: string;
}

interface LogError {
  status: number | null;
  message: string;
  retryAfterSeconds?: number;
  policyRefresh?: boolean;
}

// AWS error codes that mean "the deployment role is missing a permission" rather than a
// transient upstream hiccup — surfaced by the logs endpoint as 502 {code}. Routed to the
// same policy-refresh flow the costs panel uses for its 422, since this endpoint doesn't
// have a dedicated policy_refresh_required status of its own today.
const ACCESS_DENIED_CODES = new Set(['AccessDenied', 'AccessDeniedException', 'UnauthorizedOperation', 'Forbidden']);

function describeError(e: unknown): LogError {
  const err = e as {
    response?: { status?: number; data?: { error?: string; code?: string }; headers?: Record<string, string> };
  };
  const status = err.response?.status ?? null;
  const serverMessage = err.response?.data?.error;
  const code = err.response?.data?.code;

  if (status === 422 && code === 'policy_refresh_required') {
    return { status, message: 'Launchpad’s IAM role needs the logs permissions from a policy refresh.', policyRefresh: true };
  }
  if (status === 502 && code && ACCESS_DENIED_CODES.has(code)) {
    return { status, message: 'Launchpad’s IAM role is missing a permission needed to read logs.', policyRefresh: true };
  }

  switch (status) {
    case 400:
      return { status, message: serverMessage || 'Invalid request parameters.' };
    case 403:
      return { status, message: 'Only the infrastructure owner can view runtime logs.' };
    case 404:
      return { status, message: 'Application not found.' };
    case 409:
      return { status, message: 'Application is not deployed yet.' };
    case 429: {
      const retryAfter = err.response?.headers?.['retry-after'];
      const seconds = retryAfter ? parseInt(retryAfter, 10) : NaN;
      return {
        status,
        message: Number.isFinite(seconds) ? `Too many requests, retrying in ${seconds}s.` : 'Too many requests, retrying shortly.',
        retryAfterSeconds: Number.isFinite(seconds) ? seconds : undefined,
      };
    }
    case 502:
    case 503:
      return { status, message: 'Log service is temporarily unavailable. Try again shortly.' };
    default:
      return { status, message: serverMessage || 'Failed to load logs.' };
  }
}

function formatTimestamp(ts: string): string {
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return ts;
  const time = d.toLocaleTimeString(undefined, { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });
  return `${time}.${String(d.getMilliseconds()).padStart(3, '0')}`;
}

function levelClass(message: string): string {
  if (/\b(error|fatal|exception)\b/i.test(message)) return 'text-destructive';
  if (/\bwarn(ing)?\b/i.test(message)) return 'text-warning';
  return 'text-foreground/90';
}

function highlightAll(text: string, term: string) {
  if (!term) return text;
  const lower = text.toLowerCase();
  const nodes: React.ReactNode[] = [];
  let start = 0;
  let idx = lower.indexOf(term);
  let key = 0;
  while (idx !== -1) {
    if (idx > start) nodes.push(text.slice(start, idx));
    nodes.push(
      <mark key={key++} className="bg-warning/50 text-foreground rounded-sm px-0.5">
        {text.slice(idx, idx + term.length)}
      </mark>,
    );
    start = idx + term.length;
    idx = lower.indexOf(term, start);
  }
  if (start < text.length) nodes.push(text.slice(start));
  return nodes;
}

interface Props {
  appId: string;
  infraId: string;
  computeType?: ComputeType;
}

export function AppLiveLogs({ appId, infraId, computeType }: Props) {
  const [container, setContainer] = useState<RuntimeLogContainer>('app');
  const [minutes, setMinutes] = useState(15);
  const [previous, setPrevious] = useState(false);
  const [live, setLive] = useState(false);
  const [events, setEvents] = useState<SeqEvent[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [truncated, setTruncated] = useState(false);
  const [loading, setLoading] = useState(false);
  const [hasLoaded, setHasLoaded] = useState(false);
  const [error, setError] = useState<LogError | null>(null);
  const [filter, setFilter] = useState('');
  const [atBottom, setAtBottom] = useState(true);
  const [newSinceScroll, setNewSinceScroll] = useState(0);
  const [refreshDialog, setRefreshDialog] = useState<{ open: boolean }>({ open: false });

  const seqRef = useRef(0);
  const seenKeysRef = useRef<Set<string>>(new Set());
  const atBottomRef = useRef(true);
  const inFlightRef = useRef(false);
  const backoffRef = useRef(LIVE_POLL_MS);
  const scrollRef = useRef<HTMLDivElement>(null);
  const liveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const resetQuery = useCallback(() => {
    setEvents([]);
    setNextCursor(null);
    setTruncated(false);
    setError(null);
    setHasLoaded(false);
    setNewSinceScroll(0);
    seenKeysRef.current = new Set();
    seqRef.current = 0;
    atBottomRef.current = true;
    setAtBottom(true);
  }, []);

  // Dedupe on (timestamp, message) — the same key across two calls (e.g. the same
  // minute re-requested during live tailing) is dropped rather than shown twice. Caps
  // at BUFFER_CAP, dropping the oldest and forgetting their keys so the dedupe set can't
  // grow without bound over a long-running tail.
  const ingest = useCallback((newEvents: RuntimeLogEvent[]) => {
    if (newEvents.length === 0) return;
    const seen = seenKeysRef.current;
    const appended: SeqEvent[] = [];
    for (const ev of newEvents) {
      const key = `${ev.timestamp}|${ev.message}`;
      if (seen.has(key)) continue;
      seen.add(key);
      appended.push({ ...ev, key, seq: seqRef.current++ });
    }
    if (appended.length === 0) return;
    setEvents((prev) => {
      let merged = prev.concat(appended);
      if (merged.length > BUFFER_CAP) {
        const overflow = merged.length - BUFFER_CAP;
        for (let i = 0; i < overflow; i++) seen.delete(merged[i].key);
        merged = merged.slice(overflow);
      }
      return merged;
    });
    if (!atBottomRef.current) setNewSinceScroll((n) => n + appended.length);
  }, []);

  // Browse mode: a single window fetch, or a cursor follow-up ("Load more"). Cursor
  // pagination pages through the *fixed window* the initial request captured — CloudWatch's
  // nextToken never reaches past that window's end, so this can never itself tail forward;
  // "Load more" is for draining a backlog bigger than one page, not for following new output.
  const fetchLogs = useCallback(async (cursor?: string) => {
    setLoading(true);
    setError(null);
    try {
      const data = await applicationApi.logs(appId, cursor
        ? { container, cursor, previous: previous || undefined }
        : { container, minutes, previous: previous || undefined });
      ingest(data.events);
      setNextCursor(data.next_cursor);
      setTruncated(data.truncated);
      setHasLoaded(true);
    } catch (e: unknown) {
      const info = describeError(e);
      if (info.status === 400 && cursor) setNextCursor(null); // stale/expired cursor
      if (info.policyRefresh) setRefreshDialog({ open: true });
      setError(info);
    } finally {
      setLoading(false);
    }
  }, [appId, container, minutes, previous, ingest]);

  // Live mode: the endpoint has no forward-tailing primitive, so each tick just re-asks
  // for the last minute and relies on `ingest`'s dedupe to only append what's new. If that
  // minute alone paginates (busy app), follow up to MAX_LIVE_DRAINS pages so a burst
  // doesn't get stuck showing only its oldest 100 lines.
  const fetchLiveTick = useCallback(async () => {
    if (inFlightRef.current) return;
    inFlightRef.current = true;
    try {
      const data = await applicationApi.logs(appId, { container, minutes: 1, previous: previous || undefined });
      backoffRef.current = LIVE_POLL_MS;
      ingest(data.events);
      setTruncated(data.truncated);
      setHasLoaded(true);
      setError(null);

      let cursor = data.next_cursor;
      let drains = 0;
      while (cursor && drains < MAX_LIVE_DRAINS) {
        const more = await applicationApi.logs(appId, { container, cursor, previous: previous || undefined });
        ingest(more.events);
        setTruncated(more.truncated);
        cursor = more.next_cursor;
        drains += 1;
      }
    } catch (e: unknown) {
      const info = describeError(e);
      if (info.status === 429) {
        const retryMs = info.retryAfterSeconds ? info.retryAfterSeconds * 1000 : LIVE_POLL_MS * 2;
        backoffRef.current = Math.min(MAX_BACKOFF_MS, Math.max(LIVE_POLL_MS, retryMs));
      }
      if (info.policyRefresh) {
        setRefreshDialog({ open: true });
        setLive(false);
      } else if (info.status === 403 || info.status === 404 || info.status === 409) {
        setLive(false);
      }
      setError(info);
    } finally {
      inFlightRef.current = false;
    }
  }, [appId, container, previous, ingest]);

  useEffect(() => {
    if (!live) return;
    let cancelled = false;
    backoffRef.current = LIVE_POLL_MS;

    const tick = () => {
      if (cancelled) return;
      const run = document.visibilityState === 'hidden' ? Promise.resolve() : fetchLiveTick();
      run.finally(() => {
        if (!cancelled) liveTimerRef.current = setTimeout(tick, backoffRef.current);
      });
    };
    liveTimerRef.current = setTimeout(tick, 0);
    return () => {
      cancelled = true;
      if (liveTimerRef.current) clearTimeout(liveTimerRef.current);
    };
  }, [live, fetchLiveTick]);

  const handleContainerChange = (value: RuntimeLogContainer) => {
    if (value === container) return;
    setContainer(value);
    resetQuery();
  };

  const handleMinutesChange = (value: number) => {
    if (value === minutes) return;
    setMinutes(value);
    resetQuery();
  };

  const handlePreviousChange = (checked: boolean) => {
    setPrevious(checked);
    resetQuery();
  };

  const toggleLive = () => {
    setLive((prev) => {
      if (!prev) resetQuery();
      return !prev;
    });
  };

  // Pause auto-scroll once the user scrolls away from the bottom; resume (and clear the
  // "new lines" counter) once they scroll back down or hit Jump to latest.
  const handleScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < SCROLL_BOTTOM_THRESHOLD;
    atBottomRef.current = nearBottom;
    setAtBottom(nearBottom);
    if (nearBottom) setNewSinceScroll(0);
  };

  const jumpToLatest = () => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
    atBottomRef.current = true;
    setAtBottom(true);
    setNewSinceScroll(0);
  };

  useEffect(() => {
    if (atBottomRef.current && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [events]);

  // Log content is unredacted customer output — keep it in memory only, never
  // persisted, and drop it as soon as the panel goes away.
  useEffect(() => () => setEvents([]), []);

  const filterLower = filter.trim().toLowerCase();
  const visibleEvents = useMemo(
    () => (filterLower ? events.filter((e) => e.message.toLowerCase().includes(filterLower)) : events),
    [events, filterLower],
  );

  const copyVisible = () => {
    const text = visibleEvents.map((e) => `${e.timestamp} ${e.message}`).join('\n');
    navigator.clipboard.writeText(text).then(
      () => toast.success('Logs copied'),
      () => toast.error('Failed to copy logs'),
    );
  };

  const downloadVisible = () => {
    const text = visibleEvents.map((e) => `${e.timestamp} ${e.message}`).join('\n');
    const blob = new Blob([text], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${appId}-${container}-logs.log`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const isForbidden = error?.status === 403;

  return (
    <div className="rounded-xl panel p-4">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <ScrollText className="w-3.5 h-3.5 text-muted-foreground" />
          <p className="eyebrow">Live Logs</p>
        </div>
        {hasLoaded && !isForbidden && (
          <span className="font-mono text-[10px] text-muted-foreground/60">
            {filterLower ? `${visibleEvents.length} / ${events.length}` : events.length} lines
          </span>
        )}
      </div>

      {isForbidden ? (
        <div className="flex items-center gap-2.5 rounded-lg border border-hairline bg-surface-1 px-3 py-3">
          <Lock className="w-3.5 h-3.5 text-muted-foreground shrink-0" />
          <p className="text-xs text-muted-foreground">{error.message}</p>
        </div>
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-2 mb-3">
            <div className="flex items-center rounded-lg border border-hairline p-0.5 gap-0.5">
              {(['app', 'proxy'] as const).map((c) => (
                <button
                  key={c}
                  type="button"
                  onClick={() => handleContainerChange(c)}
                  className={`px-2.5 h-6 rounded-md text-[11px] font-mono uppercase tracking-wide transition-colors ${
                    container === c ? 'bg-surface-3 text-foreground' : 'text-muted-foreground hover:text-foreground'
                  }`}
                >
                  {c}
                </button>
              ))}
            </div>

            <div className="flex items-center rounded-lg border border-hairline p-0.5 gap-0.5">
              {WINDOW_OPTIONS.map((m) => (
                <button
                  key={m}
                  type="button"
                  disabled={live}
                  onClick={() => handleMinutesChange(m)}
                  className={`px-2.5 h-6 rounded-md text-[11px] font-mono uppercase tracking-wide transition-colors disabled:opacity-40 disabled:cursor-not-allowed ${
                    minutes === m ? 'bg-surface-3 text-foreground' : 'text-muted-foreground hover:text-foreground'
                  }`}
                >
                  {m}m
                </button>
              ))}
            </div>

            {computeType === 'eks' && (
              <label className="flex items-center gap-1.5 text-[11px] text-muted-foreground/80 select-none cursor-pointer">
                <input
                  type="checkbox"
                  checked={previous}
                  onChange={(e) => handlePreviousChange(e.target.checked)}
                  className="accent-brand"
                />
                Previous instance
              </label>
            )}

            <div className="relative">
              <Search className="w-3 h-3 text-muted-foreground/60 absolute left-2 top-1/2 -translate-y-1/2" />
              <Input
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
                placeholder="Filter…"
                className="h-6 w-32 pl-6 text-[11px] font-mono"
              />
            </div>

            <div className="ml-auto flex items-center gap-2">
              {events.length > 0 && (
                <>
                  <button onClick={copyVisible} title="Copy visible logs" className="text-muted-foreground hover:text-foreground transition-colors p-1 rounded">
                    <Copy className="w-3.5 h-3.5" />
                  </button>
                  <button onClick={downloadVisible} title="Download visible logs" className="text-muted-foreground hover:text-foreground transition-colors p-1 rounded">
                    <Download className="w-3.5 h-3.5" />
                  </button>
                </>
              )}
              <Button size="sm" variant={live ? 'default' : 'outline'} className="h-7 gap-1.5" onClick={toggleLive}>
                <span className={`w-1.5 h-1.5 rounded-full ${live ? 'bg-current animate-pulse' : 'bg-muted-foreground'}`} />
                Live
              </Button>
              {!live && (
                <Button
                  size="sm"
                  variant="outline"
                  className="h-7 gap-1.5"
                  disabled={loading}
                  onClick={() => {
                    resetQuery();
                    fetchLogs();
                  }}
                >
                  <RefreshCw className={`w-3 h-3 ${loading ? 'animate-spin' : ''}`} />
                  {hasLoaded ? 'Refresh' : 'Load logs'}
                </Button>
              )}
            </div>
          </div>

          {error && (
            <div className="rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 mb-3">
              <p className="text-xs text-destructive/80">{error.message}</p>
            </div>
          )}

          {hasLoaded && !error && events.length === 0 && (
            <p className="text-xs text-muted-foreground">No log events in this window.</p>
          )}

          {events.length > 0 && (
            <div className="relative">
              <div
                ref={scrollRef}
                onScroll={handleScroll}
                className="bg-surface-1 border border-hairline rounded-lg p-3 max-h-96 overflow-y-auto font-mono text-[11px] leading-relaxed"
              >
                {visibleEvents.length === 0 ? (
                  <p className="text-muted-foreground">No lines match &ldquo;{filter}&rdquo;.</p>
                ) : (
                  visibleEvents.map((event) => (
                    <div key={event.seq} className={`whitespace-pre-wrap break-all ${levelClass(event.message)}`}>
                      <span className="text-muted-foreground/60">{formatTimestamp(event.timestamp)}</span>{' '}
                      {highlightAll(event.message, filterLower)}
                    </div>
                  ))
                )}
              </div>

              {!atBottom && (
                <button
                  type="button"
                  onClick={jumpToLatest}
                  className="absolute bottom-3 right-3 flex items-center gap-1.5 rounded-full bg-brand text-primary-foreground px-3 py-1.5 text-[11px] font-medium shadow-lg hover:opacity-90 transition-opacity"
                >
                  <ArrowDownToLine className="w-3 h-3" />
                  Jump to latest{newSinceScroll > 0 ? ` (${newSinceScroll})` : ''}
                </button>
              )}
            </div>
          )}

          {truncated && (
            <p className="text-[10px] text-warning mt-1.5">Output truncated — narrow the time window for full coverage.</p>
          )}
          {!live && nextCursor && (
            <button
              type="button"
              onClick={() => fetchLogs(nextCursor)}
              disabled={loading}
              className="mt-2 flex items-center gap-1 text-[10px] font-mono uppercase tracking-widest text-muted-foreground/70 hover:text-brand transition-colors disabled:opacity-40"
            >
              <ChevronDown className="w-3 h-3" /> Load more
            </button>
          )}
        </>
      )}

      <PolicyRefreshDialog
        open={refreshDialog.open}
        onOpenChange={(open) => setRefreshDialog({ open })}
        infraId={infraId}
      />
    </div>
  );
}
