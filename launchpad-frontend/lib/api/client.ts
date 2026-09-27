import axios, { AxiosInstance } from 'axios';

const API_GATEWAY = process.env.NEXT_PUBLIC_API_GATEWAY_URL || 'http://localhost:8000';

export const apiClient: AxiosInstance = axios.create({
  baseURL: API_GATEWAY,
  headers: {
    'Content-Type': 'application/json',
  },
});

// Add token to requests
apiClient.interceptors.request.use((config) => {
  if (typeof window !== 'undefined') {
    const token = localStorage.getItem('access_token');
    if (token) {
      config.headers.Authorization = `Bearer ${token}`;
    }
  }
  return config;
});

// A blob-response request (evidence pack, exit export) gets its error body back as a
// Blob too, so the usual `err.response?.data?.code` read is undefined — read the blob's
// text and parse it to recover the server's actual error code.
async function extractErrorCode(error: unknown): Promise<string | undefined> {
  const data = (error as { response?: { data?: unknown } })?.response?.data;
  if (data instanceof Blob) {
    try {
      const parsed = JSON.parse(await data.text()) as { code?: string };
      return parsed.code;
    } catch {
      return undefined;
    }
  }
  return (data as { code?: string } | undefined)?.code;
}

const REAUTH_REQUIRED_CODE = 'reauth_required';

// Best-effort: revoke every refresh token for the current user before forcing a re-login.
// A stale-auth_time 401 means whatever refresh token is sitting in this browser (or one
// stolen from it) can no longer silently mint a fresh session for a sensitive action —
// closing that door here, not just locally clearing storage, is the point of revoking
// server-side rather than merely discarding the local copy.
async function revokeCurrentUserSessions(): Promise<void> {
  try {
    const stored = localStorage.getItem('user');
    const userId = stored ? (JSON.parse(stored) as { id?: string }).id : undefined;
    if (!userId) return;
    await axios.post(`${API_GATEWAY}/api/auth/revoke`, { userId });
  } catch {
    // Never block the redirect on this — an unreachable auth-service must not trap the
    // user on the current page instead of sending them to re-login.
  }
}

// Handle token refresh on 401
apiClient.interceptors.response.use(
  (response) => response,
  async (error) => {
    const originalRequest = error.config;

    if (error.response?.status === 401 && (await extractErrorCode(error)) === REAUTH_REQUIRED_CODE) {
      // A silent refresh only proves the caller still holds a valid refresh token, not
      // that they recently authenticated — the whole point of this code. Force a real
      // re-login instead of transparently minting a fresh access token and retrying, the
      // way an ordinary expired-token 401 does below.
      await revokeCurrentUserSessions();
      localStorage.removeItem('access_token');
      localStorage.removeItem('refresh_token');
      localStorage.removeItem('user');
      window.location.href = '/login';
      return Promise.reject(error);
    }

    if (error.response?.status === 401 && !originalRequest._retry) {
      originalRequest._retry = true;

      try {
        const refreshToken = localStorage.getItem('refresh_token');
        if (!refreshToken) {
          throw new Error('No refresh token');
        }

        let data: { access_token?: string; accessToken?: string; refresh_token?: string; refreshToken?: string };
        try {
          const res = await axios.post(`${API_GATEWAY}/api/user/refresh`, { refresh_token: refreshToken });
          data = res.data;
        } catch {
          const res = await axios.post(`${API_GATEWAY}/api/auth/refresh`, { token: refreshToken });
          data = res.data;
        }
        const newAccess = data.access_token || data.accessToken;
        const newRefresh = data.refresh_token || data.refreshToken;
        if (!newAccess || !newRefresh) throw new Error('Refresh failed');
        localStorage.setItem('access_token', newAccess);
        localStorage.setItem('refresh_token', newRefresh);

        originalRequest.headers.Authorization = `Bearer ${newAccess}`;
        return apiClient(originalRequest);
      } catch (refreshError) {
        localStorage.removeItem('access_token');
        localStorage.removeItem('refresh_token');
        localStorage.removeItem('user');
        window.location.href = '/login';
        return Promise.reject(refreshError);
      }
    }

    return Promise.reject(error);
  }
);
