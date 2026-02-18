# Application Service

The Application Service is responsible for managing application lifecycle, user resource quotas, and integration with infrastructure units (e.g., AWS, GCP).

## Key Features

- **Automatic Event Consumption**: Spawns background threads on server startup to listen for user registration and infrastructure creation events via RabbitMQ.
- **Resource Quota Validation**: Prevents over-provisioning by validating requested CPU/Memory/Storage against infrastructure capacity and current usage.
- **Explicit API Design**: Uses standard Django REST Framework `APIView`s with explicit routing for maximum transparency.
- **Authorization**: Ensures only authorized users (owners or invited users) can deploy applications to specific infrastructure units.

## Configuration

The service requires the following environment variables in a `.env` file:

```ini
# Database
DATABASE_NAME=application_db
DATABASE_USER_NAME=...
DATABASE_PASSWORD=...
DATABASE_HOST=localhost
DATABASE_PORT=5433

# Messaging
RABBITMQ_URL=amqp://guest:guest@localhost:5672/

# Security
DJANGO_SECRET=...
JWT_SECRET=...
INTERNAL_API_TOKEN=...
DJANGO_PORT=8001
```

## API Documentation

### Base URL: `/api/`

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/applications/` | List all applications for the authenticated user |
| `POST` | `/api/applications/` | Create a new application with quota validation |
| `GET` | `/api/applications/<uuid:pk>/` | Retrieve detailed configuration for an application |
| `DELETE` | `/api/applications/<uuid:pk>/` | Remove an application and free up its resources |
| `GET` | `/health` | Service health status |

### Creating an Application
**POST** `/api/applications/`
**Body**:
```json
{
    "name": "production-api",
    "infrastructure_id": "018e...",
    "alloted_cpu": 1.0,
    "alloted_memory": 1024.0,
    "alloted_storage": 10.0,
    "project_remote_url": "https://github.com/...",
    "project_branch": "main",
    "build_command": "docker build -t app .",
    "start_command": "docker run app",
    "envs": {
        "NODE_ENV": "production"
    }
}
```

## Technical Architecture

- **Middleware**: `UserVerificationMiddleware` bridges JWT claims with the local `api.User` model, ensuring the user exists locally before allowing requests.
- **Messaging**: `InfraEventConsumer` and `AuthEventConsumer` keep local data in sync with the wider microservice ecosystem.
- **Service Layer**: `ApplicationService` handles the heavy lifting of quota aggregation and authorization logic.

## Setup & Execution

1. **Environment**: Copy `.env.example` (if available) to `.env` and fill in the values.
2. **Migrations**: `python manage.py migrate`
3. **Run**: Use the local virtual environment to start the server:
   ```bash
   # Using the project venv
   ../venv/bin/python manage.py runserver 8001
   ```
   *Note: Consumers will start automatically in background threads.*
