import { renderEmail } from './layout';

type InfraEvent = 'provision_success' | 'provision_failure' | 'destroy_success' | 'destroy_failure';

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
};

const CLEAN_SUBJECTS: Record<InfraEvent, (name: string) => string> = {
    provision_success: (n) => `Your environment "${n}" is live`,
    provision_failure: (n) => `Action needed: "${n}" couldn't be provisioned`,
    destroy_success: (n) => `"${n}" has been torn down`,
    destroy_failure: (n) => `Action needed: "${n}" couldn't be torn down`,
};

export const getInfraEmailSubject = (event: InfraEvent, infraName?: string): string => {
    const build = CLEAN_SUBJECTS[event];
    if (!build) return 'Launchpad environment update';
    return build(infraName || 'your environment');
};

// Raw terraform / boto3 error text leaks AWS account ids, ARNs, IAM role names, state-backend
// paths and stack traces — none of which belongs in a customer's inbox. Map the raw string to a
// safe, actionable summary and NEVER echo the original. The full log stays behind the dashboard.
const ERROR_SUMMARIES: { test: RegExp; message: string }[] = [
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

export const getInfraEmailTemplate = (
    event: InfraEvent,
    infraName: string,
    userName: string,
    error?: string,
    dashboardUrl?: string,
) => {
    const { eyebrow, title, color, label, intro } = EVENT_LABELS[event];
    const name = infraName || 'your environment';
    const isFailure = event === 'provision_failure' || event === 'destroy_failure';

    const detailsCard = `<div style="margin:26px 0 0;padding:20px 22px;background:#0e0e10;border:1px solid #26262b;border-radius:12px;">
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
          <tr>
            <td style="font-family:'SF Mono',SFMono-Regular,Menlo,Consolas,monospace;font-size:11px;letter-spacing:1px;color:#8a8a94;text-transform:uppercase;">Environment</td>
            <td align="right" style="font-size:14px;font-weight:600;color:#f4f4f5;">${name}</td>
          </tr>
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

    return renderEmail({
        preheader: title,
        eyebrow,
        heading: title,
        headingColor: color,
        intro: `Hi ${userName || 'there'}, your environment <strong style="color:#f4f4f5;">${name}</strong> ${intro}`,
        contentHtml: `${detailsCard}${guidanceCard}`,
        cta,
    });
};
