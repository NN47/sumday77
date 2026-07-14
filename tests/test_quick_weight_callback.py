import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("API_TOKEN", "test-token")


class DummyUser:
    def __init__(self, user_id: int):
        self.id = user_id


class DummyCallback:
    def __init__(self):
        self.from_user = DummyUser(12345)
        self.message = object()
        self.answered = False

    async def answer(self):
        self.answered = True


def test_quick_weight_uses_callback_user_id(monkeypatch):
    from handlers import common
    import handlers.weight as weight_module

    calls = []

    async def fake_start_add_weight_for_user(message, state, user_id, source=None):
        calls.append((message, state, user_id, source))

    monkeypatch.setattr(weight_module, "start_add_weight_for_user", fake_start_add_weight_for_user)

    callback = DummyCallback()
    state = object()

    asyncio.run(common.quick_weight(callback, state))

    assert callback.answered is True
    assert calls == [(callback.message, state, "12345", weight_module.WEIGHT_SOURCE_QUICK_ADD)]
