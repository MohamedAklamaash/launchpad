/**
 * CloudFormation "Launch stack" onboarding — the GUI-path sibling to
 * `lib/onboarding-scripts.ts`'s `create_aws_role.sh` snippets.
 *
 * See plan/F8-cloudformation-onboarding.md, Decision §1 ("Template generation"), §3
 * ("Hosting"), and §5a ("Never-downgrade on template swap") for the source of every
 * constant and rule below.
 *
 * SECURITY-CRITICAL, same class of concern as onboarding-scripts.ts: the quick-create
 * link's `templateURL` is fetched by the customer's own browser/AWS console, so it must
 * resolve to a pinned, versioned S3 object — never a moving target. Hosting (F8 ticket 4)
 * has not shipped in this repo yet, so there is no default bucket to fall back to; both
 * env vars below are required before a template URL can be built at all. Never hardcode
 * a bucket name here as a "temporary" default — see the task description for this file.
 *
 * Environment resolution
 * ----------------------
 *   - `NEXT_PUBLIC_LAUNCHPAD_CFN_TEMPLATE_BASE_URL` must be set to the platform template
 *     bucket's base URL (virtual-hosted or path-style S3, or http://localhost /
 *     127.0.0.1 for local iteration against a mock). Anything else is rejected.
 *   - `NEXT_PUBLIC_LAUNCHPAD_CFN_TEMPLATE_REF` must be set to the git ref the templates
 *     were published under (mirrors `NEXT_PUBLIC_LAUNCHPAD_SCRIPT_REF`'s "standing
 *     release action" pinning contract).
 *   - Unlike the shell script, there is no "local repo path" fallback for a CFN template:
 *     `cloudformation:CreateStack`'s `TemplateURL` must always be a reachable HTTPS/S3
 *     URL, even in dev. Missing config surfaces as `getCfnOnboardingMisconfiguration()`,
 *     never a load-time throw — CI's `pnpm run build` runs with neither var set.
 */

import { ComputeType } from "@/types/infrastructure";

// IAM resources are global; the CFN *stack* created by this template has to live
// somewhere, and every caller (this quick-create link, the backend's cfn-verify/
// cfn-refresh, and nuke_worker.py's CFN branch) must agree on where to find it. Fixed
// platform constant — deliberately NOT an env var, matching
// `api/services/cfn_onboarding.py`'s `CFN_STACK_REGION`, and never a customer-facing
// `region=` parameter. If this ever needs to change it must change in both places in the
// same release.
export const CFN_STACK_REGION = "us-east-1";

export const CFN_ROLE_NAME = "LaunchpadDeploymentRole";

/**
 * Mirrors the backend's `CFN_ONBOARDING_ENABLED` flag (plan §9) — default off, same
 * precedent as `EKS_ENABLED`. The whole CFN onboarding UI (wizard tab, detail-page
 * "Verify connection", refresh dialog) stays hidden until both the frontend and backend
 * flags are on; flipping only the frontend one would surface UI whose calls 404 (the
 * backend views return 404, not 403, when its own flag is off).
 */
export function isCfnOnboardingEnabled(): boolean {
  return process.env.NEXT_PUBLIC_CFN_ONBOARDING_ENABLED === "true";
}

// Plan §3: degrade to a copy-paste parameter block instead of a quick-create link once
// the merged ExternalIds list or the resulting URL gets this large.
const EXTERNAL_IDS_QUICK_CREATE_CAP = 20;
const QUICK_CREATE_URL_LENGTH_CAP = 1800;

const TEMPLATE_FILENAME: Record<ComputeType, string> = {
  ecs_fargate: "launchpad-deployment-role.ecs-fargate.template.json",
  eks: "launchpad-deployment-role.eks.template.json",
};

/**
 * Distinguishable error type so a page can render a misconfig banner instead of
 * crashing — mirrors `OnboardingMisconfigurationError` in onboarding-scripts.ts.
 */
export class CfnOnboardingMisconfigurationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "CfnOnboardingMisconfigurationError";
  }
}

let warnedAboutBaseUrl = false;

/**
 * Validates `NEXT_PUBLIC_LAUNCHPAD_CFN_TEMPLATE_BASE_URL` as either an S3 bucket base
 * URL (virtual-hosted-style `https://<bucket>.s3[.<region>].amazonaws.com`, or
 * path-style `https://s3[.<region>].amazonaws.com/<bucket>`) or an explicit localhost
 * dev override — `TemplateURL` only resolves against an S3 object location (plan §3's
 * Non-goals), so a plain HTTPS URL that isn't shaped like one of these is rejected,
 * unlike `onboarding-scripts.ts`'s script override which accepts arbitrary HTTPS.
 */
function getBaseUrl(): string | null {
  const raw = process.env.NEXT_PUBLIC_LAUNCHPAD_CFN_TEMPLATE_BASE_URL;
  if (!raw) return null;
  const trimmed = raw.trim().replace(/\/+$/, "");
  if (!trimmed) return null;

  try {
    const u = new URL(trimmed);
    if (u.protocol === "http:" && (u.hostname === "localhost" || u.hostname === "127.0.0.1")) {
      return trimmed;
    }
    if (u.protocol !== "https:") throw new Error("not https");
    const virtualHosted = /^[^.]+\.s3([.-][a-z0-9-]+)?\.amazonaws\.com$/i.test(u.hostname);
    const pathStyle = /^s3([.-][a-z0-9-]+)?\.amazonaws\.com$/i.test(u.hostname) && u.pathname.length > 1;
    if (virtualHosted || pathStyle) return trimmed;
    throw new Error("not an S3 URL shape");
  } catch {
    /* fall through to rejection */
  }

  if (!warnedAboutBaseUrl) {
    warnedAboutBaseUrl = true;
    console.warn(
      "[onboarding-cloudformation] NEXT_PUBLIC_LAUNCHPAD_CFN_TEMPLATE_BASE_URL rejected " +
        "— must be a virtual-hosted or path-style S3 URL (or http://localhost / " +
        "127.0.0.1 for dev). CloudFormation quick-create links will be unavailable.",
    );
  }
  return null;
}

/**
 * Returns a human-readable reason the CFN onboarding UI can't render a template URL
 * right now, or `null` if fully configured. Safe to call from any environment.
 */
export function getCfnOnboardingMisconfiguration(): string | null {
  const baseUrl = getBaseUrl();
  const ref = process.env.NEXT_PUBLIC_LAUNCHPAD_CFN_TEMPLATE_REF?.trim();
  if (!baseUrl && !ref) {
    return "CloudFormation template hosting isn't configured on this deployment yet.";
  }
  if (!baseUrl) {
    return (
      "NEXT_PUBLIC_LAUNCHPAD_CFN_TEMPLATE_BASE_URL must be set to the platform " +
      "template bucket's S3 URL. Contact your platform admin."
    );
  }
  if (!ref) {
    return (
      "NEXT_PUBLIC_LAUNCHPAD_CFN_TEMPLATE_REF must be set to the published template " +
      "ref. Contact your platform admin."
    );
  }
  return null;
}

/**
 * The hosted template URL for `computeType`, or `null` if hosting isn't configured.
 * Never throws — callers check `getCfnOnboardingMisconfiguration()` first if they want
 * to explain *why* this is null.
 */
export function resolveCfnTemplateUrl(computeType: ComputeType): string | null {
  const baseUrl = getBaseUrl();
  const ref = process.env.NEXT_PUBLIC_LAUNCHPAD_CFN_TEMPLATE_REF?.trim();
  if (!baseUrl || !ref) return null;
  return `${baseUrl}/${ref}/${TEMPLATE_FILENAME[computeType]}`;
}

/**
 * Mirrors `api/services/cfn_onboarding.py`'s `widest_compute_type`: any `eks` member
 * means the shared role must keep carrying the `eks` template (statements +
 * MaxSessionDuration 7200), never `ecs_fargate` alone. Only two compute types exist
 * today, so "widest" is simply "is eks present". Always run every compute-type decision
 * in this module through this function — including the single-infra case, where it
 * trivially returns that infra's own type — rather than reading `compute_type` off one
 * infra directly, so a future sibling never silently narrows what gets recommended.
 */
export function widestComputeType(computeTypes: Iterable<ComputeType>): ComputeType {
  for (const c of computeTypes) {
    if (c === "eks") return "eks";
  }
  return "ecs_fargate";
}

export interface QuickCreateParams {
  computeType: ComputeType;
  externalIds: string[];
  platformAccountId: string;
  platformUser: string;
  stackName?: string;
}

export interface QuickCreateLink {
  url: string;
  templateUrl: string;
  stackName: string;
}

/**
 * Builds the CloudFormation console "quick-create" deep link, or `null` when template
 * hosting isn't configured, the parameter list has grown past the quick-create-safe cap,
 * or the resulting URL would be unreasonably long (plan §3) — callers fall back to a
 * copy-paste parameter block in all three cases.
 */
export function buildQuickCreateLink(params: QuickCreateParams): QuickCreateLink | null {
  if (params.externalIds.length === 0) return null;
  if (params.externalIds.length > EXTERNAL_IDS_QUICK_CREATE_CAP) return null;

  const templateUrl = resolveCfnTemplateUrl(params.computeType);
  if (!templateUrl) return null;

  const stackName = params.stackName?.trim() || CFN_ROLE_NAME;
  const query = new URLSearchParams({
    templateURL: templateUrl,
    stackName,
    param_ExternalIds: params.externalIds.join(","),
    param_LaunchpadPlatformAccountId: params.platformAccountId,
    param_LaunchpadPlatformUser: params.platformUser,
  });
  const url =
    `https://${CFN_STACK_REGION}.console.aws.amazon.com/cloudformation/home` +
    `?region=${CFN_STACK_REGION}#/stacks/quickcreate?${query.toString()}`;

  if (url.length > QUICK_CREATE_URL_LENGTH_CAP) return null;
  return { url, templateUrl, stackName };
}

/**
 * The console URL for an existing stack's Stack Info page — used both as the "update
 * existing stack" entry point (second infra onboarding into an already CFN-managed
 * account, plan §5's customer-driven copy/paste flow) and as nuke's last-infra
 * `customer_delete_required` fallback link.
 */
export function buildStackInfoUrl(stackName: string): string {
  const encoded = encodeURIComponent(stackName);
  return (
    `https://${CFN_STACK_REGION}.console.aws.amazon.com/cloudformation/home` +
    `?region=${CFN_STACK_REGION}#/stacks/stackinfo?stackId=${encoded}`
  );
}
