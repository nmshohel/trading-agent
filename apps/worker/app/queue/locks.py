"""Distributed per-account Redis execution lock & fast kill-switch reader."""

from __future__ import annotations

import uuid
from typing import Any

import redis as redis_lib

KILL_SWITCH_KEY = "system:kill_switch"

_RELEASE_LUA = """
if redis.call("get", KEYS[1]) == ARGV[1] then
  return redis.call("del", KEYS[1])
else
  return 0
end
"""


class ExecutionLockManager:
    """Guarantees only one MT5 execution/close runs per broker account at a time."""

    def __init__(
        self,
        redis_url: str,
        *,
        redis_client: Any | None = None,
    ) -> None:
        self._owns_client = redis_client is None
        self._redis = (
            redis_client
            if redis_client is not None
            else redis_lib.Redis.from_url(redis_url, decode_responses=True)
        )

    def close(self) -> None:
        if self._owns_client:
            self._redis.close()

    @staticmethod
    def lock_key(account_login: str) -> str:
        return f"lock:execution:{account_login}"

    def acquire(self, account_login: str, ttl_seconds: int = 30) -> str | None:
        token = str(uuid.uuid4())
        ok = self._redis.set(
            self.lock_key(account_login), token, nx=True, ex=ttl_seconds
        )
        return token if ok else None

    def release(self, account_login: str, token: str) -> bool:
        key = self.lock_key(account_login)
        if hasattr(self._redis, "eval"):
            deleted = self._redis.eval(_RELEASE_LUA, 1, key, token)
            return bool(deleted)
        if self._redis.get(key) == token:
            self._redis.delete(key)
            return True
        return False

    def is_kill_switch_active(self) -> bool:
        return str(self._redis.get(KILL_SWITCH_KEY) or "0") == "1"
