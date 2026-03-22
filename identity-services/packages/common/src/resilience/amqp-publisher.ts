import { connect, type Channel, type ChannelModel, type Options } from 'amqplib';
import { CreateLogger } from '../utils/logger.js';

const rootLogger = CreateLogger({ name: 'resilience' });

export interface AmqpPublisherOptions {
    /** AMQP URL */
    url: string;
    /** Exchange name */
    exchange: string;
    /** Exchange type (default: "topic") */
    exchangeType?: string;
    /** Max reconnect attempts (default: 10, 0 = infinite) */
    maxRetries?: number;
    /** Initial retry delay in ms (doubles each attempt, default: 1000) */
    retryDelay?: number;
    /** Max messages to buffer while disconnected (default: 100) */
    maxBufferSize?: number;
    /** Name for logging */
    name?: string;
}

interface BufferedMessage {
    routingKey: string;
    content: Buffer;
    options: Options.Publish;
}

export class ResilientAmqpPublisher {
    private channel: Channel | null = null;
    private connection: ChannelModel | null = null;
    private buffer: BufferedMessage[] = [];
    private reconnecting = false;
    private stopped = false;
    private retryCount = 0;

    private readonly url: string;
    private readonly exchange: string;
    private readonly exchangeType: string;
    private readonly maxRetries: number;
    private readonly retryDelay: number;
    private readonly maxBufferSize: number;
    private readonly log: ReturnType<typeof rootLogger.child>;

    constructor(options: AmqpPublisherOptions) {
        this.url = options.url;
        this.exchange = options.exchange;
        this.exchangeType = options.exchangeType ?? 'topic';
        this.maxRetries = options.maxRetries ?? 10;
        this.retryDelay = options.retryDelay ?? 1_000;
        this.maxBufferSize = options.maxBufferSize ?? 100;
        this.log = rootLogger.child({ amqpPublisher: options.name ?? options.exchange });
    }

    async connect(): Promise<void> {
        if (this.connection) return;
        await this.attemptConnect();
    }

    publish(routingKey: string, content: Buffer, options: Options.Publish = {}): void {
        const msg: BufferedMessage = { routingKey, content, options };

        if (!this.channel) {
            this.log.warn({ routingKey }, 'Channel not ready – buffering message');
            if (this.buffer.length >= this.maxBufferSize) {
                const dropped = this.buffer.shift();
                this.log.warn(
                    { routingKey: dropped?.routingKey },
                    'Buffer full – dropped oldest message',
                );
            }
            this.buffer.push(msg);
            this.scheduleReconnect();
            return;
        }

        this.doPublish(msg);
    }

    isConnected(): boolean {
        return this.channel !== null;
    }

    async close(): Promise<void> {
        this.stopped = true;
        try {
            await this.channel?.close();
            await this.connection?.close();
        } catch (err) {
            this.log.error({ err }, 'Error closing AMQP publisher');
        } finally {
            this.channel = null;
            this.connection = null;
        }
    }

    private doPublish(msg: BufferedMessage): void {
        if (!this.channel) return;
        const ok = this.channel.publish(this.exchange, msg.routingKey, msg.content, {
            contentType: 'application/json',
            persistent: true,
            ...msg.options,
        });
        if (!ok) {
            this.log.warn({ routingKey: msg.routingKey }, 'Publish returned false (backpressure)');
        }
    }

    private async flushBuffer(): Promise<void> {
        if (!this.channel || this.buffer.length === 0) return;
        this.log.info({ count: this.buffer.length }, 'Flushing buffered messages');
        const toFlush = [...this.buffer];
        this.buffer = [];
        for (const msg of toFlush) {
            this.doPublish(msg);
        }
    }

    private async attemptConnect(): Promise<void> {
        this.log.info('Connecting to RabbitMQ...');
        const conn = await connect(this.url);
        this.connection = conn;
        const ch = await conn.createChannel();
        await ch.assertExchange(this.exchange, this.exchangeType, { durable: true });
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

        this.log.info('RabbitMQ publisher connected');
        await this.flushBuffer();
    }

    private scheduleReconnect(): void {
        if (this.reconnecting || this.stopped) return;
        if (this.maxRetries > 0 && this.retryCount >= this.maxRetries) {
            this.log.error(
                { retryCount: this.retryCount },
                'Max reconnect attempts reached – giving up',
            );
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
