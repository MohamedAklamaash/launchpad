import requests
import logging
from typing import Any, Optional, Dict
from .circuit_breaker import CircuitBreaker

logger = logging.getLogger("resilience")

class ResilientHttpClient:
    def __init__(
        self,
        name: str,
        base_url: Optional[str] = None,
        timeout: float = 5.0,
        circuit_breaker_options: Optional[Dict[str, Any]] = None
    ):
        self.name = name
        self.base_url = base_url
        self.timeout = timeout
        
        cb_opts = circuit_breaker_options or {}
        self.breaker = CircuitBreaker(name, **cb_opts)
        self.session = requests.Session()

    def get(self, url: str, **kwargs) -> requests.Response:
        return self._execute("GET", url, **kwargs)

    def post(self, url: str, **kwargs) -> requests.Response:
        return self._execute("POST", url, **kwargs)

    def put(self, url: str, **kwargs) -> requests.Response:
        return self._execute("PUT", url, **kwargs)

    def patch(self, url: str, **kwargs) -> requests.Response:
        return self._execute("PATCH", url, **kwargs)

    def delete(self, url: str, **kwargs) -> requests.Response:
        return self._execute("DELETE", url, **kwargs)

    def _execute(self, method: str, url: str, **kwargs) -> requests.Response:
        full_url = f"{self.base_url.rstrip('/')}/{url.lstrip('/')}" if self.base_url else url
        
        if "timeout" not in kwargs:
            kwargs["timeout"] = self.timeout

        def make_request():
            res = self.session.request(method, full_url, **kwargs)
            res.raise_for_status()
            return res

        start_time = requests.compat.time.time()
        try:
            response = self.breaker.execute(make_request)
            duration = requests.compat.time.time() - start_time
            logger.info(f"HTTP {method} {full_url} succeeded - status: {response.status_code}, duration: {duration:.3f}s")
            return response
        except requests.exceptions.HTTPError as e:
            if e.response.status_code >= 500:
                logger.error(f"HTTP {method} {full_url} failed with server error: {e}")
                raise
            return e.response
        except Exception as e:
            logger.error(f"HTTP {method} {full_url} failed: {e}")
            raise
