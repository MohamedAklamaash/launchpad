from django.db import models
from shared.utils.uuid import uuid7_pk


class ReservedDnsLabel(models.Model):
    """Append-only record of every dns_label ever minted, kept independent of the owning
    Infrastructure row's lifecycle.

    Infrastructure.delete_infrastructure() hard-deletes the row on purpose so (user, name)
    frees up for reuse. A label tombstone has to survive that delete, so infrastructure_id
    here is a plain UUID field, not a ForeignKey — a real FK would either cascade-delete this
    row (defeating the whole point) or need PROTECT/SET_NULL, both wrong for an audit trail
    that must outlive its parent.
    """

    id = models.UUIDField(primary_key=True, default=uuid7_pk, editable=False)
    label = models.CharField(max_length=16, unique=True, db_index=True)
    infrastructure_id = models.UUIDField()
    reserved_at = models.DateTimeField(auto_now_add=True)
