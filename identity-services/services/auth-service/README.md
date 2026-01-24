# Auth Service

The Identity and Authentication Service for Launchpad.

## API Documentation

### Invited User Authentication

#### Register Invited User
Register a user who has been invited to an infrastructure added by admin.
- **URL**: \`/auth/register\`
- **Method**: \`POST\`
- **Body**:
  \`\`\`json
  {
    "email": "user@example.com",
    "password": "securepassword123",
    "user_name": "John Doe",
    "infra_id": "infra-123",
    "role": "admin"
  }
  \`\`\`

#### Login
Authenticate a registered user.
- **URL**: \`/auth/login\`
- **Method**: \`POST\`
- **Body**:
  \`\`\`json
  {
    "email": "user@example.com",
    "password": "securepassword123",
    "infra_id": "infra-123"
  }
  \`\`\`

#### Authenticate with OTP (Link Click)
Endpoint handled when user clicks the magic link in email.
- **URL**: \`/auth/authenticate-with-otp\`
- **Method**: \`GET\`
- **Query Params**:
  - \`email\`: User's email
  - \`otp\`: The OTP code
- **Response**: Returns Auth Tokens

### Password Management

#### Forgot Password
Request a password reset. Sends an OTP (or logic to send one).
- **URL**: \`/auth/forgot-password\`
- **Method**: \`POST\`
- **Body**:
  \`\`\`json
  {
    "email": "user@example.com",
    "infra_id": "infra-123" // Optional
  }
  \`\`\`

#### Verify Reset OTP
Verify the OTP sent for password reset.
- **URL**: \`/auth/verify-reset-otp\`
- **Method**: \`POST\`
- **Body**:
  \`\`\`json
  {
    "email": "user@example.com",
    "otp": "123456"
  }
  \`\`\`
- **Response**:
  \`\`\`json
  {
    "success": true,
    "token": "jwt_reset_token..."
  }
  \`\`\`

#### Reset Password
Set a new password using the verified reset token.
- **URL**: \`/auth/reset-password\`
- **Method**: \`POST\`
- **Body**:
  \`\`\`json
  {
    "token": "jwt_reset_token...",
    "newPassword": "newsecurepassword123"
  }
  \`\`\`

### GitHub Authentication

#### Login with GitHub
Redirects to GitHub OAuth.
- **URL**: \`/user/login\`
- **Method**: \`GET\`

#### GitHub Callback
Handle the callback code from GitHub.
- **URL**: \`/user/callback\`
- **Method**: \`GET\`
- **Query Params**:
  - \`code\`: GitHub authorization code