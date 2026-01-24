# User Service

The Service responsible for managing user data and retrieval.

## API Documentation

### User Retrieval

#### Get User by ID
Retrieve details of a specific user.
- **URL**: \`/users/:userId\`
- **Method**: \`GET\`
- **Response**:
  \`\`\`json
  {
    "user_id": "123",
    "user_name": "John Doe",
    "email": "john@example.com",
    "role": "admin",
    "infra_id": ["infra-1"],
    "created_at": "2024-01-01T00:00:00.000Z",
    "updated_at": "2024-01-01T00:00:00.000Z"
  }
  \`\`\`

#### Search Users
Search for users by name or email.
- **URL**: \`/users\`
- **Method**: \`GET\`
- **Query Params**:
  - \`q\`: Search query string
- **Response**: List of users matching the query.
  \`\`\`json
  [
    {
      "user_id": "123",
      "user_name": "John Doe",
      ...
    }
  ]
  \`\`\`
