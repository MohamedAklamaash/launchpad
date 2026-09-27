class PolicyRefreshRequiredError(ValueError):
    """Raised when the customer's assumed role can't perform an action Launchpad needs yet
    because their applied LaunchpadDeploymentPolicy is behind.

    Distinguished from a generic ValueError so a view can return a 422 with the shared
    machine-readable `policy_refresh_required` shape instead of a plain 400. Shared across
    services (database_service.py's precheck, cost_service.py's Cost Explorer calls) so the
    dashboard has exactly one shape to handle regardless of which action triggered it.
    """

    def __init__(self, message, denied_actions=None):
        super().__init__(message)
        self.denied_actions = denied_actions or []
