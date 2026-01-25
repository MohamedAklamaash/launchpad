# Gateway Service

A production-grade API Gateway built with FastAPI to route requests to various microservices.

## Service Endpoints

### Gateway Health
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/` | Root status message |
| GET | `/health` | Health check endpoint |

### Authentication Service (Auth Service)
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/auth/register` | Register an invited user |
| POST | `/auth/login` | Authenticate a user |
| GET | `/auth/authenticate-with-otp` | Authenticate via magic link (OTP) |
| POST | `/auth/forgot-password` | Request a password reset |
| POST | `/auth/verify-reset-otp` | Verify the OTP sent for password reset |
| POST | `/auth/reset-password` | Set a new password using reset token |
| GET | `/user/login` | Redirect to GitHub OAuth login (Proxied to auth-service) |
| GET | `/auth/user/login` | Alternative path for GitHub login |
| GET | `/user/callback` | Handle GitHub OAuth callback |

### User Service
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/users/{userId}` | Retrieve details of a specific user |
| GET | `/users` | Search for users by name or email (use `?q=query`) |

### Notification Service
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/notifications/user/{userId}` | Retrieve notification history for a user |

## Environment Configuration

The gateway uses a `.env` file to locate individual services. Ensure these URLs are correct for your environment:

```env
AUTH_SERVICE_URL=http://127.0.0.1:5001/api
USER_SERVICE_URL=http://127.0.0.1:5002/api
NOTIFICATION_SERVICE_URL=http://127.0.0.1:5003/api
```

## Running the Service

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
2. Start the gateway:
   ```bash
   python3 app.py
   ```
