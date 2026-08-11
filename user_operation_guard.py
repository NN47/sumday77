"""Process-local coordination for user operations and account deletion."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
import hashlib
import threading
from typing import AsyncIterator, Iterable


class UserOperationBlocked(RuntimeError):
    """Raised when a user event must not run during/after account deletion."""


class UserWriteBlocked(RuntimeError):
    """Raised when an obsolete operation attempts to persist user data."""


@dataclass(frozen=True)
class UserOperationToken:
    user_key: bytes
    generation: int


@dataclass
class _UserState:
    generation: int = 0
    status: str = "active"  # active | deleting | deleted


_current_operation: ContextVar[UserOperationToken | None] = ContextVar(
    "sumday77_current_user_operation",
    default=None,
)


class UserOperationGuard:
    """Serializes one user's handlers and invalidates obsolete async work."""

    def __init__(self) -> None:
        self._state_lock = threading.RLock()
        self._states: dict[bytes, _UserState] = {}
        self._async_locks: dict[bytes, asyncio.Lock] = {}
        self._write_locks: dict[bytes, threading.Lock] = {}
        self._deletion_write_locks: set[bytes] = set()

    @staticmethod
    def _user_key(user_id: str | int) -> bytes:
        return hashlib.sha256(str(user_id).encode("utf-8")).digest()

    def _state_for(self, user_key: bytes) -> _UserState:
        return self._states.setdefault(user_key, _UserState())

    def _async_lock_for(self, user_key: bytes) -> asyncio.Lock:
        with self._state_lock:
            return self._async_locks.setdefault(user_key, asyncio.Lock())

    def _write_lock_for(self, user_key: bytes) -> threading.Lock:
        with self._state_lock:
            return self._write_locks.setdefault(user_key, threading.Lock())

    @asynccontextmanager
    async def operation(
        self,
        user_id: str | int,
        *,
        allow_reactivate: bool = False,
    ) -> AsyncIterator[UserOperationToken]:
        """Run one foreground/background operation for a single user."""
        user_key = self._user_key(user_id)
        existing = _current_operation.get()
        if existing is not None and existing.user_key == user_key:
            self.assert_user_write_allowed(user_id)
            yield existing
            return

        with self._state_lock:
            initial_state = self._state_for(user_key)
            initial_generation = initial_state.generation
            if initial_state.status == "deleting":
                raise UserOperationBlocked("account deletion is in progress")
            if initial_state.status == "deleted" and not allow_reactivate:
                raise UserOperationBlocked("account has been deleted")

        async with self._async_lock_for(user_key):
            with self._state_lock:
                state = self._state_for(user_key)
                if state.generation != initial_generation:
                    raise UserOperationBlocked("operation belongs to an obsolete account generation")
                if state.status == "deleting":
                    raise UserOperationBlocked("account deletion is in progress")
                if state.status == "deleted":
                    if not allow_reactivate:
                        raise UserOperationBlocked("account has been deleted")
                    state.status = "active"

                operation_token = UserOperationToken(user_key, state.generation)
                context_token: Token = _current_operation.set(operation_token)

            try:
                yield operation_token
            finally:
                _current_operation.reset(context_token)

    async def begin_deletion(self, user_id: str | int) -> None:
        """Block new writes and mark deletion while the user's handler lock is held."""
        user_key = self._user_key(user_id)
        operation = _current_operation.get()
        if operation is None or operation.user_key != user_key:
            raise RuntimeError("account deletion must run inside the user's operation context")

        write_lock = self._write_lock_for(user_key)
        await asyncio.to_thread(write_lock.acquire)
        try:
            with self._state_lock:
                state = self._state_for(user_key)
                if operation.generation != state.generation or state.status != "active":
                    raise UserOperationBlocked("account cannot enter deletion state")
                state.status = "deleting"
                self._deletion_write_locks.add(user_key)
        except Exception:
            write_lock.release()
            raise

    def complete_deletion(self, user_id: str | int) -> None:
        """Invalidate all previously started operations after the DB commit."""
        user_key = self._user_key(user_id)
        try:
            with self._state_lock:
                state = self._state_for(user_key)
                if state.status != "deleting":
                    raise RuntimeError("account is not being deleted")
                state.generation += 1
                state.status = "deleted"
        finally:
            self._release_deletion_write_lock(user_key)

    def rollback_deletion(self, user_id: str | int) -> None:
        """Restore normal operation without invalidating user state after rollback."""
        user_key = self._user_key(user_id)
        try:
            with self._state_lock:
                state = self._state_for(user_key)
                if state.status == "deleting":
                    state.status = "active"
        finally:
            self._release_deletion_write_lock(user_key)

    def _release_deletion_write_lock(self, user_key: bytes) -> None:
        with self._state_lock:
            if user_key not in self._deletion_write_locks:
                return
            self._deletion_write_locks.remove(user_key)
            write_lock = self._write_lock_for(user_key)
        write_lock.release()

    def acquire_write_permits(self, user_ids: Iterable[str | int]) -> list[threading.Lock]:
        """Hold user write locks until the surrounding DB transaction finishes."""
        user_keys = sorted({self._user_key(user_id) for user_id in user_ids})
        acquired: list[threading.Lock] = []
        try:
            for user_key in user_keys:
                write_lock = self._write_lock_for(user_key)
                write_lock.acquire()
                acquired.append(write_lock)
                self._assert_user_key_write_allowed(user_key)
            return acquired
        except Exception:
            self.release_write_permits(acquired)
            raise

    @staticmethod
    def release_write_permits(permits: Iterable[threading.Lock]) -> None:
        for permit in reversed(list(permits)):
            permit.release()

    def assert_user_write_allowed(self, user_id: str | int) -> None:
        """Reject writes by deleting/deleted accounts and stale operation contexts."""
        self._assert_user_key_write_allowed(self._user_key(user_id))

    def _assert_user_key_write_allowed(self, user_key: bytes) -> None:
        operation = _current_operation.get()
        with self._state_lock:
            state = self._state_for(user_key)
            if state.status != "active":
                raise UserWriteBlocked(f"user writes are blocked while account is {state.status}")
            if (
                operation is not None
                and operation.user_key == user_key
                and operation.generation != state.generation
            ):
                raise UserWriteBlocked("obsolete operation cannot persist user data")

    def status(self, user_id: str | int) -> str:
        with self._state_lock:
            return self._state_for(self._user_key(user_id)).status

    def reset_for_testing(self) -> None:
        """Clear process state between isolated automated tests."""
        with self._state_lock:
            if self._deletion_write_locks:
                raise RuntimeError("cannot reset while deletion write locks are held")
            self._states.clear()
            self._async_locks.clear()
            self._write_locks.clear()
        _current_operation.set(None)


user_operation_guard = UserOperationGuard()
