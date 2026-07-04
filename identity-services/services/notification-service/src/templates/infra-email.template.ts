import { renderEmail } from './layout';

type InfraEvent = 'provision_success' | 'provision_failure' | 'destroy_success' | 'destroy_failure';

const EVENT_LABELS: Record<
    InfraEvent,
    { title: string; color: string; label: string; intro: string }
> = {
    provision_success: {
        title: 'Infrastructure provisioned',
        color: '#34d399',
        label: 'Success',
        intro: 'is live. Your AWS environment is ready — you can start shipping applications to it.',
    },
    provision_failure: {
        title: 'Provisioning failed',
        color: '#f87171',
        label: 'Failed',
        intro: "couldn't be provisioned. The details below should help — you can retry from the dashboard.",
    },
    destroy_success: {
        title: 'Infrastructure destroyed',
        color: '#60a5fa',
        label: 'Destroyed',
        intro: 'has been torn down. All associated AWS resources were removed.',
    },
    destroy_failure: {
        title: 'Destroy failed',
        color: '#f87171',
        label: 'Failed',
        intro: "couldn't be fully torn down. Review the details below and retry from the dashboard.",
    },
};

export const getInfraEmailTemplate = (
    event: InfraEvent,
    infraName: string,
    userName: string,
    error?: string,
) => {
    const { title, color, label, intro } = EVENT_LABELS[event];

    const errorHtml = error
        ? `<div style="margin:20px 0 0;padding:16px 18px;background:#1a1214;border:1px solid #4c1d1d;border-radius:10px;font-family:'SF Mono',Menlo,Consolas,monospace;font-size:12px;line-height:1.5;color:#fca5a5;white-space:pre-wrap;word-break:break-word;"><strong style="color:#f87171;">Error</strong><br>${error}</div>`
        : '';

    const contentHtml = `<div style="margin:26px 0 0;padding:20px 22px;background:#0e0e10;border:1px solid #26262b;border-radius:12px;">
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
          <tr>
            <td style="font-size:13px;color:#a1a1aa;">Environment</td>
            <td align="right" style="font-size:14px;font-weight:600;color:#f4f4f5;">${infraName}</td>
          </tr>
          <tr>
            <td style="padding-top:14px;font-size:13px;color:#a1a1aa;">Status</td>
            <td align="right" style="padding-top:14px;">
              <span style="display:inline-block;padding:4px 13px;border-radius:999px;border:1px solid ${color};color:${color};font-size:12px;font-weight:600;">
                <span style="display:inline-block;width:7px;height:7px;border-radius:999px;background:${color};vertical-align:middle;margin-right:6px;"></span>${label}
              </span>
            </td>
          </tr>
        </table>
      </div>${errorHtml}`;

    return renderEmail({
        preheader: title,
        heading: title,
        headingColor: color,
        intro: `Hi ${userName}, your environment <strong style="color:#f4f4f5;">${infraName}</strong> ${intro}`,
        contentHtml,
    });
};
