import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

import main


def test_normal_start_acquires_lock_without_standby(monkeypatch, caplog):
    lock_connection = Mock()
    sleep = Mock()
    monkeypatch.setattr(main, "acquire_polling_lock", lambda: lock_connection)
    monkeypatch.setattr(main.asyncio, "sleep", sleep)

    with caplog.at_level("INFO"):
        result = asyncio.run(main.wait_for_polling_lock())

    assert result is lock_connection
    sleep.assert_not_called()
    assert "Попытка получить polling lock" in caplog.text
    assert "Polling lock получен" in caplog.text
    assert "standby" not in caplog.text


def test_standby_retries_and_becomes_active(monkeypatch, caplog):
    lock_connection = Mock()
    attempts = iter((None, None, lock_connection))
    sleeps = []

    monkeypatch.setattr(main, "acquire_polling_lock", lambda: next(attempts))

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(main.asyncio, "sleep", fake_sleep)

    with caplog.at_level("INFO"):
        result = asyncio.run(main.wait_for_polling_lock())

    assert result is lock_connection
    assert sleeps == [main.POLLING_LOCK_RETRY_SECONDS] * 2
    assert "Polling lock занят, standby" in caplog.text
    assert "Повторная попытка получить polling lock" in caplog.text
    assert "Переход standby → active" in caplog.text


def test_failed_advisory_lock_closes_session(monkeypatch):
    connection = Mock()
    connection.closed = False
    connection.execute.return_value.scalar.return_value = False
    test_engine = Mock()
    test_engine.url.get_backend_name.return_value = "postgresql"
    test_engine.connect.return_value = connection
    monkeypatch.setattr(main, "engine", test_engine)

    assert main.acquire_polling_lock() is None
    connection.close.assert_called_once_with()


def test_acquired_advisory_lock_commits_implicit_transaction(monkeypatch):
    connection = Mock()
    connection.execute.return_value.scalar.return_value = True
    test_engine = Mock()
    test_engine.url.get_backend_name.return_value = "postgresql"
    test_engine.connect.return_value = connection
    monkeypatch.setattr(main, "engine", test_engine)

    assert main.acquire_polling_lock() is connection
    connection.commit.assert_called_once_with()
    connection.close.assert_not_called()


def test_release_unlocks_before_closing(monkeypatch):
    connection = Mock()
    connection.closed = False
    connection.invalidated = False
    connection.connection.driver_connection.closed = False

    main.release_polling_lock_safely(connection)
    main.close_connection_safely(connection)

    sql = str(connection.execute.call_args.args[0])
    assert "pg_advisory_unlock" in sql
    connection.close.assert_called_once_with()


def test_database_initialization_runs_after_postgresql_polling_lock(monkeypatch):
    events = []
    lock_connection = Mock()
    test_engine = Mock()
    test_engine.url.get_backend_name.return_value = "postgresql"

    async def wait_for_lock():
        events.append("lock")
        return lock_connection

    def initialize_database():
        events.append("init_db")

    monkeypatch.setattr(main, "engine", test_engine)
    monkeypatch.setattr(main, "wait_for_polling_lock", wait_for_lock)
    monkeypatch.setattr(main, "init_db", initialize_database)

    result = asyncio.run(main.initialize_database_for_active_instance())

    assert result is lock_connection
    assert events == ["lock", "init_db"]


def test_database_initialization_skips_advisory_lock_for_sqlite(monkeypatch):
    test_engine = Mock()
    test_engine.url.get_backend_name.return_value = "sqlite"
    wait_for_lock = AsyncMock()
    initialize_database = Mock()
    monkeypatch.setattr(main, "engine", test_engine)
    monkeypatch.setattr(main, "wait_for_polling_lock", wait_for_lock)
    monkeypatch.setattr(main, "init_db", initialize_database)

    result = asyncio.run(main.initialize_database_for_active_instance())

    assert result is None
    wait_for_lock.assert_not_awaited()
    initialize_database.assert_called_once_with()


def test_database_initialization_failure_releases_polling_lock(monkeypatch):
    lock_connection = Mock()
    test_engine = Mock()
    test_engine.url.get_backend_name.return_value = "postgresql"
    release_lock = Mock()
    close_connection = Mock()
    monkeypatch.setattr(main, "engine", test_engine)
    monkeypatch.setattr(
        main,
        "wait_for_polling_lock",
        AsyncMock(return_value=lock_connection),
    )
    monkeypatch.setattr(main, "init_db", Mock(side_effect=RuntimeError("boom")))
    monkeypatch.setattr(main, "release_polling_lock_safely", release_lock)
    monkeypatch.setattr(main, "close_connection_safely", close_connection)

    with pytest.raises(RuntimeError, match="boom"):
        asyncio.run(main.initialize_database_for_active_instance())

    release_lock.assert_called_once_with(lock_connection)
    close_connection.assert_called_once_with(lock_connection)
