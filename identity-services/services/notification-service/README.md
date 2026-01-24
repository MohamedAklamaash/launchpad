# Notification Service

The Service responsible for sending emails and managing notifications.

## API Documentation

### Notifications

#### Get User Notifications
Retrieve notification history for a user.
- **URL**: \`/notifications/user/:userId\`
- **Method**: \`GET\`
- **Response**: List of notifications sorted by newest first.
  \`\`\`json
  [
    {
      "_id": "679e...",
      "user_id": "123",
      "user_name": "John Doe",
      "email": "john@example.com",
      "infra_id": "infra-1",
      "source": "mail",
      "metadata": {},
      "created_at": 1700000000000
    }
  ]
  \`\`\`
