import time
import enum
import logging
from typing import Callable, Any, Optional

logger = logging.getLogger("resilience")

class CircuitState(enum.Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"

class CircuitBreakerOpenError(Exception):
    def __init__(self, name: str):
        super().__init__(f"Circuit breaker [{name}] is OPEN – call rejected")
        self.name = name

class CircuitBreaker:
    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        success_threshold: int = 2,
        timeout: float = 30.0,
        half_open_max_calls: int = 3,
        fallback: Optional[Callable[[], Any]] = None
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.success_threshold = success_threshold
        self.timeout = timeout
        self.half_open_max_calls = half_open_max_calls
        self.fallback = fallback
        
        self.state = CircuitState.CLOSED
        self.failures = 0
        self.successes = 0
        self.total_calls = 0
        self.last_failure_time = None
        self.last_state_change = time.time()
        self.half_open_calls = 0

    def execute(self, func: Callable[..., Any], *args, **kwargs) -> Any:
        self.total_calls += 1
        self._maybe_transition_from_open()

        if self.state == CircuitState.OPEN:
            logger.warning(f"Circuit breaker [{self.name}] is OPEN – rejecting call")
            if self.fallback:
                return self.fallback()
            raise CircuitBreakerOpenError(self.name)

        if self.state == CircuitState.HALF_OPEN:
            if self.half_open_calls >= self.half_open_max_calls:
                logger.warning(f"Circuit breaker [{self.name}] HALF_OPEN – max probe calls reached, rejecting")
                if self.fallback:
                    return self.fallback()
                raise CircuitBreakerOpenError(self.name)
            self.half_open_calls += 1

        try:
            result = func(*args, **kwargs)
            self._on_success()
            return result
        except Exception as e:
            self._on_failure(e)
            raise

    def get_state(self) -> CircuitState:
        self._maybe_transition_from_open()
        return self.state

    def reset(self):
        self._transition_to(CircuitState.CLOSED)
        self.failures = 0
        self.successes = 0
        self.half_open_calls = 0

    def _on_success(self):
        self.failures = 0
        if self.state == CircuitState.HALF_OPEN:
            self.successes += 1
            if self.successes >= self.success_threshold:
                self._transition_to(CircuitState.CLOSED)

    def _on_failure(self, err: Exception):
        self.last_failure_time = time.time()
        self.successes = 0
        self.failures += 1

        if self.state == CircuitState.HALF_OPEN:
            self._transition_to(CircuitState.OPEN)
            return

        if self.state == CircuitState.CLOSED and self.failures >= self.failure_threshold:
            self._transition_to(CircuitState.OPEN)

        logger.error(f"Circuit breaker [{self.name}] recorded failure: {err}")

    def _maybe_transition_from_open(self):
        if (
            self.state == CircuitState.OPEN and
            self.last_failure_time is not None and
            time.time() - self.last_failure_time >= self.timeout
        ):
            self._transition_to(CircuitState.HALF_OPEN)

    def _transition_to(self, next_state: CircuitState):
        if self.state == next_state:
            return
        logger.info(f"Circuit breaker [{self.name}] transition: {self.state.value} -> {next_state.value}")
        self.state = next_state
        self.last_state_change = time.time()
        self.half_open_calls = 0
        self.successes = 0
        if next_state == CircuitState.CLOSED:
            self.failures = 0
