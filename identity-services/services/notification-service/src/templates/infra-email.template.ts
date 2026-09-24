import { renderEmail } from './layout';

export type InfraEvent =
    | 'provision_success'
    | 'provision_failure'
    | 'destroy_success'
    | 'destroy_failure'
    | 'database_create_success'
    | 'database_create_failure'
    | 'database_delete_success'
    | 'database_delete_failure';

export const ALL_INFRA_EVENTS: InfraEvent[] = [
    'provision_success',
    'provision_failure',
    'destroy_success',
    'destroy_failure',
    'database_create_success',
    'database_create_failure',
    'database_delete_success',
    'database_delete_failure',
];

// infra/database names and the user's display name are all customer-supplied and land
// directly in this HTML email — escape before interpolating, or a crafted name becomes
// stored HTML injection into the recipient's inbox.
const escapeHtml = (value: string): string =>
    value
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');

const EVENT_LABELS: Record<
    InfraEvent,
    { eyebrow: string; title: string; color: string; label: string; intro: string }
> = {
    provision_success: {
        eyebrow: 'Environment · Provisioning',
        title: 'Your environment is live',
        color: '#34d399',
        label: 'Active',
        intro: 'is live and ready — you can start shipping applications to it.',
    },
    provision_failure: {
        eyebrow: 'Environment · Provisioning',
        title: 'Provisioning needs attention',
        color: '#f87171',
        label: 'Failed',
        intro: "couldn't finish provisioning. Here's what to do next — you can retry from the dashboard.",
    },
    destroy_success: {
        eyebrow: 'Environment · Teardown',
        title: 'Environment torn down',
        color: '#60a5fa',
        label: 'Destroyed',
        intro: 'has been torn down. All associated AWS resources were removed from your account.',
    },
    destroy_failure: {
        eyebrow: 'Environment · Teardown',
        title: 'Teardown needs attention',
        color: '#f87171',
        label: 'Failed',
        intro: "couldn't be fully torn down. Here's what to do next — you can retry from the dashboard.",
    },
    database_create_success: {
        eyebrow: 'Database · Provisioning',
        title: 'Your database is live',
        color: '#34d399',
        label: 'Active',
        intro: 'database is live — attach it to an app and redeploy to connect.',
    },
    database_create_failure: {
        eyebrow: 'Database · Provisioning',
        title: 'Database creation needs attention',
        color: '#f87171',
        label: 'Failed',
        intro: "database couldn't finish provisioning. Here's what to do next — you can retry from the dashboard.",
    },
    database_delete_success: {
        eyebrow: 'Database · Teardown',
        title: 'Database deleted',
        color: '#60a5fa',
        label: 'Deleted',
        intro: 'database has been deleted. A final snapshot was taken before the underlying resource was destroyed.',
    },
    database_delete_failure: {
        eyebrow: 'Database · Teardown',
        title: 'Database deletion needs attention',
        color: '#f87171',
        label: 'Failed',
        intro: "database couldn't be fully deleted. Here's what to do next — you can retry from the dashboard.",
    },
};

const CLEAN_SUBJECTS: Record<InfraEvent, (name: string) => string> = {
    provision_success: (n) => `Your environment "${n}" is live`,
    provision_failure: (n) => `Action needed: "${n}" couldn't be provisioned`,
    destroy_success: (n) => `"${n}" has been torn down`,
    destroy_failure: (n) => `Action needed: "${n}" couldn't be torn down`,
    database_create_success: (n) => `Your database "${n}" is live`,
    database_create_failure: (n) => `Action needed: database "${n}" couldn't be provisioned`,
    database_delete_success: (n) => `Database "${n}" has been deleted`,
    database_delete_failure: (n) => `Action needed: database "${n}" couldn't be deleted`,
};

const DATABASE_EVENTS = new Set<InfraEvent>([
    'database_create_success',
    'database_create_failure',
    'database_delete_success',
    'database_delete_failure',
]);

export const getInfraEmailSubject = (
    event: InfraEvent,
    infraName?: string,
    databaseName?: string,
): string => {
    const build = CLEAN_SUBJECTS[event];
    if (!build) return 'Launchpad environment update';
    const isDatabaseEvent = DATABASE_EVENTS.has(event);
    return build(
        (isDatabaseEvent ? databaseName : infraName) ||
            (isDatabaseEvent ? 'your database' : 'your environment'),
    );
};

// Raw terraform / boto3 error text leaks AWS account ids, ARNs, IAM role names, state-backend
// paths and stack traces — none of which belongs in a customer's inbox. Map the raw string to a
// safe, actionable summary and NEVER echo the original. The full log stays behind the dashboard.
const ERROR_SUMMARIES: { test: RegExp; message: string }[] = [
    {
        test: /access\s?entr(y|ies)|\beks\b[\s\S]*(access\s?denied|not\s?authorized|unauthorized|forbidden)|(access\s?denied|not\s?authorized|unauthorized|forbidden)[\s\S]*\beks\b/i,
        message:
            'Launchpad could not access the Kubernetes cluster in your AWS account. Re-run the onboarding script to refresh the deployment role, then retry.',
    },
    {
        test: /kubernetes\s?api|k8s\s?api|unable\s?to\s?connect\s?to\s?the\s?server|\.eks\.amazonaws\.com|api\s?server[\s\S]*(unreachable|refused|timed?\s?out)/i,
        message:
            "Launchpad could not reach your Kubernetes cluster's API. This is usually temporary — retry from the dashboard.",
    },
    {
        test: /\beks\b[\s\S]*cluster[\s\S]*(timed?\s?out|timeout|did\s?not\s?become)|\beks\b[\s\S]*cluster\s?creat(e|ion)?[\s\S]*(timed?\s?out|timeout)/i,
        message:
            'The Kubernetes cluster did not finish creating in time. Cluster creation can take up to 20 minutes — retry from the dashboard.',
    },
    {
        test: /\baddons?\b|add-on|vpc-cni|aws-node|coredns|kube-proxy/i,
        message:
            'A Kubernetes cluster component failed to install. Retry from the dashboard and Launchpad will reconcile the cluster add-ons.',
    },
    {
        test: /nodepool|node\s?pool|nodeclass|karpenter|failedscheduling|unschedulable|\bnodes?\b[\s\S]*(capacity|insufficient)|insufficient[\s\S]*\bnodes?\b/i,
        message:
            'AWS could not provide compute capacity for the cluster nodes. Retry, optionally in a different region.',
    },
    {
        test: /dbinstancealreadyexists|dbclusteralreadyexistsfault|replicationgroupalreadyexists/i,
        message:
            'A database with this name already exists in your account from a previous run. Delete it manually in AWS, or retry with a different name.',
    },
    {
        test: /storagetypenotsupported|storagequotaexceeded|provisionedthroughputexceeded/i,
        message:
            'The requested storage size isn’t supported for this database configuration. Adjust the storage size and retry.',
    },
    {
        test: /dbsubnetgroupnotfoundfault|cachesubnetgroupnotfoundfault|dbclusterparametergroupnotfound|invalidvpcnetworkstatefault/i,
        message:
            'Launchpad could not find the private subnets for this database. This is usually transient — retry from the dashboard.',
    },
    {
        test: /insufficientcacheclustercapacity|insufficientdbinstancecapacity|insufficientstorageclustercapacity/i,
        message:
            'AWS did not have capacity for this database’s instance size in your account’s region. Retry with a smaller instance class, or a different region.',
    },
    {
        test: /secretsmanager.*(resourceexistsexception|invalidrequestexception)|scheduled\s?for\s?deletion/i,
        message:
            'A credential secret from a previous run is still pending deletion in Secrets Manager. This resolves itself within a day — retry then.',
    },
    {
        test: /access\s?denied|not\s?authorized|unauthorized|assume\s?role|forbidden|\biam\b|permission/i,
        message:
            'Launchpad could not access your AWS account. Re-run the onboarding script to refresh the deployment role, then retry.',
    },
    {
        test: /expired|invalidclienttokenid|signature|credential|\btoken\b/i,
        message:
            'Your AWS credentials have expired. Re-run the onboarding script to refresh them, then retry.',
    },
    {
        test: /\blimit\b|quota|throttl|toomanyrequests/i,
        message:
            'An AWS service limit was reached in your account. Request a limit increase for the affected service, then retry.',
    },
    {
        test: /timed?\s?out|timeout|deadline/i,
        message:
            'The operation timed out before AWS finished. This is usually temporary — retry from the dashboard.',
    },
    {
        test: /already\s?exists|duplicate|conflict|in\s?use/i,
        message:
            'A resource from a previous run is still present in your account. Retry and Launchpad will reconcile it.',
    },
    {
        test: /cidr|\bvpc\b|subnet|network|route\s?table/i,
        message:
            'A networking conflict stopped the run, often an overlapping VPC or CIDR range. Adjust the configuration and retry.',
    },
    {
        test: /insufficient|capacity|unavailable/i,
        message:
            'AWS did not have capacity for the requested resources. Retry, optionally in a different region.',
    },
];

export const summarizeInfraError = (raw?: string): string | undefined => {
    if (!raw || !raw.trim()) return undefined;
    const match = ERROR_SUMMARIES.find(({ test }) => test.test(raw));
    return match
        ? match.message
        : 'The run did not complete. Open the dashboard for the full log, then retry.';
};

const FAILURE_EVENTS = new Set<InfraEvent>([
    'provision_failure',
    'destroy_failure',
    'database_create_failure',
    'database_delete_failure',
]);

export const getInfraEmailTemplate = (
    event: InfraEvent,
    infraName: string,
    userName: string,
    error?: string,
    dashboardUrl?: string,
    databaseName?: string,
) => {
    const { eyebrow, title, color, label, intro } = EVENT_LABELS[event];
    const name = escapeHtml(infraName || 'your environment');
    const safeUserName = escapeHtml(userName || 'there');
    const isDatabaseEvent = DATABASE_EVENTS.has(event);
    const isFailure = FAILURE_EVENTS.has(event);

    const dbNameRow = isDatabaseEvent
        ? `<tr>
            <td style="padding-top:14px;font-family:'SF Mono',SFMono-Regular,Menlo,Consolas,monospace;font-size:11px;letter-spacing:1px;color:#8a8a94;text-transform:uppercase;">Database</td>
            <td align="right" style="padding-top:14px;font-size:14px;font-weight:600;color:#f4f4f5;">${escapeHtml(databaseName || 'unnamed')}</td>
          </tr>`
        : '';

    const detailsCard = `<div style="margin:26px 0 0;padding:20px 22px;background:#0e0e10;border:1px solid #26262b;border-radius:12px;">
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
          <tr>
            <td style="font-family:'SF Mono',SFMono-Regular,Menlo,Consolas,monospace;font-size:11px;letter-spacing:1px;color:#8a8a94;text-transform:uppercase;">Environment</td>
            <td align="right" style="font-size:14px;font-weight:600;color:#f4f4f5;">${name}</td>
          </tr>
          ${dbNameRow}
          <tr>
            <td style="padding-top:14px;font-family:'SF Mono',SFMono-Regular,Menlo,Consolas,monospace;font-size:11px;letter-spacing:1px;color:#8a8a94;text-transform:uppercase;">Status</td>
            <td align="right" style="padding-top:14px;">
              <span style="display:inline-block;padding:4px 13px;border-radius:999px;border:1px solid ${color};color:${color};font-size:12px;font-weight:600;">
                <span style="color:${color};font-size:9px;vertical-align:middle;">&#9679;</span>&nbsp;${label}
              </span>
            </td>
          </tr>
        </table>
      </div>`;

    const guidance = summarizeInfraError(error);
    const guidanceCard =
        isFailure && guidance
            ? `<div style="margin:16px 0 0;padding:18px 20px;background:#0e0e10;border:1px solid #26262b;border-left:3px solid ${color};border-radius:10px;">
             <div style="font-family:'SF Mono',SFMono-Regular,Menlo,Consolas,monospace;font-size:11px;letter-spacing:1px;color:#8a8a94;text-transform:uppercase;margin:0 0 8px;">What to do next</div>
             <p style="margin:0;font-size:14px;line-height:1.6;color:#d4d4d8;">${guidance}</p>
           </div>`
            : '';

    const cta = dashboardUrl
        ? { label: isFailure ? 'Open dashboard' : 'Go to dashboard', url: dashboardUrl }
        : undefined;

    const introText = isDatabaseEvent
        ? `Hi ${safeUserName}, your database <strong style="color:#f4f4f5;">${escapeHtml(databaseName || 'unnamed')}</strong> in environment <strong style="color:#f4f4f5;">${name}</strong> ${intro}`
        : `Hi ${safeUserName}, your environment <strong style="color:#f4f4f5;">${name}</strong> ${intro}`;

    return renderEmail({
        preheader: title,
        eyebrow,
        heading: title,
        headingColor: color,
        intro: introText,
        contentHtml: `${detailsCard}${guidanceCard}`,
        cta,
    });
};
