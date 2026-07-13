import logging

import pytest

from dicomhawk.repository import new_repo
from dicomhawk.storage import new_store
from profiles.profile import load_profile
from web.component import new_web_component


class _Dispatcher:
    def __init__(self):
        self.shutdown_called = False

    def shutdown(self, **_kwargs):
        self.shutdown_called = True


class _FakeServer:
    def __init__(self):
        self.closed = False
        self.ran = False
        self.task_dispatcher = _Dispatcher()

    def run(self):
        self.ran = True

    def close(self):
        self.closed = True


def _component(tmp_path):
    return new_web_component(
        load_profile("fujifilm"),
        new_repo(None, new_store(str(tmp_path / "traces"))),
        logging.getLogger("test-web-component"),
        "127.0.0.1",
        18080,
        18081,
    )


def test_web_component_surfaces_bind_failure_and_closes_first(monkeypatch, tmp_path):
    first = _FakeServer()
    calls = 0

    def create_server(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("address already in use")
        return first

    monkeypatch.setattr("web.component.waitress.create_server", create_server)
    component = _component(tmp_path)

    with pytest.raises(OSError, match="address already in use"):
        component.start()
    assert first.closed
    assert first.task_dispatcher.shutdown_called
    assert component._servers == []


def test_web_component_stop_closes_both_servers(monkeypatch, tmp_path):
    servers = [_FakeServer(), _FakeServer()]
    monkeypatch.setattr(
        "web.component.waitress.create_server", lambda *_args, **_kwargs: servers.pop(0)
    )
    component = _component(tmp_path)
    component.start()
    active = list(component._servers)
    component.stop()

    assert all(server.ran for server in active)
    assert all(server.closed for server in active)
    assert all(server.task_dispatcher.shutdown_called for server in active)
    component.stop()  # idempotent
