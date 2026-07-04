type Cta = { label: string; url: string };

interface EmailOptions {
    preheader: string;
    heading: string;
    headingColor?: string;
    intro: string;
    contentHtml?: string;
    cta?: Cta;
}

const logoMark = `<svg width="26" height="26" viewBox="0 0 28 28" fill="none" xmlns="http://www.w3.org/2000/svg">
  <path d="M14 2L24.39 8V20L14 26L3.61 20V8L14 2Z" stroke="#ffffff" stroke-width="1.5" stroke-linejoin="round" fill="none" opacity="0.55"/>
  <path d="M14 7C14 7 10 11.5 10 15.5C10 17.5 11.5 19 14 19C16.5 19 18 17.5 18 15.5C18 11.5 14 7 14 7Z" fill="#ffffff"/>
  <path d="M10 15.5L8 18L10 17.5" stroke="#ffffff" stroke-width="1.2" stroke-linecap="round"/>
  <path d="M18 15.5L20 18L18 17.5" stroke="#ffffff" stroke-width="1.2" stroke-linecap="round"/>
  <path d="M12.5 19C12.5 19 13 21.5 14 22C15 21.5 15.5 19 15.5 19" stroke="#ffffff" stroke-width="1.2" stroke-linecap="round" fill="none" opacity="0.85"/>
  <circle cx="14" cy="14" r="1.5" fill="#0b3a63"/>
</svg>`;

// Shared brand shell for every transactional email: true-black canvas, azure accent, one
// dark card. Table-based with inline styles so it renders in Gmail/Apple Mail/Outlook alike.
export const renderEmail = (opts: EmailOptions): string => {
    const { preheader, heading, headingColor = '#f4f4f5', intro, contentHtml = '', cta } = opts;
    const year = new Date().getFullYear();

    const ctaHtml = cta
        ? `<table role="presentation" cellpadding="0" cellspacing="0" style="margin:30px 0 0;">
             <tr><td style="border-radius:10px;background:#2e8fe6;box-shadow:0 6px 20px rgba(46,143,230,0.35);">
               <a href="${cta.url}" target="_blank" style="display:inline-block;padding:14px 34px;font-size:15px;font-weight:600;color:#ffffff;text-decoration:none;border-radius:10px;">${cta.label}</a>
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
<body style="margin:0;padding:0;background:#09090b;">
  <span style="display:none;max-height:0;overflow:hidden;opacity:0;color:#09090b;">${preheader}</span>
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#09090b;padding:32px 16px;">
    <tr><td align="center">
      <table role="presentation" width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;background:#131316;border:1px solid #26262b;border-radius:18px;overflow:hidden;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
        <tr><td style="height:3px;background:linear-gradient(90deg,#1f6fc4,#2e8fe6,#7dd3fc);font-size:0;line-height:0;">&nbsp;</td></tr>
        <tr><td style="padding:26px 40px;border-bottom:1px solid #26262b;background:radial-gradient(120% 140% at 0% 0%, rgba(46,143,230,0.10), transparent 60%);">
          <table role="presentation" cellpadding="0" cellspacing="0"><tr>
            <td style="vertical-align:middle;">
              <table role="presentation" cellpadding="0" cellspacing="0"><tr><td style="width:40px;height:40px;border-radius:11px;background:linear-gradient(140deg,#2e8fe6,#0b5aa0);text-align:center;vertical-align:middle;">${logoMark}</td></tr></table>
            </td>
            <td style="padding-left:13px;vertical-align:middle;color:#f4f4f5;font-size:18px;font-weight:700;letter-spacing:-0.3px;">Launchpad</td>
          </tr></table>
        </td></tr>
        <tr><td style="padding:38px 40px 40px;">
          <h1 style="margin:0 0 12px;font-size:23px;font-weight:700;color:${headingColor};letter-spacing:-0.5px;">${heading}</h1>
          <p style="margin:0;font-size:15px;line-height:1.65;color:#a1a1aa;">${intro}</p>
          ${ctaHtml}
          ${contentHtml}
        </td></tr>
        <tr><td style="padding:22px 40px;border-top:1px solid #26262b;background:#0e0e10;">
          <p style="margin:0;font-size:12px;line-height:1.6;color:#71717a;">You're receiving this because you have a Launchpad account.<br>&copy; ${year} Launchpad — deploy to your own AWS account in minutes.</p>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>`;
};
