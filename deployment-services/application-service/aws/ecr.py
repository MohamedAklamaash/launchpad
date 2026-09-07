import json
import logging

logger = logging.getLogger(__name__)

# How many per-commit images to retain per repository. Rollback only needs to reach a
# recent known-good image; keeping every image ever built is unbounded storage cost in the
# customer's account, which they pay for.
RETAINED_COMMIT_IMAGES = 10


class ECRClient:
    def __init__(self, session):
        self.client = session.client('ecr')

    def get_image_uri(self, repository_url, tag):
        return f"{repository_url}:{tag}"

    @staticmethod
    def _lifecycle_policy():
        """Keep the last N per-commit images; never expire a `-latest` tag.

        Rule priority is evaluation order, and a `-latest` image is also matched by the
        broad count rule. The protective rule must therefore come first: if the count rule
        ran first it would eventually expire `-latest` out from under every task definition
        that still references it, and ECS would fail to pull on the next task placement.
        """
        return {
            "rules": [
                {
                    "rulePriority": 1,
                    "description": "Never expire the moving -latest tag; task definitions reference it.",
                    "selection": {
                        "tagStatus": "tagged",
                        "tagPrefixList": ["latest"],
                        "countType": "imageCountMoreThan",
                        "countNumber": 9999,
                    },
                    "action": {"type": "expire"},
                },
                {
                    "rulePriority": 2,
                    "description": f"Retain the {RETAINED_COMMIT_IMAGES} most recent tagged images.",
                    "selection": {
                        "tagStatus": "tagged",
                        "tagPatternList": ["*"],
                        "countType": "imageCountMoreThan",
                        "countNumber": RETAINED_COMMIT_IMAGES,
                    },
                    "action": {"type": "expire"},
                },
                {
                    "rulePriority": 3,
                    "description": "Expire untagged layers left behind when a tag is overwritten.",
                    "selection": {
                        "tagStatus": "untagged",
                        "countType": "sinceImagePushed",
                        "countUnit": "days",
                        "countNumber": 7,
                    },
                    "action": {"type": "expire"},
                },
            ]
        }

    def ensure_lifecycle_policy(self, repository_name):
        """Apply the retention policy. Idempotent — PutLifecyclePolicy replaces wholesale.

        Best-effort: a failure here must never fail a deploy. The consequence of not having
        the policy is storage cost, not a broken application, and the policy is re-applied
        on the next deploy anyway.
        """
        try:
            self.client.put_lifecycle_policy(
                repositoryName=repository_name,
                lifecyclePolicyText=json.dumps(self._lifecycle_policy()),
            )
            logger.info(f"Applied ECR lifecycle policy to {repository_name}")
            return True
        except Exception as e:
            logger.warning(f"Could not apply ECR lifecycle policy to {repository_name}: {e}")
            return False

    @staticmethod
    def repository_name_from_url(repository_url):
        """`123456789012.dkr.ecr.us-east-1.amazonaws.com/launchpad-abc` -> `launchpad-abc`."""
        if not repository_url:
            return None
        return repository_url.split('/', 1)[-1] if '/' in repository_url else repository_url
