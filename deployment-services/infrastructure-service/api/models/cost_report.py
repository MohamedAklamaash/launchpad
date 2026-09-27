from django.db import models
from shared.utils.uuid import uuid7_pk


class CostReport(models.Model):
    """A cached cost_service result for one infrastructure and query window.

    Cost Explorer bills the customer $0.01 per GetCostAndUsage call, so a cache hit must
    never call CE again. Cached per (infrastructure, window_start, window_end); `computed_on`
    is compared to today's date rather than a TTL — one CE call per infra per window per day,
    however many times the dashboard polls the endpoint that day.
    """
    id = models.UUIDField(primary_key=True, default=uuid7_pk, editable=False)
    infrastructure = models.ForeignKey(
        'Infrastructure', on_delete=models.CASCADE, related_name='cost_reports',
    )
    window_start = models.DateField()
    window_end = models.DateField()
    computed_on = models.DateField()
    payload = models.JSONField()

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['infrastructure', 'window_start', 'window_end'],
                name='unique_cost_report_window',
            ),
        ]
