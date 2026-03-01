- check hyper hyper log for rate limit algo, also look into fencing tokens and some cool rate limit algos

## assume role

```bash
aws sts assume-role \
  --role-arn arn:aws:iam::<TARGET_ACCOUNT_ID>:role/DeploymentRole \
  --role-session-name deployment-session
```

<TARGET_ACCOUNT_ID> → the account where role was created

**will get a resp like**

```json
{
  "Credentials": {
    "AccessKeyId": "ASIA...",
    "SecretAccessKey": "wJalrXUtnFEMI...",
    "SessionToken": "IQoJb3JpZ2luX2VjEOX//////////wEaCXVzLXdlc3QtMiJHMEUCIQ...",
    "Expiration": "2026-02-28T14:32:01Z"
  }
}
```