import os
import subprocess
import time
import logging
from enum import Enum
from typing import Optional

from .config import ServiceConfig

_log = logging.getLogger(__name__)


class ServiceStatus(Enum):
    OFFLINE = "offline"
    STARTING = "starting"
    ONLINE = "online"
    STOPPING = "stopping"
    ERROR = "error"


class ServiceManager:
    _instance: Optional['ServiceManager'] = None
    _active_service: Optional[str] = None
    _active_llm_profile: Optional[str] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._subprocess_run = subprocess.run
        self._TimeoutExpired = subprocess.TimeoutExpired
        self._CalledProcessError = subprocess.CalledProcessError
        self._active_llm_profile = None
        self._configs = {
            'llm': ServiceConfig(
                name='llamacpp',
                port=11432,
                health_endpoint='http://127.0.0.1:11432/v1/models',
                start_timeout=120,
                stop_timeout=60,
            ),
            'tts': ServiceConfig(
                name='qwentts',
                port=11433,
                health_endpoint='http://127.0.0.1:11433/health',
                start_timeout=120,
                stop_timeout=30,
            ),
            'image-gen': ServiceConfig(
                name='qwen-image-gen',
                port=11434,
                health_endpoint='http://127.0.0.1:11434/health',
                start_timeout=60,
                stop_timeout=15,
            ),
        }

    def get_config(self, service_name: str) -> ServiceConfig:
        if service_name not in self._configs:
            raise ValueError(f"Unknown service: {service_name}")
        return self._configs[service_name]

    def get_status(self, service_name: str) -> ServiceStatus:
        config = self.get_config(service_name)
        is_running = self._is_service_running(config.name)
        if not is_running:
            return ServiceStatus.OFFLINE
        return ServiceStatus.ONLINE

    def _is_service_running(self, service_name: str) -> bool:
        try:
            result = self._subprocess_run(
                ['systemctl', '--user', 'is-active', service_name],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return result.returncode == 0
        except self._TimeoutExpired:
            return False
        except Exception:
            return False

    def _is_service_enabled(self, service_name: str) -> bool:
        try:
            result = self._subprocess_run(
                ['systemctl', '--user', 'is-enabled', service_name],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return result.returncode == 0
        except self._TimeoutExpired:
            return False
        except Exception:
            return False

    def _start_service(self, service_name: str) -> bool:
        config = self.get_config(service_name)
        try:
            self._subprocess_run(
                ['systemctl', '--user', 'start', config.name],
                check=True,
                timeout=10,
            )
            return True
        except self._CalledProcessError:
            return False
        except Exception:
            return False

    def _stop_service(self, service_name: str) -> bool:
        config = self.get_config(service_name)
        try:
            self._subprocess_run(
                ['systemctl', '--user', 'stop', config.name],
                check=True,
                timeout=10,
            )
            return True
        except self._CalledProcessError:
            return False
        except Exception:
            return False

    def _wait_for_service(self, service_name: str) -> bool:
        config = self.get_config(service_name)
        start_time = time.time()
        timeout = config.start_timeout

        print(f"Waiting for {service_name} to become healthy (timeout={timeout}s)...")
        last_error = None
        while time.time() - start_time < timeout:
            try:
                import requests
                response = requests.get(config.health_endpoint, timeout=5)
                if response.status_code == 200:
                    _log.info("%s is healthy.", service_name)
                    return True
                last_error = f"HTTP {response.status_code}"
            except Exception as exc:
                last_error = str(exc)
            time.sleep(1)

        _log.info("%s health check failed after %ds. Last error: %s", service_name, timeout, last_error)
        return False

    def _wait_for_service_offline(self, service_name: str) -> bool:
        config = self.get_config(service_name)
        start_time = time.time()
        timeout = config.stop_timeout

        print(f"Waiting for {service_name} to go offline (timeout={timeout}s)...")
        while time.time() - start_time < timeout:
            if self.get_status(service_name) == ServiceStatus.OFFLINE:
                _log.info("%s is offline.", service_name)
                return True
            time.sleep(1)

        _log.info("%s did not go offline within %ds.", service_name, timeout)
        return False

    def _wait_for_gpu_memory(self, free_mb: int = 4000, timeout: int = 30) -> bool:
        """Wait for sufficient free GPU memory, not just for systemd to report offline.

        systemd may report a service as inactive before the process has exited
        and released its CUDA allocations.  This polls nvidia-smi until the
        GPU reports *free_mb* MiB available, or until *timeout* seconds elapse.
        """
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                result = self._subprocess_run(
                    [
                        "nvidia-smi",
                        "--query-gpu=memory.free",
                        "--format=csv,noheader,nounits",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                free = int(result.stdout.strip())
                if free >= free_mb:
                    return True
            except Exception:
                pass
            time.sleep(1)
        return False

    def start(self, service_name: str) -> bool:
        if service_name not in self._configs:
            raise ValueError(f"Unknown service: {service_name}")

        config = self.get_config(service_name)

        if self.get_status(service_name) == ServiceStatus.ONLINE:
            return True

        if self._active_service is not None and self._active_service != service_name:
            other_service = self._active_service
            if self.get_status(other_service) == ServiceStatus.ONLINE:
                if not self.stop(other_service):
                    raise RuntimeError(f"Failed to stop active service: {other_service}")
                # systemd may report inactive before the process exits and
                # releases CUDA allocations; wait for actual GPU memory.
                if not self._wait_for_gpu_memory():
                    raise RuntimeError(
                        f"GPU memory not freed after stopping {other_service}. "
                        f"The process may still be alive or another process is using the GPU."
                    )

        if not self._start_service(service_name):
            raise RuntimeError(f"Failed to start service: {service_name}")

        if not self._wait_for_service(service_name):
            raise RuntimeError(f"Service {service_name} failed to start within timeout")

        self._active_service = service_name
        return True

    def stop(self, service_name: str) -> bool:
        if service_name not in self._configs:
            raise ValueError(f"Unknown service: {service_name}")

        config = self.get_config(service_name)

        if self.get_status(service_name) != ServiceStatus.ONLINE:
            return True

        if self._active_service == service_name:
            self._active_service = None
        if service_name == 'llm':
            self._active_llm_profile = None

        if not self._stop_service(service_name):
            raise RuntimeError(f"Failed to stop service: {service_name}")

        if not self._wait_for_service_offline(service_name):
            raise RuntimeError(f"Service {service_name} failed to stop within timeout")

        return True

    def ensure_only_one_active(self) -> Optional[str]:
        active = [
            name for name in self._configs
            if self.get_status(name) == ServiceStatus.ONLINE
        ]
        if len(active) > 1:
            raise RuntimeError(f"Multiple services are active: {active}")
        return active[0] if active else None

    def get_active_service(self) -> Optional[str]:
        return self.ensure_only_one_active()

    def start_if_needed(self, service_name: str) -> bool:
        if self.get_status(service_name) == ServiceStatus.ONLINE:
            return True
        return self.start(service_name)

    def stop_if_running(self, service_name: str) -> bool:
        if self.get_status(service_name) == ServiceStatus.ONLINE:
            return self.stop(service_name)
        return True

    def start_all(self) -> dict[str, bool]:
        results = {}
        for name in self._configs:
            results[name] = self.start(name)
        return results

    def _set_llm_profile(self, profile: str) -> None:
        llamacpp_dir = os.path.expanduser("~/llamacpp")
        target = os.path.join(llamacpp_dir, f"{profile}.conf")
        current = os.path.join(llamacpp_dir, "current.conf")

        if not os.path.isfile(target):
            raise ValueError(f"LLM profile file not found: {target}")

        try:
            os.unlink(current)
        except FileNotFoundError:
            pass
        os.symlink(target, current)

    def ensure_llm_profile(self, profile: str) -> None:
        if self._active_service == 'llm' and self._active_llm_profile == profile:
            if self.get_status('llm') == ServiceStatus.ONLINE:
                return

        if self.get_status('llm') == ServiceStatus.ONLINE:
            self.stop('llm')

        self._set_llm_profile(profile)
        self.start('llm')
        self._active_llm_profile = profile

    def stop_all(self) -> dict[str, bool]:
        results = {}
        for name in self._configs:
            results[name] = self.stop(name)
        return results
