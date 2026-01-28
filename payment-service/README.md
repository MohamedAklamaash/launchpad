# Payment Service

The Payment Service handles billing and Stripe integration for the Launchpad platform. It supports infrastructure-based billing and synchronizes user and infrastructure data via RabbitMQ.

## Features

- **Stripe Integration**: Create checkout sessions and handle webhooks for payment verification.
- **Infrastructure-Based Billing**: Link payments to specific infrastructure instances.
- **Data Sync**: Real-time synchronization of users and infrastructures via RabbitMQ.

## API Endpoints

### 1. Create Checkout Session
Initiates a Stripe checkout session for the authenticated user.

- **URL**: `/api/v1/payments/checkout/`
- **Method**: `POST`
- **Headers**:
    - `Authorization: Bearer <JWT_TOKEN>`
- **Body**:
  ```json
  {
    "amount": 1000,
    "infrastructure_id": "optional-uuid"
  }
  ```
- **Response**:
  ```json
  {
    "checkout_url": "https://checkout.stripe.com/...",
    "session_id": "cs_test_..."
  }
  ```

### 2. Stripe Webhook
Receives and processes events from Stripe.

- **URL**: `/api/v1/payments/webhook/`
- **Method**: `POST`
- **Headers**:
    - `STRIPE_SIGNATURE: <header_from_stripe>`
- **Exclusion**: This endpoint is excluded from JWT authentication but verified via Stripe signature.

### Messaging
The service automatically starts background threads for consuming RabbitMQ events:
- `auth.events`: Syncs user data.
- `infrastructure.events`: Syncs infrastructure data.
