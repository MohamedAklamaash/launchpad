import axios, { type AxiosInstance, type AxiosRequestConfig, type AxiosResponse } from "axios";
import { CircuitBreaker, type CircuitBreakerOptions, type CircuitMetrics } from "./circuit-breaker";
import { CreateLogger } from "../utils/logger";

const rootLogger = CreateLogger({ name: "resilience" });

export interface ResilientHttpClientOptions {
    /** Base URL for all requests */
    baseURL?: string;
    /** Default request timeout in ms (default: 5000) */
    requestTimeout?: number;
    /** Name for this client – used in logs and circuit breaker label */
    name: string;
    /** Circuit breaker options */
    circuitBreaker?: CircuitBreakerOptions;
    /** Additional axios defaults */
    axiosDefaults?: AxiosRequestConfig;
}
export class ResilientHttpClient {
    private readonly client: AxiosInstance;
    private readonly breaker: CircuitBreaker;
    private readonly requestTimeout: number;
    private readonly log: ReturnType<typeof rootLogger.child>;

    constructor(options: ResilientHttpClientOptions) {
        this.requestTimeout = options.requestTimeout ?? 5_000;
        this.log = rootLogger.child({ httpClient: options.name });

        this.client = axios.create({
            baseURL: options.baseURL,
            timeout: this.requestTimeout,
            ...options.axiosDefaults,
        });

        this.breaker = new CircuitBreaker(options.name, options.circuitBreaker);
    }

    async get<T = unknown>(
        url: string,
        config?: AxiosRequestConfig
    ): Promise<AxiosResponse<T>> {
        return this.execute<T>(() =>
            this.client.get<T>(url, this.withTimeout(config))
        );
    }

    async post<T = unknown>(
        url: string,
        data?: unknown,
        config?: AxiosRequestConfig
    ): Promise<AxiosResponse<T>> {
        return this.execute<T>(() =>
            this.client.post<T>(url, data, this.withTimeout(config))
        );
    }

    async put<T = unknown>(
        url: string,
        data?: unknown,
        config?: AxiosRequestConfig
    ): Promise<AxiosResponse<T>> {
        return this.execute<T>(() =>
            this.client.put<T>(url, data, this.withTimeout(config))
        );
    }

    async patch<T = unknown>(
        url: string,
        data?: unknown,
        config?: AxiosRequestConfig
    ): Promise<AxiosResponse<T>> {
        return this.execute<T>(() =>
            this.client.patch<T>(url, data, this.withTimeout(config))
        );
    }

    async delete<T = unknown>(
        url: string,
        config?: AxiosRequestConfig
    ): Promise<AxiosResponse<T>> {
        return this.execute<T>(() =>
            this.client.delete<T>(url, this.withTimeout(config))
        );
    }

    getCircuitMetrics(): CircuitMetrics {
        return this.breaker.getMetrics();
    }

    private async execute<T>(fn: () => Promise<AxiosResponse<T>>): Promise<AxiosResponse<T>> {
        const start = Date.now();
        return this.breaker.execute(async () => {
            try {
                const res = await fn();
                this.log.info(
                    { status: res.status, durationMs: Date.now() - start },
                    "HTTP request succeeded"
                );
                return res;
            } catch (err) {
                this.log.error(
                    { err, durationMs: Date.now() - start },
                    "HTTP request failed"
                );
                throw err;
            }
        });
    }

    private withTimeout(config?: AxiosRequestConfig): AxiosRequestConfig {
        return { timeout: this.requestTimeout, ...config };
    }
}
