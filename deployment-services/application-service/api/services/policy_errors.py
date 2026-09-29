class PolicyRefreshRequiredError(ValueError):
    """Raised when the customer's assumed role can't perform an action Launchpad needs yet
    because their applied LaunchpadDeploymentPolicy is behind.

    Distinguished from a generic ValueError so a view can return a 422 with the shared
    machine-readable `policy_refresh_required` shape instead of a plain 400. Mirrors
    infrastructure-service's `api/services/policy_errors.py` byte-for-byte — the two
    services don't share a Python import path, so the dashboard's one shape is kept in
    sync by convention rather than a shared module (deployment-services/shared/ has no
    Django-model-free home for it either)."""

    def __init__(self, message, denied_actions=None):
        super().__init__(message)
        self.denied_actions = denied_actions or []
