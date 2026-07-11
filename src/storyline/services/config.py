from dataclasses import dataclass
from typing import Optional


@dataclass
class ServiceConfig:
    name: str
    port: int
    health_endpoint: str
    start_timeout: int = 240
    stop_timeout: int = 30
    health_check_interval: int = 20
    max_retries: int = 3
    profile: Optional[str] = None
