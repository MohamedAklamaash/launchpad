"""Found on real AWS: the reconcile published after the first provision was dropped because
the writer had never started, so no queue was bound to the topic exchange yet."""
from unittest.mock import MagicMock, patch

from api.services.platform_dns import producer as dns_producer


def test_reconcile_declares_the_writer_queue_before_publishing():
    fake = MagicMock()
    order = []
    fake.declare_queue.side_effect = lambda **kw: order.append(("declare", kw))
    fake.publish.side_effect = lambda *a, **kw: order.append(("publish", a))
    with patch.object(dns_producer, "_get_producer", return_value=fake):
        dns_producer.request_dns_reconcile("01a0eac8-e7fc-7957-a486-739e6c882442", coalesce=False)

    assert order[0] == ("declare", {"queue": dns_producer.DNS_RECONCILE_QUEUE,
                                    "routing_key": dns_producer.DNS_RECONCILE_ROUTING_KEY})
    assert order[1][0] == "publish"


def test_a_declare_failure_still_publishes():
    fake = MagicMock()
    fake.declare_queue.side_effect = RuntimeError("broker hiccup")
    with patch.object(dns_producer, "_get_producer", return_value=fake):
        dns_producer.request_dns_reconcile("01a0eac8-e7fc-7957-a486-739e6c882442", coalesce=False)
    fake.publish.assert_called_once()
