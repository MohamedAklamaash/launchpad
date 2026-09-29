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
 * One script, two snippet variants:
 *   - create_aws_role.sh is idempotent and does both the first-time bootstrap
 *     and the later policy refresh. Which callback it fires is decided by the
 *     credential the snippet injects (onboarding token vs script API key).
 *   - "bootstrap" renders the first-time snippet (onboarding token); "refresh"
 *     renders the re-run snippet (script API key) customers use when Launchpad
 *     widens the IAM surface (e.g. the codebuild:* regression that broke
 *     `create_project` for accounts onboarded before the fix).
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

// A single idempotent script backs every flow; the variant only changes the
// label/description and which credential the caller injects.
const SCRIPT_FILE = "create_aws_role.sh";

export type OnboardingVariant = "bootstrap" | "refresh";

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

function buildScriptInvocation(envExports: string[]): string {
  const lines = [...envExports];
  if (isLocalEnvironment()) {
    lines.push(`bash ${DEFAULT_LOCAL_PATH}/${SCRIPT_FILE}`);
  } else {
    lines.push(`curl -sSL ${resolveRemoteBaseUrl()}/${SCRIPT_FILE} | bash`);
  }
  return lines.join("\n");
}

export interface BootstrapEnvSource {
  id: string;
  code: string;
  compute_type: string;
  platform_account_id: string;
  platform_user: string;
  is_mock?: boolean;
}

/**
 * Builds the `export ...` lines the bootstrap snippet needs, from an infra (as returned
 * by create or reissue-token) plus its one-time onboarding token. Shared by the
 * post-create screen and the infra detail page's "show setup command" recovery flow so
 * the two never drift on which vars the script expects.
 */
export function buildBootstrapEnvExports(
  infra: BootstrapEnvSource,
  onboardingToken: string,
  callbackUrl: string,
): string[] {
  const exports = [
    `export LAUNCHPAD_INFRA_ID=${infra.id}`,
    `export LAUNCHPAD_CALLBACK_URL=${callbackUrl}`,
    `export LAUNCHPAD_ONBOARDING_TOKEN=${onboardingToken}`,
    `export LAUNCHPAD_EXTERNAL_ID=${infra.id}`,
    `export LAUNCHPAD_COMPUTE_TYPE=${infra.compute_type}`,
    `export LAUNCHPAD_PLATFORM_ACCOUNT_ID=${infra.platform_account_id}`,
    `export LAUNCHPAD_PLATFORM_USER=${infra.platform_user}`,
  ];
  if (infra.is_mock) {
    exports.push(`export LAUNCHPAD_MOCK=1`, `export LAUNCHPAD_ACCOUNT_ID=${infra.code}`);
  }
  return exports;
}

export function resolveOnboardingScript(
  variant: OnboardingVariant,
  envExports: string[] = [],
): ResolvedScript {
  const local = isLocalEnvironment();
  const base = local ? DEFAULT_LOCAL_PATH : resolveRemoteBaseUrl();

  const meta: Record<OnboardingVariant, { label: string; description: string }> = {
    bootstrap: {
      label: "Bootstrap script",
      description:
        "Creates LaunchpadDeploymentRole in your AWS account and notifies Launchpad when ready.",
    },
    refresh: {
      label: "Refresh policy script",
      description:
        "Re-applies the latest LaunchpadDeploymentPolicy and refreshes the trust policy in your AWS account. Re-run this if deployments fail with an AccessDenied error after onboarding.",
    },
  };

  return {
    label: meta[variant].label,
    description: meta[variant].description,
    invocation: buildScriptInvocation(envExports),
    location: `${base}/${SCRIPT_FILE}`,
    locationIsUrl: !local,
  };
}
