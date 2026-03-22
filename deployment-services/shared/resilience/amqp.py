import pika
import time
import logging
import json
from typing import Callable, Any, Optional

logger = logging.getLogger("resilience")


class ResilientPikaProducer:

    def __init__(self, url: str, exchange: str, exchange_type: str = "topic", name: str = "producer"):
        self.url = url
        self.exchange = exchange
        self.exchange_type = exchange_type
        self.name = name
        self.connection: Optional[pika.BlockingConnection] = None
        self.channel: Optional[pika.adapters.blocking_connection.BlockingChannel] = None
        self._buffer = []
        self._max_buffer_size = 100

    def connect(self):
        """Open connection and declare the exchange. Idempotent if already connected."""
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

    def _is_connected(self) -> bool:
        return bool(
            self.connection
            and not self.connection.is_closed
            and self.channel
            and self.channel.is_open
        )

    def publish(self, routing_key: str, body: Any, properties: Optional[pika.BasicProperties] = None):
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
                logger.error(
                    f"AMQP Producer [{self.name}] reconnect failed — message buffered",
                    extra={"error": str(e)},
                    exc_info=True,
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
            logger.error(
                f"AMQP Producer [{self.name}] publish failed — buffering for retry",
                extra={"routing_key": routing_key, "error": str(e)},
                exc_info=True,
            )
            self._buffer.append((routing_key, serialized, properties))
            try:
                self.close()
            except Exception:
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
            except Exception:
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
        self.connection: Optional[pika.BlockingConnection] = None
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
            except Exception as e:
                if self._should_stop:
                    break
                logger.error(
                    f"AMQP Consumer [{self.name}] error: {e}. Reconnecting in {backoff}s...",
                    exc_info=True,
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
            except Exception:
                pass
        if self.connection and not self.connection.is_closed:
            try:
                self.connection.close()
            except Exception:
                pass
