import { connect, type Channel, type ChannelModel, type ConsumeMessage } from 'amqplib';
import { CreateLogger } from '../utils/logger.js';

const rootLogger = CreateLogger({ name: 'resilience' });

export interface AmqpConsumerOptions {
    /** AMQP URL */
    url: string;
    /** Exchange name */
    exchange: string;
    /** Exchange type (default: "topic") */
    exchangeType?: string;
    /** Queue name */
    queue: string;
    /** Routing key to bind */
    routingKey: string;
    /** Max unacknowledged messages (backpressure, default: 10) */
    prefetchCount?: number;
    /** Max reconnect attempts (default: 0 = infinite) */
    maxRetries?: number;
    /** Initial retry delay in ms (doubles each attempt, default: 1000) */
    retryDelay?: number;
    /** Name for logging */
    name?: string;
}

export type MessageHandler = (msg: ConsumeMessage, channel: Channel) => Promise<void>;

export class ResilientAmqpConsumer {
    private channel: Channel | null = null;
    private connection: ChannelModel | null = null;
    private handler: MessageHandler | null = null;
    private stopped = false;
    private reconnecting = false;
    private retryCount = 0;
    private inFlight = 0;

    private readonly url: string;
    private readonly exchange: string;
    private readonly exchangeType: string;
    private readonly queue: string;
    private readonly routingKey: string;
    private readonly prefetchCount: number;
    private readonly maxRetries: number;
    private readonly retryDelay: number;
    private readonly log: ReturnType<typeof rootLogger.child>;

    constructor(options: AmqpConsumerOptions) {
        this.url = options.url;
        this.exchange = options.exchange;
        this.exchangeType = options.exchangeType ?? 'topic';
        this.queue = options.queue;
        this.routingKey = options.routingKey;
        this.prefetchCount = options.prefetchCount ?? 10;
        this.maxRetries = options.maxRetries ?? 0;
        this.retryDelay = options.retryDelay ?? 1_000;
        this.log = rootLogger.child({ amqpConsumer: options.name ?? options.queue });
    }

    async start(handler: MessageHandler): Promise<void> {
        this.handler = handler;
        await this.attemptConnect();
    }

    isConnected(): boolean {
        return this.channel !== null;
    }

    async stop(): Promise<void> {
        this.stopped = true;
        this.log.info(
            { inFlight: this.inFlight },
            'Stopping consumer – draining in-flight messages',
        );

        // Wait up to 10 s for in-flight messages to complete
        const deadline = Date.now() + 10_000;
        while (this.inFlight > 0 && Date.now() < deadline) {
            await new Promise((r) => setTimeout(r, 100));
        }

        try {
            await this.channel?.close();
            await this.connection?.close();
        } catch (err) {
            this.log.error({ err }, 'Error closing AMQP consumer');
        } finally {
            this.channel = null;
            this.connection = null;
        }
    }

    private async attemptConnect(): Promise<void> {
        this.log.info('Connecting to RabbitMQ...');
        const conn = await connect(this.url);
        this.connection = conn;
        const ch = await conn.createChannel();
        await ch.prefetch(this.prefetchCount);
        await ch.assertExchange(this.exchange, this.exchangeType, { durable: true });
        await ch.assertQueue(this.queue, { durable: true });
        await ch.bindQueue(this.queue, this.exchange, this.routingKey);
        this.channel = ch;
        this.retryCount = 0;

        conn.on('close', () => {
            this.log.warn('RabbitMQ connection closed');
            this.channel = null;
            this.connection = null;
            this.scheduleReconnect();
        });

        conn.on('error', (err) => {
            this.log.error({ err }, 'RabbitMQ connection error');
        });

        ch.consume(this.queue, async (msg: ConsumeMessage | null) => {
            if (!msg) return;
            this.inFlight++;
            try {
                await this.handler!(msg, ch);
            } catch (err: unknown) {
                this.log.error({ err }, 'Unhandled error in message handler');
                ch.nack(msg, false, false); // dead-letter without requeue
            } finally {
                this.inFlight--;
            }
        });

        this.log.info({ queue: this.queue }, 'RabbitMQ consumer started');
    }

    private scheduleReconnect(): void {
        if (this.reconnecting || this.stopped) return;
        if (this.maxRetries > 0 && this.retryCount >= this.maxRetries) {
            this.log.error({ retryCount: this.retryCount }, 'Max reconnect attempts reached');
            return;
        }

        this.reconnecting = true;
        const delay = Math.min(this.retryDelay * Math.pow(2, this.retryCount), 30_000);
        this.retryCount++;
        this.log.info({ delay, attempt: this.retryCount }, 'Scheduling RabbitMQ reconnect');

        setTimeout(async () => {
            this.reconnecting = false;
            try {
                await this.attemptConnect();
            } catch (err: unknown) {
                this.log.error({ err }, 'Reconnect attempt failed');
                this.scheduleReconnect();
            }
        }, delay);
    }
}
