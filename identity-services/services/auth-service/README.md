**Service** : Auth Service

**Things to Note**:
    - A user can belong to multiple infras but they can join into an infra one by one
    - allow all possible crud for an invited user into infra
    - send notification in mail for otp using the notification service
    - send a event that user has been created
    - use bullmq or celery to publish an event and send a notification