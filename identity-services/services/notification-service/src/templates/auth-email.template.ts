export const getAuthEmailTemplate = (url: string, userName: string) => {
    return `
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Authenticate to Launchpad</title>
    <style>
        body {
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background-color: #f4f4f5;
            margin: 0;
            padding: 0;
            line-height: 1.6;
            color: #18181b;
        }
        .container {
            max-width: 600px;
            margin: 40px auto;
            background-color: #ffffff;
            border-radius: 12px;
            overflow: hidden;
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06);
        }
        .header {
            background: linear-gradient(135deg, #4f46e5 0%, #7c3aed 100%);
            padding: 32px 40px;
            text-align: center;
        }
        .logo {
            color: white;
            font-size: 24px;
            font-weight: 800;
            letter-spacing: -0.5px;
            margin: 0;
            text-decoration: none;
        }
        .content {
            padding: 40px;
        }
        h1 {
            font-size: 24px;
            font-weight: 700;
            color: #111827;
            margin-top: 0;
            margin-bottom: 16px;
        }
        p {
            color: #4b5563;
            margin-bottom: 24px;
            font-size: 16px;
        }
        .button-container {
            text-align: center;
            margin: 32px 0;
        }
        .button {
            display: inline-block;
            background-color: #4f46e5;
            color: #ffffff;
            font-weight: 600;
            font-size: 16px;
            padding: 14px 32px;
            text-decoration: none;
            border-radius: 6px;
            transition: background-color 0.2s;
        }
        .button:hover {
            background-color: #4338ca;
        }
        .footer {
            background-color: #f9fafb;
            padding: 24px 40px;
            text-align: center;
            border-top: 1px solid #e5e7eb;
        }
        .footer p {
            font-size: 13px;
            color: #6b7280;
            margin: 0;
        }
        .link-fallback {
            font-size: 13px;
            color: #6b7280;
            word-break: break-all;
            margin-top: 24px;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div class="logo">Launchpad</div>
        </div>
        <div class="content">
            <h1>Welcome, ${userName}!</h1>
            <p>You have been invited to join Launchpad. To securely sign in and access your account, simply click the button below:</p>
            
            <div class="button-container">
                <a href="${url}" class="button" target="_blank">Authenticate & Sign In</a>
            </div>

            <p>This link works like a magic sign-in key. You don't need to remember another password right now.</p>
            
            <div class="link-fallback">
                If the button doesn't work, copy and paste this link into your browser:<br>
                <a href="${url}" style="color: #4f46e5;">${url}</a>
            </div>
        </div>
        <div class="footer">
            <p>&copy; ${new Date().getFullYear()} Launchpad. All rights reserved.</p>
            <p>If you didn't request this email, you can safely ignore it.</p>
        </div>
    </div>
</body>
</html>
    `;
};
