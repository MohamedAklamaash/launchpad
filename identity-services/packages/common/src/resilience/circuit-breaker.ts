import { CreateLogger } from "../utils/logger.js";

const rootLogger = CreateLogger({ name: "resilience" });

export type CircuitState = "CLOSED" | "OPEN" | "HALF_OPEN";

export interface CircuitBreakerOptions {
    /** Number of consecutive failures before opening the circuit */
    failureThreshold?: number;
    /** Number of consecutive successes in HALF_OPEN before closing */
    successThreshold?: number;
    /** Milliseconds to wait in OPEN state before transitioning to HALF_OPEN */
    timeout?: number;
    /** Max concurrent calls allowed in HALF_OPEN state */
    halfOpenMaxCalls?: number;
    /** Optional fallback function called when circuit is OPEN */
    fallback?: <T>() => T | Promise<T>;
}

export interface CircuitMetrics {
    state: CircuitState;
    failures: number;
    successes: number;
    totalCalls: number;
    lastFailureTime: number | null;
    lastStateChange: number;
}

export class CircuitBreakerOpenError extends Error {
    constructor(name: string) {
        super(`Circuit breaker [${name}] is OPEN – call rejected`);
        this.name = "CircuitBreakerOpenError";
    }
}

export class CircuitBreaker {
    private state: CircuitState = "CLOSED";
    private failures = 0;
    private successes = 0;
    private totalCalls = 0;
    private lastFailureTime: number | null = null;
    private lastStateChange = Date.now();
    private halfOpenCalls = 0;

    private readonly failureThreshold: number;
    private readonly successThreshold: number;
    private readonly timeout: number;
    private readonly halfOpenMaxCalls: number;
    private readonly fallback?: <T>() => T | Promise<T>;
    private readonly log: ReturnType<typeof rootLogger.child>;

    constructor(
        private readonly name: string,
        options: CircuitBreakerOptions = {}
    ) {
        this.failureThreshold = options.failureThreshold ?? 5;
        this.successThreshold = options.successThreshold ?? 2;
        this.timeout = options.timeout ?? 30_000;
        this.halfOpenMaxCalls = options.halfOpenMaxCalls ?? 3;
        this.fallback = options.fallback;
        this.log = rootLogger.child({ circuitBreaker: name });
    }

    async execute<T>(fn: () => Promise<T>): Promise<T> {
        this.totalCalls++;
        this.maybeTransitionFromOpen();

        if (this.state === "OPEN") {
            this.log.warn({ state: this.state }, "Circuit OPEN – rejecting call");
            if (this.fallback) return this.fallback<T>();
            throw new CircuitBreakerOpenError(this.name);
        }

        if (this.state === "HALF_OPEN") {
            if (this.halfOpenCalls >= this.halfOpenMaxCalls) {
                this.log.warn("Circuit HALF_OPEN – max probe calls reached, rejecting");
                if (this.fallback) return this.fallback<T>();
                throw new CircuitBreakerOpenError(this.name);
            }
            this.halfOpenCalls++;
        }

        try {
            const result = await fn();
            this.onSuccess();
            return result;
        } catch (err) {
            this.onFailure(err);
            throw err;
        }
    }

    getState(): CircuitState {
        this.maybeTransitionFromOpen();
        return this.state;
    }

    getMetrics(): CircuitMetrics {
        return {
            state: this.getState(),
            failures: this.failures,
            successes: this.successes,
            totalCalls: this.totalCalls,
            lastFailureTime: this.lastFailureTime,
            lastStateChange: this.lastStateChange,
        };
    }

    reset() {
        this.transitionTo("CLOSED");
        this.failures = 0;
        this.successes = 0;
        this.halfOpenCalls = 0;
    }

    private onSuccess() {
        this.failures = 0;
        if (this.state === "HALF_OPEN") {
            this.successes++;
            if (this.successes >= this.successThreshold) {
                this.transitionTo("CLOSED");
            }
        }
    }

    private onFailure(err: unknown) {
        this.lastFailureTime = Date.now();
        this.successes = 0;
        this.failures++;

        if (this.state === "HALF_OPEN") {
            this.transitionTo("OPEN");
            return;
        }

        if (this.state === "CLOSED" && this.failures >= this.failureThreshold) {
            this.transitionTo("OPEN");
        }

        this.log.error({ err, failures: this.failures }, "Circuit breaker recorded failure");
    }

    private maybeTransitionFromOpen() {
        if (
            this.state === "OPEN" &&
            this.lastFailureTime !== null &&
            Date.now() - this.lastFailureTime >= this.timeout
        ) {
            this.transitionTo("HALF_OPEN");
        }
    }

    private transitionTo(next: CircuitState) {
        if (this.state === next) return;
        this.log.info({ from: this.state, to: next }, "Circuit breaker state transition");
        this.state = next;
        this.lastStateChange = Date.now();
        this.halfOpenCalls = 0;
        this.successes = 0;
        if (next === "CLOSED") this.failures = 0;
    }
}
