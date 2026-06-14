/**
 * Single source of truth for the onboarding shell scripts the dashboard tells
 * customers to run against their own AWS account.
 *
 * SECURITY-CRITICAL: the URL rendered by this module is a `curl | bash`
 * source. Anything served from it executes with the customer's AWS
 * credentials. To preserve supply-chain integrity we (a) pin the script
 * source to a specific git ref in production (no moving `main`) and
 * (b) reject overrides that aren't HTTPS (or an explicit localhost dev
 * URL). If you change this file, re-read those two invariants before
 * shipping.
 *
 * Two scripts:
 *   - create_aws_role.sh — first-time bootstrap. Creates the role, attaches
 *     the policy, and posts back to the onboarding callback.
 *   - update_aws_role.sh — refresh path. Re-applies the policy in place. The
 *     customer needs this whenever Launchpad widens the IAM surface (e.g. the
 *     codebuild:* regression that broke `create_project` for accounts onboarded
 *     before the fix).
 *
 * Environment resolution
 * ----------------------
 *   - `NEXT_PUBLIC_LAUNCHPAD_SCRIPT_BASE_URL` overrides everything when set
 *     to a valid URL. Accepted forms: `https://...`, `http://localhost...`,
 *     `http://127.0.0.1...`. Anything else (including whitespace-only
 *     strings) is rejected with a one-time console warning and we fall
 *     through to the next source.
 *   - In production (no override) we compose the URL from
 *     `NEXT_PUBLIC_LAUNCHPAD_SCRIPT_REF` (commit SHA or signed tag) and
 *     `NEXT_PUBLIC_LAUNCHPAD_SCRIPT_REPO` (defaults to the canonical repo).
 *     If `NEXT_PUBLIC_LAUNCHPAD_SCRIPT_REF` is missing in a production
 *     build, this module throws at load time — pinning is mandatory.
 *   - In non-production (no override) we render the local repo path
 *     (`bash ./app_scripts/<script>.sh`) so engineers can iterate without
 *     pushing. No GitHub fetch in dev.
 */

// Default repo used when NEXT_PUBLIC_LAUNCHPAD_SCRIPT_REPO is not set. The
// ref (commit SHA or signed tag) is intentionally NOT defaulted — pinning is
// required, see `composeProdBaseUrl` below.
const DEFAULT_SCRIPT_REPO = "MohamedAklamaash/launchpad";

// Default local path mirrors the repo layout. Customers running the dashboard
// against `next dev` will see the path relative to the repo root, which is the
// natural thing to copy into a terminal sitting in that checkout.
const DEFAULT_LOCAL_PATH = "./app_scripts";

export type OnboardingScript = "create_aws_role.sh" | "update_aws_role.sh";

interface ResolvedScript {
  /** Human-readable label shown in the UI ("Bootstrap script" etc.). */
  label: string;
  /** One-line description shown under the label. */
  description: string;
  /**
   * The exact command we want the customer to paste into a shell. Built by
   * `buildScriptInvocation` so the environment-vs-curl difference is in one
   * place.
   */
  invocation: string;
  /**
   * The raw location (URL or filesystem path) of the script, for a "View
   * source" link. In local mode this is a path string, not a URL, so it is
   * rendered as plain text rather than an anchor.
   */
  location: string;
  /** True when `location` is a clickable URL. */
  locationIsUrl: boolean;
}

// Module-scoped so we only warn once per page-load even if multiple scripts
// resolve in the same render.
let warnedAboutOverride = false;

/**
 * Read `NEXT_PUBLIC_LAUNCHPAD_SCRIPT_BASE_URL`, trim it, and validate it as
 * an HTTPS URL (or localhost for dev). Returns `null` if unset, empty after
 * trimming, or rejected by the protocol/hostname check.
 *
 * Hostname is checked via WHATWG `URL` parsing rather than a string prefix —
 * `startsWith("http://localhost")` would accept `http://localhost.evil.com`
 * (same trick works for `127.0.0.1.evil.com`). Anything not HTTPS, and not
 * HTTP-on-localhost-or-127.0.0.1, is rejected with a one-time warning.
 */
function getOverrideBaseUrl(): string | null {
  const raw = process.env.NEXT_PUBLIC_LAUNCHPAD_SCRIPT_BASE_URL;
  if (!raw) return null;
  const trimmed = raw.trim();
  if (!trimmed) return null;

  try {
    const u = new URL(trimmed);
    if (u.protocol === "https:") return trimmed.replace(/\/+$/, "");
    if (
      u.protocol === "http:" &&
      (u.hostname === "localhost" || u.hostname === "127.0.0.1")
    ) {
      return trimmed.replace(/\/+$/, "");
    }
  } catch {
    /* fall through to rejection */
  }

  if (!warnedAboutOverride) {
    warnedAboutOverride = true;
    // Loud signal because this URL is a `curl | bash` source: an HTTP
    // override on a public network would be a MITM vector.
    console.warn(
      "[onboarding-scripts] NEXT_PUBLIC_LAUNCHPAD_SCRIPT_BASE_URL " +
        "rejected — must be https:// or http://localhost (or http://127.0.0.1). " +
        "Falling back to the pinned production URL.",
    );
  }
  return null;
}

/**
 * Distinguishable error type so the page can render a misconfig banner
 * instead of crashing the whole route. Anything thrown from this module
 * that isn't an `OnboardingMisconfigurationError` is a real bug.
 */
export class OnboardingMisconfigurationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "OnboardingMisconfigurationError";
  }
}

/**
 * Returns a human-readable message describing why onboarding script rendering
 * would fail, or `null` if everything required is present. Safe to call from
 * any environment (dev returns `null` because dev uses the local repo path).
 */
export function getOnboardingMisconfiguration(): string | null {
  if (isLocalEnvironment()) return null;
  // An accepted override skips the SHA pinning requirement.
  if (getOverrideBaseUrl() !== null) return null;
  const ref = process.env.NEXT_PUBLIC_LAUNCHPAD_SCRIPT_REF?.trim();
  if (!ref) {
    return (
      "NEXT_PUBLIC_LAUNCHPAD_SCRIPT_REF must be set to a pinned commit SHA " +
      "or signed tag. Contact your platform admin."
    );
  }
  return null;
}

/**
 * Build the production GitHub raw URL pinned to a specific ref. Throws an
 * `OnboardingMisconfigurationError` if the ref is missing in a production
 * build so deploys can't silently fall back to a moving `main`.
 */
function composeProdBaseUrl(): string {
  const ref = process.env.NEXT_PUBLIC_LAUNCHPAD_SCRIPT_REF?.trim();
  const repo =
    process.env.NEXT_PUBLIC_LAUNCHPAD_SCRIPT_REPO?.trim() || DEFAULT_SCRIPT_REPO;
  if (!ref) {
    throw new OnboardingMisconfigurationError(
      "NEXT_PUBLIC_LAUNCHPAD_SCRIPT_REF must be set to a commit SHA or " +
        "signed tag in production builds — refusing to fall back to a " +
        "moving target. See lib/onboarding-scripts.ts.",
    );
  }
  return `https://raw.githubusercontent.com/${repo}/${ref}/app_scripts`;
}

function isLocalEnvironment(): boolean {
  // Mirror the convention used elsewhere in the frontend (lib/api/client.ts):
  // explicit (valid) override > NODE_ENV check. Staging deploys should set
  // NEXT_PUBLIC_LAUNCHPAD_SCRIPT_BASE_URL to whatever they actually serve.
  if (getOverrideBaseUrl() !== null) return false;
  return process.env.NODE_ENV !== "production";
}

function resolveRemoteBaseUrl(): string {
  // Same precedence everywhere: validated override beats the pinned default.
  const override = getOverrideBaseUrl();
  if (override !== null) return override.replace(/\/+$/, "");
  return composeProdBaseUrl().replace(/\/+$/, "");
}

function buildScriptInvocation(
  script: OnboardingScript,
  envExports: string[],
): string {
  const lines = [...envExports];
  if (isLocalEnvironment()) {
    lines.push(`bash ${DEFAULT_LOCAL_PATH}/${script}`);
  } else {
    lines.push(`curl -sSL ${resolveRemoteBaseUrl()}/${script} | bash`);
  }
  return lines.join("\n");
}

export function resolveOnboardingScript(
  script: OnboardingScript,
  envExports: string[] = [],
): ResolvedScript {
  const local = isLocalEnvironment();
  const base = local ? DEFAULT_LOCAL_PATH : resolveRemoteBaseUrl();

  const meta: Record<OnboardingScript, { label: string; description: string }> = {
    "create_aws_role.sh": {
      label: "Bootstrap script",
      description:
        "Creates LaunchpadDeploymentRole in your AWS account and notifies Launchpad when ready.",
    },
    "update_aws_role.sh": {
      label: "Refresh policy script",
      description:
        "Re-applies the latest LaunchpadDeploymentPolicy and refreshes the trust policy in your AWS account. Re-run this if deployments fail with an AccessDenied error after onboarding.",
    },
  };

  return {
    label: meta[script].label,
    description: meta[script].description,
    invocation: buildScriptInvocation(script, envExports),
    location: `${base}/${script}`,
    locationIsUrl: !local,
  };
}
