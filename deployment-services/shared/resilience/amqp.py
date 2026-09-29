import json
import logging
import os
import time
from collections.abc import Callable
from typing import Any

import pika

logger = logging.getLogger("resilience")


class ResilientPikaProducer:

    def __init__(self, url: str, exchange: str, exchange_type: str = "topic", name: str = "producer"):
        self.url = url
        self.exchange = exchange
        self.exchange_type = exchange_type
        self.name = name
        self.connection: pika.BlockingConnection | None = None
        self.channel: pika.adapters.blocking_connection.BlockingChannel | None = None
        self._buffer = []
        self._max_buffer_size = 100

    def connect(self):
        """Open connection and declare the exchange. Idempotent if already connected.

        Backstop against the exact leak this class caused once already: a Django test
        suite (no live broker mocked) calling a view/service that publishes for real,
        landing junk messages — for infra ids that exist in no database — on the
        developer's local RabbitMQ. Each service's root conftest.py patches connect/publish
        to an in-memory recorder for every test (see the `no_real_broker` fixture) and is
        the primary defense; this guard only matters if that patch is somehow bypassed, in
        which case it refuses to reach pika.BlockingConnection at all. Note this raises
        from inside connect() itself — publish()'s own `except Exception` around its
        auto-connect swallows it and just leaves the message buffered, which is still the
        safe outcome (no socket opened) but not a loud test failure; call connect()
        directly, as the tests here do, to observe the raise.
        """
        if "PYTEST_CURRENT_TEST" in os.environ:
            raise RuntimeError(
                f"ResilientPikaProducer[{self.name}].connect() was called while running "
                "under pytest. Tests must never open a connection to a real broker — "
                "patch ResilientPikaProducer.connect/publish (see each service's root "
                "conftest.py `no_real_broker` fixture) instead of exercising this method "
                "for real."
            )
        if self.connection and not self.connection.is_closed:
            return

        parameters = pika.URLParameters(self.url)
        parameters.heartbeat = 600
        parameters.blocked_connection_timeout = 300
        self.connection = pika.BlockingConnection(parameters)
        self.channel = self.connection.channel()

        self.channel.exchange_declare(
            exchange=self.exchange,
            exchange_type=self.exchange_type,
            durable=True,
        )

        self.channel.confirm_delivery()

        logger.info(f"AMQP Producer [{self.name}] connected to {self.exchange}")
        self._flush_buffer()

    def declare_queue(self, queue: str, routing_key: str):
        """Idempotently declare and bind a consumer's queue from the producer side too,
        so a message published before that consumer's process has ever started still has
        somewhere to land instead of being silently dropped by the broker (a topic
        exchange drops anything with no matching binding). Must be called after
        `connect()`.

        The declare args here MUST match `ResilientPikaConsumer._connect_and_consume`'s
        exactly (durable=True, exclusive=False, auto_delete=False, no extra `arguments`) —
        RabbitMQ raises PRECONDITION_FAILED and closes the channel if two declares for the
        same queue name disagree on any property, so drifting from the consumer's own
        declare would break the consumer's very next (re)connect, not just this call."""
        if not self.channel:
            raise RuntimeError(f"AMQP Producer [{self.name}] declare_queue called before connect()")

        self.channel.queue_declare(
            queue=queue,
            durable=True,
            exclusive=False,
            auto_delete=False,
        )
        self.channel.queue_bind(
            queue=queue,
            exchange=self.exchange,
            routing_key=routing_key,
        )

    def _is_connected(self) -> bool:
        return bool(
            self.connection
            and not self.connection.is_closed
            and self.channel
            and self.channel.is_open
        )

    def publish(self, routing_key: str, body: Any, properties: pika.BasicProperties | None = None):
        if not properties:
            properties = pika.BasicProperties(
                delivery_mode=2,            # persistent — survives broker restart
                content_type="application/json",
            )

        serialized = json.dumps(body) if not isinstance(body, (str, bytes)) else body

        if not self._is_connected():
            logger.warning(
                f"AMQP Producer [{self.name}] not connected — buffering message",
                extra={"routing_key": routing_key},
            )
            if len(self._buffer) >= self._max_buffer_size:
                logger.warning(
                    f"AMQP Producer [{self.name}] buffer full — dropping oldest message"
                )
                self._buffer.pop(0)
            self._buffer.append((routing_key, serialized, properties))
            try:
                self.connect()
            except Exception as e:
                logger.exception(
                    f"AMQP Producer [{self.name}] reconnect failed — message buffered",
                    extra={"error": str(e)},
                )
                return

        try:
            self.channel.basic_publish(
                exchange=self.exchange,
                routing_key=routing_key,
                body=serialized,
                properties=properties,
            )
        except Exception as e:
            logger.exception(
                f"AMQP Producer [{self.name}] publish failed — buffering for retry",
                extra={"routing_key": routing_key, "error": str(e)},
            )
            self._buffer.append((routing_key, serialized, properties))
            try:
                self.close()
            except Exception:  # noqa: S110 - best-effort teardown, the connection is being discarded either way
                pass

    def _flush_buffer(self):
        if not self._buffer:
            return
        logger.info(f"AMQP Producer [{self.name}] flushing {len(self._buffer)} buffered messages")
        to_flush = self._buffer[:]
        self._buffer = []
        for rk, body, props in to_flush:
            self.publish(rk, body, props)

    def close(self):
        if self.connection and not self.connection.is_closed:
            try:
                self.connection.close()
            except Exception:  # noqa: S110 - best-effort teardown, the connection is being discarded either way
                pass
        self.connection = None
        self.channel = None


class ResilientPikaConsumer:
    def __init__(
        self,
        url: str,
        exchange: str,
        queue: str,
        routing_key: str,
        name: str = "consumer",
        prefetch_count: int = 1,
    ):
        self.url = url
        self.exchange = exchange
        self.queue = queue
        self.routing_key = routing_key
        self.name = name
        self.prefetch_count = prefetch_count
        self.connection: pika.BlockingConnection | None = None
        self.channel = None
        self._should_stop = False

    def start(self, callback: Callable):
        """
        Messages are re-queued by the broker if the consumer disconnects
        without ACKing (at-least-once delivery).
        """
        backoff = 5
        while not self._should_stop:
            try:
                self._connect_and_consume(callback)
                backoff = 5  # reset after clean reconnect
            except Exception:
                if self._should_stop:
                    break
                logger.exception(
                    f"AMQP Consumer [{self.name}] error, reconnecting in {backoff}s...",
                )
                time.sleep(backoff)
                backoff = min(backoff * 2, 60)  # exponential back-off, cap at 60s

    def _connect_and_consume(self, callback: Callable):
        parameters = pika.URLParameters(self.url)
        parameters.heartbeat = 600
        parameters.blocked_connection_timeout = 300
        self.connection = pika.BlockingConnection(parameters)
        self.channel = self.connection.channel()

        self.channel.exchange_declare(
            exchange=self.exchange,
            exchange_type="topic",
            durable=True,
        )

        self.channel.queue_declare(
            queue=self.queue,
            durable=True,
            exclusive=False,
            auto_delete=False,
        )

        self.channel.queue_bind(
            queue=self.queue,
            exchange=self.exchange,
            routing_key=self.routing_key,
        )

        self.channel.basic_qos(prefetch_count=self.prefetch_count)
        
        self.channel.basic_consume(
            queue=self.queue,
            on_message_callback=callback,
            auto_ack=False,
        )

        logger.info(
            f"AMQP Consumer [{self.name}] started — "
            f"exchange={self.exchange} queue={self.queue} routing_key={self.routing_key}"
        )
        self.channel.start_consuming()

    def stop(self):
        self._should_stop = True
        if self.channel:
            try:
                self.channel.stop_consuming()
            except Exception:  # noqa: S110 - best-effort teardown, the connection is being discarded either way
                pass
        if self.connection and not self.connection.is_closed:
            try:
                self.connection.close()
            except Exception:  # noqa: S110 - best-effort teardown, the connection is being discarded either way
                pass
