import ast
import logging
from pathlib import Path

import pytest

from dicomhawk.repository import new_repo
from dicomhawk.storage import new_store
from profiles.profile import load_profile
from web.component import (
    _QueueDepthFilter,
    new_attacker_web_component,
    new_operator_component,
    new_web_component,
)


def test_core_web_modules_do_not_import_optional_packages_at_module_load():
    root = Path(__file__).parents[1]
    forbidden = {"analysis", "fingerprint"}
    for relative in ("src/web/component.py", "src/commands/serve.py"):
        tree = ast.parse((root / relative).read_text())
        imports = {
            node.module.split(".", 1)[0]
            for node in tree.body
            if isinstance(node, ast.ImportFrom) and node.module
        }
        assert imports.isdisjoint(forbidden), relative


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


def test_waitress_queue_filter_keeps_only_pressure_milestones():
    now = [100.0]
    queue_filter = _QueueDepthFilter(clock=lambda: now[0])
    emitted = []
    for depth in (1, 2, 4, 5, 6, 10, 11, 24, 25, 26):
        record = logging.LogRecord(
            "waitress.queue",
            logging.WARNING,
            "",
            0,
            "Task queue depth is %d",
            (depth,),
            None,
        )
        if queue_filter.filter(record):
            emitted.append(depth)
    assert emitted == [1, 5, 10, 25]

    now[0] += 11
    record = logging.LogRecord(
        "waitress.queue",
        logging.WARNING,
        "",
        0,
        "Task queue depth is %d",
        (2,),
        None,
    )
    assert queue_filter.filter(record)


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


@pytest.mark.parametrize(
    ("factory", "expected_port"),
    [(new_attacker_web_component, 18080), (new_operator_component, 18081)],
)
def test_split_web_roles_start_exactly_one_listener(
    monkeypatch, tmp_path, factory, expected_port
):
    calls = []

    def create_server(*_args, **kwargs):
        calls.append(kwargs)
        return _FakeServer()

    monkeypatch.setattr("web.component.waitress.create_server", create_server)
    component = factory(
        load_profile("generic-pacs"),
        new_repo(None, new_store(str(tmp_path / "traces"))),
        logging.getLogger("test-split-web-component"),
        "127.0.0.1",
        18080,
        18081,
    )
    component.start()
    component.stop()

    assert len(calls) == 1
    assert calls[0]["port"] == expected_port


def test_web_component_cleans_up_if_thread_start_fails(monkeypatch, tmp_path):
    servers = [_FakeServer(), _FakeServer()]
    monkeypatch.setattr(
        "web.component.waitress.create_server", lambda *_args, **_kwargs: servers.pop(0)
    )

    class FakeThread:
        starts = 0

        def __init__(self, target, **_kwargs):
            self.target = target

        def start(self):
            FakeThread.starts += 1
            if FakeThread.starts == 2:
                raise RuntimeError("thread resources exhausted")

        def is_alive(self):
            return False

    monkeypatch.setattr("web.component.threading.Thread", FakeThread)
    component = _component(tmp_path)
    with pytest.raises(RuntimeError, match="resources exhausted"):
        component.start()
    assert component._servers == []


def test_trusted_proxy_applies_only_to_attacker_facing_server(monkeypatch, tmp_path):
    calls = []

    def create_server(*_args, **kwargs):
        calls.append(kwargs)
        return _FakeServer()

    monkeypatch.setattr("web.component.waitress.create_server", create_server)
    component = _component(tmp_path)
    component.trusted_proxy = "192.0.2.10"
    component.start()
    component.stop()

    assert calls[0]["trusted_proxy"] == "192.0.2.10"
    assert calls[0]["trusted_proxy_headers"] == {
        "x-forwarded-for",
        "x-forwarded-host",
        "x-forwarded-port",
        "x-forwarded-proto",
    }
    assert "trusted_proxy" not in calls[1]


def test_waitress_allows_the_route_specific_upload_body_limit(monkeypatch, tmp_path):
    calls = []

    def create_server(*_args, **kwargs):
        calls.append(kwargs)
        return _FakeServer()

    monkeypatch.setattr("web.component.waitress.create_server", create_server)
    component = new_web_component(
        load_profile("generic-pacs"),
        new_repo(None, new_store(str(tmp_path / "traces"))),
        logging.getLogger("test-web-upload-listener-limit"),
        "127.0.0.1",
        18080,
        18081,
    )
    component.start()
    component.stop()

    assert calls[0]["max_request_body_size"] == 50 * 1024 * 1024
    assert calls[1]["max_request_body_size"] == 1024 * 1024


# --- shutdown race: stop() closes the socket asyncore is polling ---


def _run_serve(run, stopping_set):
    """Drive _serve directly; the real race is too narrow to trigger on demand."""
    import threading

    from web.component import _serve

    class _Server:
        def run(self):
            run()

    stopping = threading.Event()
    if stopping_set:
        stopping.set()
    _serve("dicomweb-9080", _Server(), stopping)


def test_serve_absorbs_the_bad_file_descriptor_raised_by_stop():
    import errno

    def closed_mid_select():
        raise OSError(errno.EBADF, "Bad file descriptor")

    # Must not escape as an unhandled thread exception and print a traceback.
    _run_serve(closed_mid_select, stopping_set=True)


def test_serve_still_raises_a_bad_file_descriptor_outside_shutdown():
    import errno

    def closed_unexpectedly():
        raise OSError(errno.EBADF, "Bad file descriptor")

    # The same error while running is a real failure and must not be swallowed.
    with pytest.raises(OSError):
        _run_serve(closed_unexpectedly, stopping_set=False)


def test_serve_still_raises_other_errors_during_shutdown():
    import errno

    def out_of_memory():
        raise OSError(errno.ENOMEM, "Cannot allocate memory")

    with pytest.raises(OSError):
        _run_serve(out_of_memory, stopping_set=True)


def test_listener_threads_survive_a_real_start_stop_cycle(tmp_path):
    import threading

    caught = []
    previous = threading.excepthook
    threading.excepthook = lambda args: caught.append(args.exc_type.__name__)
    try:
        repo = new_repo(None, new_store(str(tmp_path / "traces")))
        repo.start()
        component = new_web_component(
            load_profile("generic-pacs"),
            repo,
            logging.getLogger("bus"),
            "127.0.0.1",
            0,
            0,
        )
        component.start()
        component.stop()
        repo.stop()
    finally:
        threading.excepthook = previous

    assert caught == []
