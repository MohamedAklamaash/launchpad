type Cta = { label: string; url: string };

interface EmailOptions {
    preheader: string;
    eyebrow: string;
    heading: string;
    headingColor?: string;
    intro: string;
    contentHtml?: string;
    cta?: Cta;
}

// Bulletproof brand shell for every transactional email. Deep-space canvas, azure launch accent,
// one dark card, mono "Mission Control" chrome to match the dashboard. Table-based with inline
// styles and no SVG/webfonts/JS so it renders the same in Gmail, Apple Mail and Outlook.
export const renderEmail = (opts: EmailOptions): string => {
    const {
        preheader,
        eyebrow,
        heading,
        headingColor = '#f4f4f5',
        intro,
        contentHtml = '',
        cta,
    } = opts;
    const year = new Date().getFullYear();

    // Padding on the <td>, not the <a>: Outlook's Word engine drops top/bottom padding on an
    // inline anchor, which would collapse the button to text height. bgcolor guards the washout.
    const ctaHtml = cta
        ? `<table role="presentation" cellpadding="0" cellspacing="0" style="margin:30px 0 0;">
             <tr><td bgcolor="#2e8fe6" style="border-radius:10px;background:#2e8fe6;padding:13px 32px;text-align:center;">
               <a href="${cta.url}" target="_blank" style="font-size:15px;font-weight:600;color:#ffffff;text-decoration:none;">${cta.label}</a>
             </td></tr>
           </table>`
        : '';

    return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta name="color-scheme" content="dark">
  <title>${heading}</title>
</head>
<body bgcolor="#09090b" style="margin:0;padding:0;background:#09090b;">
  <span style="display:none;max-height:0;overflow:hidden;opacity:0;color:#09090b;">${preheader}</span>
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" bgcolor="#09090b" style="background:#09090b;">
    <tr><td align="center" style="padding:32px 16px;">
      <table role="presentation" width="600" cellpadding="0" cellspacing="0" bgcolor="#131316" style="max-width:600px;width:100%;background:#131316;border:1px solid #26262b;border-radius:18px;overflow:hidden;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
        <tr><td style="height:3px;background:#2e8fe6;background:linear-gradient(90deg,#1f6fc4,#2e8fe6,#7dd3fc);font-size:0;line-height:0;">&nbsp;</td></tr>
        <tr><td style="padding:22px 40px;border-bottom:1px solid #26262b;background:radial-gradient(120% 160% at 0% 0%, rgba(46,143,230,0.12), transparent 60%);">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
            <td style="vertical-align:middle;">
              <table role="presentation" cellpadding="0" cellspacing="0"><tr>
                <td bgcolor="#2e8fe6" style="width:38px;height:38px;border-radius:10px;background:#2e8fe6;background:linear-gradient(140deg,#2e8fe6,#0b5aa0);text-align:center;vertical-align:middle;font-size:18px;line-height:38px;color:#ffffff;">&#9650;</td>
                <td style="padding-left:12px;vertical-align:middle;color:#f4f4f5;font-size:18px;font-weight:700;letter-spacing:-0.3px;">Launchpad</td>
              </tr></table>
            </td>
            <td align="right" style="vertical-align:middle;font-family:'SF Mono',SFMono-Regular,Menlo,Consolas,monospace;font-size:10px;font-weight:600;letter-spacing:1.5px;color:#71717a;text-transform:uppercase;">
              <span style="color:#34d399;">&#9679;</span>&nbsp;Mission&nbsp;Control
            </td>
          </tr></table>
        </td></tr>
        <tr><td style="padding:36px 40px 40px;">
          <div style="font-family:'SF Mono',SFMono-Regular,Menlo,Consolas,monospace;font-size:11px;font-weight:600;letter-spacing:2px;color:#2e8fe6;text-transform:uppercase;margin:0 0 14px;">${eyebrow}</div>
          <h1 style="margin:0 0 12px;font-size:23px;font-weight:700;color:${headingColor};letter-spacing:-0.5px;line-height:1.25;">${heading}</h1>
          <p style="margin:0;font-size:15px;line-height:1.65;color:#a1a1aa;">${intro}</p>
          ${ctaHtml}
          ${contentHtml}
        </td></tr>
        <tr><td bgcolor="#0e0e10" style="padding:22px 40px;border-top:1px solid #26262b;background:#0e0e10;">
          <p style="margin:0;font-size:12px;line-height:1.6;color:#8a8a94;">This is a transactional message for your Launchpad account.<br>&copy; ${year} Launchpad &mdash; deploy to your own AWS account in minutes.</p>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>`;
};
