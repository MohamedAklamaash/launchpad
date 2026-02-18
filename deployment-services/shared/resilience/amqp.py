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
        self.connection = None
        self.channel = None
        self._buffer = []
        self._max_buffer_size = 100

    def connect(self):
        if self.connection and not self.connection.is_closed:
            return
        
        try:
            parameters = pika.URLParameters(self.url)
            self.connection = pika.BlockingConnection(parameters)
            self.channel = self.connection.channel()
            self.channel.exchange_declare(
                exchange=self.exchange,
                exchange_type=self.exchange_type,
                durable=True
            )
            logger.info(f"AMQP Producer [{self.name}] connected to {self.exchange}")
            self._flush_buffer()
        except Exception as e:
            logger.error(f"AMQP Producer [{self.name}] failed to connect: {e}")
            raise

    def publish(self, routing_key: str, body: Any, properties: Optional[pika.BasicProperties] = None):
        if not properties:
            properties = pika.BasicProperties(delivery_mode=2, content_type='application/json')
            
        if not self.channel or self.connection.is_closed:
            logger.warning(f"AMQP Producer [{self.name}] not connected, buffering message")
            if len(self._buffer) >= self._max_buffer_size:
                self._buffer.pop(0)
            self._buffer.append((routing_key, body, properties))
            try:
                self.connect()
            except:
                return
        
        try:
            self.channel.basic_publish(
                exchange=self.exchange,
                routing_key=routing_key,
                body=json.dumps(body) if not isinstance(body, (str, bytes)) else body,
                properties=properties
            )
        except Exception as e:
            logger.error(f"AMQP Producer [{self.name}] failed to publish: {e}")
            self._buffer.append((routing_key, body, properties))

    def _flush_buffer(self):
        if not self._buffer:
            return
        logger.info(f"AMQP Producer [{self.name}] flushing {len(self._buffer)} messages")
        to_flush = self._buffer[:]
        self._buffer = []
        for rk, body, props in to_flush:
            self.publish(rk, body, props)

    def close(self):
        if self.connection and not self.connection.is_closed:
            self.connection.close()

class ResilientPikaConsumer:
    def __init__(self, url: str, exchange: str, queue: str, routing_key: str, name: str = "consumer"):
        self.url = url
        self.exchange = exchange
        self.queue = queue
        self.routing_key = routing_key
        self.name = name
        self.connection = None
        self.channel = None
        self._should_stop = False

    def start(self, callback: Callable[[pika.channel.Channel, pika.spec.Basic.Deliver, pika.spec.BasicProperties, bytes], None]):
        while not self._should_stop:
            try:
                self._connect_and_consume(callback)
            except Exception as e:
                if self._should_stop:
                    break
                logger.error(f"AMQP Consumer [{self.name}] error: {e}. Reconnecting in 5s...")
                time.sleep(5)

    def _connect_and_consume(self, callback):
        parameters = pika.URLParameters(self.url)
        self.connection = pika.BlockingConnection(parameters)
        self.channel = self.connection.channel()
        
        self.channel.exchange_declare(exchange=self.exchange, exchange_type='topic', durable=True)
        self.channel.queue_declare(queue=self.queue, durable=True)
        self.channel.queue_bind(queue=self.queue, exchange=self.exchange, routing_key=self.routing_key)
        
        self.channel.basic_qos(prefetch_count=10)
        self.channel.basic_consume(queue=self.queue, on_message_callback=callback)
        
        logger.info(f"AMQP Consumer [{self.name}] started on queue {self.queue}")
        self.channel.start_consuming()

    def stop(self):
        self._should_stop = True
        if self.channel:
            self.channel.stop_consuming()
        if self.connection and not self.connection.is_closed:
            self.connection.close()
