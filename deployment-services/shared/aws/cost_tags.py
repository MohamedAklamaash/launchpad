"""Cost-allocation tag keys shared between application-service (which writes them onto
per-app AWS resources, see aws/tags.py) and infrastructure-service (which reads them back
out of Cost Explorer, see api/services/cost_service.py). Defined once here so the two
services can never drift on the literal tag key strings."""

TAG_INFRA_KEY = "launchpad:infra"
TAG_APP_KEY = "launchpad:app"
