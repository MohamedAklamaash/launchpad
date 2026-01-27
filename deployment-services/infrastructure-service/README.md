# Infrastructure Service API

This service manages cloud infrastructure metadata and user access.

## Base URL
`/api/v1/`

## Endpoints

### Infrastructures

**Note on Trailing Slashes**: `APPEND_SLASH` is disabled. Ensure your request URLs match the definitions exactly.

**Authentication**: Attach `Authorization: Bearer <access_token>` header. CSRF is exempted for these API endpoints.

#### List Infrastructures
- **URL**: `/infrastructures/`
- **Method**: `GET`
- **Description**: Returns all infrastructures owned by the authenticated user.
- **Response**: `200 OK`
  ```json
  [
    {
      "id": "uuid",
      "name": "string",
      "cloud_provider": "string",
      "max_cpu": 0.0,
      "max_memory": 0.0,
      "is_cloud_authenticated": boolean,
      "metadata": {},
      "created_at": "ISO-8601",
      "updated_at": "ISO-8601",
      "invited_users": ["uuid"]
    }
  ]
  ```

#### Create Infrastructure
- **URL**: `/infrastructures/`
- **Method**: `POST`
- **Body**:
  ```json
  {
    "name": "string",
    "cloud_provider": "string",
    "max_cpu": 0.0,
    "max_memory": 0.0,
    "metadata": {}
  }
  ```
- **Response**: `201 Created`

#### Get Infrastructure Detail
- **URL**: `/infrastructures/<id>/`
- **Method**: `GET`
- **Response**: `200 OK`

#### Update Infrastructure
- **URL**: `/infrastructures/<id>/`
- **Method**: `PUT`
- **Body**: (Any subset of fields)
- **Response**: `200 OK`

#### Delete Infrastructure
- **URL**: `/infrastructures/<id>/`
- **Method**: `DELETE`
- **Response**: `204 No Content`
