/**
 * Minimal SSE client over `fetch`, not `EventSource`: the backend's stream endpoints
 * require a bearer token (gateway-service passes `Authorization` straight through,
 * never verifying it itself, see root CLAUDE.md), and `EventSource` has no way to send
 * a custom header. `fetch` plus manual `ReadableStream` parsing is the trade-off.
 *
 * A connection reconnects itself with backoff on a transport failure (network error,
 * non-2xx open, or the body stream ending without an `event: end` frame), carrying
 * `Last-Event-ID` so the server can resume from the watermark the last frame reported.
 * After `MAX_CONSECUTIVE_FAILURES` of those, or immediately on an open failure whose
 * status will never succeed by retrying (401/403/404/409), it gives up and calls
 * `onFallback` once. The caller is expected to fall back to its existing polling,
 * exactly as if SSE had never been tried.
 *
 * A clean `event: end` from the server (hard stream duration, or a completed build) is
 * not treated as a failure: it calls `onEnd` and stops without retrying. Whether to
 * reconnect afterwards (runtime logs, which the server always ends eventually) or stay
 * stopped (build logs, once `build_finished`) is a decision specific to each caller, not
 * something this module can know.
 */

const INITIAL_BACKOFF_MS = 1_000;
const MAX_BACKOFF_MS = 30_000;
const MAX_CONSECUTIVE_FAILURES = 3;
// Statuses a retry can never turn into a success: the caller's existing polling path
// (which already knows how to render each of these) takes over immediately instead of
// burning a backoff cycle first. 401 included so the next SSE attempt benefits from
// apiClient's own token-refresh interceptor running on the fallback's first poll.
const PERMANENT_FAILURE_STATUSES = new Set([400, 401, 403, 404, 409]);

export interface SSEHttpError {
  response: {
    status: number;
    data?: unknown;
    headers?: Record<string, string>;
  };
}

export interface SSEHandlers {
  /** `id` is the frame's `id:` field, if present. Callers that want to resume across
   * an `onEnd` reconnect should remember the most recent one and pass it back in. */
  onEvent: (data: unknown, eventType: string | null, id: string | null) => void;
  onEnd?: (reason: string | undefined) => void;
  onFallback: (lastError?: SSEHttpError) => void;
}

export interface SSEConnection {
  close: () => void;
}

function apiGatewayUrl(): string {
  return process.env.NEXT_PUBLIC_API_GATEWAY_URL || 'http://localhost:8000';
}

function buildUrl(path: string, params: Record<string, string | undefined>): string {
  const url = new URL(path, apiGatewayUrl());
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined) url.searchParams.set(key, value);
  }
  return url.toString();
}

function parseFrame(raw: string): { event: string | null; id: string | null; data: unknown } | null {
  let eventType: string | null = null;
  let id: string | null = null;
  const dataLines: string[] = [];
  for (const line of raw.split('\n')) {
    if (line === '' || line.startsWith(':')) continue; // heartbeat/comment line
    if (line.startsWith('event:')) eventType = line.slice('event:'.length).trim();
    else if (line.startsWith('id:')) id = line.slice('id:'.length).trim();
    else if (line.startsWith('data:')) dataLines.push(line.slice('data:'.length).trim());
  }
  if (dataLines.length === 0) return null;
  try {
    return { event: eventType, id, data: JSON.parse(dataLines.join('\n')) };
  } catch {
    return null;
  }
}

export function connectSSE(
  path: string,
  params: Record<string, string | undefined>,
  handlers: SSEHandlers,
  initialLastEventId?: string,
): SSEConnection {
  let closed = false;
  let lastEventId = initialLastEventId;
  let consecutiveFailures = 0;
  let retryTimer: ReturnType<typeof setTimeout> | null = null;
  let controller: AbortController | null = null;

  const fail = (lastError?: SSEHttpError) => {
    if (closed) return;
    consecutiveFailures += 1;
    if (consecutiveFailures > MAX_CONSECUTIVE_FAILURES) {
      close();
      handlers.onFallback(lastError);
      return;
    }
    const delay = Math.min(MAX_BACKOFF_MS, INITIAL_BACKOFF_MS * 2 ** (consecutiveFailures - 1));
    retryTimer = setTimeout(run, delay);
  };

  async function run() {
    if (closed) return;
    controller = new AbortController();

    const token = typeof window !== 'undefined' ? localStorage.getItem('access_token') : null;
    const headers: Record<string, string> = { Accept: 'text/event-stream' };
    if (token) headers.Authorization = `Bearer ${token}`;
    if (lastEventId) headers['Last-Event-ID'] = lastEventId;

    let response: Response;
    try {
      response = await fetch(buildUrl(path, params), { headers, signal: controller.signal });
    } catch {
      fail();
      return;
    }
    if (closed) return;

    if (!response.ok || !response.body) {
      let data: unknown;
      try {
        data = await response.json();
      } catch {
        data = undefined;
      }
      // The effect's cleanup can call close() while this await was in flight (it does
      // not abort a response whose headers already arrived), so re-check before acting
      // on a connection the caller has already torn down.
      if (closed) return;
      const httpError: SSEHttpError = {
        response: { status: response.status, data, headers: Object.fromEntries(response.headers.entries()) },
      };
      if (PERMANENT_FAILURE_STATUSES.has(response.status)) {
        close();
        handlers.onFallback(httpError);
        return;
      }
      fail(httpError);
      return;
    }

    consecutiveFailures = 0;
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let endedCleanly = false;

    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let boundary = buffer.indexOf('\n\n');
        while (boundary !== -1) {
          const frame = parseFrame(buffer.slice(0, boundary));
          buffer = buffer.slice(boundary + 2);
          if (frame) {
            if (frame.id) lastEventId = frame.id;
            if (frame.event === 'end') {
              endedCleanly = true;
              const reason = (frame.data as { reason?: string } | undefined)?.reason;
              handlers.onEnd?.(reason);
            } else {
              handlers.onEvent(frame.data, frame.event, frame.id);
            }
          }
          boundary = buffer.indexOf('\n\n');
        }
        if (endedCleanly) break;
      }
    } catch {
      if (closed) return;
      fail();
      return;
    }

    if (closed) return;
    if (endedCleanly) {
      // A clean server-initiated end stops here. Reconnecting (or not) is the
      // caller's call, made from its `onEnd` handler, not this module's.
      return;
    }
    // The body ended without an `event: end` frame: an upstream/network drop rather
    // than a graceful close. Same backoff-then-fallback path as an open failure.
    fail();
  }

  function close() {
    closed = true;
    if (retryTimer) clearTimeout(retryTimer);
    controller?.abort();
  }

  run();

  return { close };
}
