type InfraEvent = 'provision_success' | 'provision_failure' | 'destroy_success' | 'destroy_failure';

const EVENT_LABELS: Record<InfraEvent, { title: string; color: string; emoji: string }> = {
    provision_success: { title: 'Infrastructure Provisioned', color: '#16a34a', emoji: '✅' },
    provision_failure: { title: 'Infrastructure Provision Failed', color: '#dc2626', emoji: '❌' },
    destroy_success: { title: 'Infrastructure Destroyed', color: '#2563eb', emoji: '🗑️' },
    destroy_failure: { title: 'Infrastructure Destroy Failed', color: '#dc2626', emoji: '❌' },
};

export const getInfraEmailTemplate = (
    event: InfraEvent,
    infraName: string,
    infraId: string,
    userName: string,
    error?: string,
) => {
    const { title, color, emoji } = EVENT_LABELS[event];
    return `
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>${title}</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background:#f4f4f5; margin:0; padding:0; color:#18181b; }
    .container { max-width:600px; margin:40px auto; background:#fff; border-radius:12px; overflow:hidden; box-shadow:0 4px 6px -1px rgba(0,0,0,.1); }
    .header { background:linear-gradient(135deg,#4f46e5 0%,#7c3aed 100%); padding:32px 40px; text-align:center; }
    .logo { color:#fff; font-size:24px; font-weight:800; letter-spacing:-.5px; margin:0; }
    .content { padding:40px; }
    h1 { font-size:22px; font-weight:700; color:${color}; margin-top:0; }
    .meta { background:#f9fafb; border:1px solid #e5e7eb; border-radius:8px; padding:16px 20px; margin:20px 0; font-size:14px; color:#374151; }
    .meta span { font-weight:600; color:#111827; }
    .error-box { background:#fef2f2; border:1px solid #fecaca; border-radius:8px; padding:16px 20px; margin:20px 0; font-size:13px; color:#991b1b; font-family:monospace; white-space:pre-wrap; word-break:break-word; }
    .footer { background:#f9fafb; padding:24px 40px; text-align:center; border-top:1px solid #e5e7eb; font-size:13px; color:#6b7280; }
  </style>
</head>
<body>
  <div class="container">
    <div class="header"><div class="logo">Launchpad</div></div>
    <div class="content">
      <h1>${emoji} ${title}</h1>
      <p>Hi <strong>${userName}</strong>, here's an infrastructure update on your Launchpad account.</p>
      <div class="meta">
        <div>Infrastructure: <span>${infraName}</span></div>
        <div style="margin-top:6px">ID: <span style="font-family:monospace">${infraId}</span></div>
      </div>
      ${error ? `<div class="error-box"><strong>Error:</strong>\n${error}</div>` : ''}
    </div>
    <div class="footer">&copy; ${new Date().getFullYear()} Launchpad. All rights reserved.</div>
  </div>
</body>
</html>`;
};
