export const getForgotPasswordTemplate = (otp: string, userName: string) => {
    return `
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Reset Your Password</title>
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
            background: linear-gradient(135deg, #ef4444 0%, #b91c1c 100%);
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
        .otp-container {
            text-align: center;
            margin: 32px 0;
        }
        .otp {
            display: inline-block;
            background-color: #f3f4f6;
            color: #111827;
            font-weight: 700;
            font-size: 32px;
            padding: 16px 40px;
            border-radius: 8px;
            letter-spacing: 4px;
            border: 1px solid #e5e7eb;
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
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div class="logo">Launchpad</div>
        </div>
        <div class="content">
            <h1>Reset Your Password</h1>
            <p>Hi ${userName},</p>
            <p>We received a request to reset your password. Use the code below to complete the process:</p>
            
            <div class="otp-container">
                <div class="otp">${otp}</div>
            </div>

            <p>This code will expire in 10 minutes. If you didn't request a password reset, you can safely ignore this email.</p>
        </div>
        <div class="footer">
            <p>&copy; ${new Date().getFullYear()} Launchpad. All rights reserved.</p>
        </div>
    </div>
</body>
</html>
    `;
};
