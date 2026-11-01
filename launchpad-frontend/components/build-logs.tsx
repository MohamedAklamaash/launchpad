'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { ArrowDownToLine, Hammer, Lock } from 'lucide-react';
import { applicationApi } from '@/lib/api/applications';
import { connectSSE, SSEConnection } from '@/lib/sse';
import { BuildLogEvent } from '@/types/application';

// Mirrors app-live-logs.tsx's cadence reasoning: settings.RATE_BUDGET_BUILD_LOGS_* budgets
// 40 calls/60s per user — 4s (15/min) leaves headroom for a second tab or a manual retry
// without tripping 429, while still feeling live while a build is in progress.
const LIVE_POLL_MS = 4_000;
const MAX_BACKOFF_MS = 60_000;
const BUFFER_CAP = 4000;
const SCROLL_BOTTOM_THRESHOLD = 48;

interface SeqEvent extends BuildLogEvent {
  seq: number;
  key: string;
}

interface LogError {
  status: number | null;
  message: string;
}

function describeError(e: unknown): LogError {
  const err = e as { response?: { status?: number; data?: { error?: string; code?: string }; headers?: Record<string, string> } };
  const resp = err.response;
  const status = resp?.status ?? null;
  const code = resp?.data?.code;

  switch (status) {
    case 403:
      return { status, message: 'Only the infrastructure owner can view build logs.' };
    case 404:
      return { status, message: 'No build logs available.' };
    case 409:
      return {
        status,
        message: code === 'legacy_build'
          ? 'This deployment used the pre-platform build path, no build logs are available.'
          : (resp?.data?.error || 'Build logs are not available for this deployment.'),
      };
    case 429: {
      const retryAfter = resp?.headers?.['retry-after'];
      const seconds = retryAfter ? parseInt(retryAfter, 10) : NaN;
      return {
        status,
        message: Number.isFinite(seconds) ? `Too many requests. Retrying in ${seconds}s.` : 'Too many requests. Retrying shortly.',
      };
    }
    case 502:
    case 503:
      return { status, message: 'Build log service is temporarily unavailable. Try again shortly.' };
    default:
      return { status, message: resp?.data?.error || 'Failed to load build logs.' };
  }
}

function formatTimestamp(ts: string): string {
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return ts;
  return d.toLocaleTimeString(undefined, { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

interface Props {
  appId: string;
  /** Omit to tail the in-progress build (`/build-logs/current`); pass once a Deployment
   * row exists (deploy finished) to read its own completed build's full log. */
  deploymentId?: string;
  /** Called once the in-progress tail 404s — the attempt finished, so the caller should
   * stop showing this panel (deployment history will have the row with its own
   * "Build logs" action once it refreshes). No-op in deploymentId mode. */
  onFinished?: () => void;
}

export function BuildLogs({ appId, deploymentId, onFinished }: Props) {
  const [events, setEvents] = useState<SeqEvent[]>([]);
  const [complete, setComplete] = useState(false);
  const [error, setError] = useState<LogError | null>(null);
  const [loading, setLoading] = useState(true);
  const [atBottom, setAtBottom] = useState(true);
  const [newSinceScroll, setNewSinceScroll] = useState(0);
  // SSE is tried first; see app-live-logs.tsx's identically-named state for why a
  // repeatedly-failing stream flips this to false and hands off to the polling effect
  // below instead of this component trying to recover on its own.
  const [sseActive, setSseActive] = useState(true);

  const seqRef = useRef(0);
  const seenKeysRef = useRef<Set<string>>(new Set());
  const atBottomRef = useRef(true);
  const inFlightRef = useRef(false);
  const cursorRef = useRef<string | null>(null);
  const backoffRef = useRef(LIVE_POLL_MS);
  const scrollRef = useRef<HTMLDivElement>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const sseLastEventIdRef = useRef<string | undefined>(undefined);

  const ingest = useCallback((newEvents: BuildLogEvent[]) => {
    if (newEvents.length === 0) return;
    const seen = seenKeysRef.current;
    const appended: SeqEvent[] = [];
    for (const ev of newEvents) {
      const key = `${ev.container}|${ev.timestamp}|${ev.message}`;
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

  const fetchTick = useCallback(async () => {
    if (inFlightRef.current) return;
    inFlightRef.current = true;
    try {
      const data = deploymentId
        ? await applicationApi.buildLogs(appId, deploymentId, { cursor: cursorRef.current || undefined })
        : await applicationApi.currentBuildLogs(appId, { cursor: cursorRef.current || undefined });
      backoffRef.current = LIVE_POLL_MS;
      ingest(data.events);
      cursorRef.current = data.next_cursor;
      setComplete(data.complete);
      setError(null);
    } catch (e: unknown) {
      const info = describeError(e);
      if (info.status === 400 && cursorRef.current) {
        // A cursor handed over from a falling-back SSE connection (or a stale poll
        // cursor) that the JSON endpoint now rejects, same shape as a build id moving
        // on underneath it. Restart from the head rather than staying wedged on a
        // cursor that will never validate again.
        cursorRef.current = null;
      }
      if (info.status === 429) {
        backoffRef.current = Math.min(MAX_BACKOFF_MS, backoffRef.current * 2);
      } else if (info.status === 404 && !deploymentId) {
        setComplete(true);
        onFinished?.();
      } else if (info.status === 403 || info.status === 404 || info.status === 409) {
        setComplete(true);
      }
      setError(info);
    } finally {
      setLoading(false);
      inFlightRef.current = false;
    }
  }, [appId, deploymentId, ingest, onFinished]);

  useEffect(() => {
    if (complete || sseActive) return;
    let cancelled = false;
    const tick = () => {
      if (cancelled) return;
      fetchTick().finally(() => {
        if (!cancelled && !complete) timerRef.current = setTimeout(tick, backoffRef.current);
      });
    };
    timerRef.current = setTimeout(tick, 0);
    return () => {
      cancelled = true;
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, [complete, sseActive, fetchTick]);

  // SSE live tail: one long-lived connection instead of a 4s poll. `build_finished` is
  // terminal: the build has stopped, so nothing further will ever arrive on either
  // stream, and this stops rather than reconnecting. A hard 10-minute duration
  // reconnects with the last frame's cursor; any other end reason falls back to
  // polling, same reasoning as app-live-logs.tsx's runtime-logs stream.
  useEffect(() => {
    if (complete || !sseActive) return;
    let cancelled = false;
    let connection: SSEConnection | null = null;

    const markLoaded = () => setLoading(false);

    // The JSON endpoint's pagination cursor and the SSE watermark cursor are the same
    // signed token for build logs (both wrap build_logs_cursor's per-container forward
    // tokens), so handing it to the poll loop resumes exactly where the stream left off
    // instead of re-reading from the start.
    const fallBackToPolling = () => {
      if (sseLastEventIdRef.current) cursorRef.current = sseLastEventIdRef.current;
      setSseActive(false);
    };

    const open = () => {
      if (cancelled) return;
      const path = deploymentId
        ? `/api/applications/${appId}/deployments/${deploymentId}/build-logs/stream`
        : `/api/applications/${appId}/build-logs/current/stream`;
      connection = connectSSE(
        path, {},
        {
          onEvent: (data, eventType, id) => {
            if (id) sseLastEventIdRef.current = id;
            markLoaded();
            if (eventType !== 'message') return;
            const payload = data as { events?: BuildLogEvent[] };
            if (payload.events) ingest(payload.events);
            setError(null);
          },
          onEnd: (reason) => {
            markLoaded();
            if (cancelled) return;
            if (reason === 'build_finished') {
              setComplete(true);
              if (!deploymentId) onFinished?.();
              return;
            }
            if (reason === 'max_duration') {
              // Expected and benign: the server always ends a stream by this point.
              open();
              return;
            }
            // Any other reason (an upstream hiccup the server already gave up on) hands
            // off to polling rather than hot-looping open/fail/open against the
            // backend's per-user budget, same reasoning as app-live-logs.tsx.
            fallBackToPolling();
          },
          onFallback: () => {
            markLoaded();
            if (!cancelled) fallBackToPolling();
          },
        },
        sseLastEventIdRef.current,
      );
    };

    open();
    return () => {
      cancelled = true;
      connection?.close();
    };
  }, [complete, sseActive, appId, deploymentId, ingest, onFinished]);

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

  // Build output is unredacted-beyond-rendering-safety platform/customer content — keep
  // it in memory only, never persisted, and drop it as soon as the panel goes away.
  useEffect(() => () => setEvents([]), []);

  const isForbidden = error?.status === 403;

  return (
    <div className="rounded-lg border border-border bg-card p-4">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <Hammer className="size-3.5 text-muted-foreground" />
          <h2 className="text-sm font-medium text-foreground">Build logs</h2>
        </div>
        {!loading && events.length > 0 && (
          <span className="font-mono text-xs text-muted-foreground">
            {events.length} lines{complete ? '' : ' · live'}
          </span>
        )}
      </div>

      {isForbidden ? (
        <div className="flex items-center gap-2.5 rounded-lg border border-border bg-surface-1 px-3 py-3">
          <Lock className="size-3.5 text-muted-foreground shrink-0" />
          <p className="text-xs text-muted-foreground">{error.message}</p>
        </div>
      ) : (
        <>
          {error && error.status !== 429 && (
            <div className="rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 mb-3">
              <p className="text-xs text-muted-foreground">{error.message}</p>
            </div>
          )}

          {loading && events.length === 0 && (
            <p className="text-xs text-muted-foreground">Loading build logs…</p>
          )}

          {!loading && events.length === 0 && !error && (
            <p className="text-xs text-muted-foreground">No log output yet.</p>
          )}

          {events.length > 0 && (
            <div className="relative">
              <div
                ref={scrollRef}
                onScroll={handleScroll}
                className="bg-surface-1 border border-border rounded-lg p-3 max-h-96 overflow-y-auto font-mono text-xs leading-relaxed"
              >
                {events.map((event) => (
                  <div key={event.seq} className="whitespace-pre-wrap break-all text-foreground/90">
                    <span className="text-muted-foreground">{formatTimestamp(event.timestamp)}</span>{' '}
                    <span className="text-muted-foreground text-xs">[{event.container}]</span>{' '}
                    {event.message}
                  </div>
                ))}
              </div>

              {!atBottom && (
                <button
                  type="button"
                  onClick={jumpToLatest}
                  className="absolute bottom-3 right-3 flex items-center gap-1.5 rounded-full bg-brand text-primary-foreground px-3 py-1.5 text-xs font-medium shadow-popover hover:opacity-90 transition-opacity"
                >
                  <ArrowDownToLine className="size-3" />
                  Jump to latest{newSinceScroll > 0 ? ` (${newSinceScroll})` : ''}
                </button>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}
