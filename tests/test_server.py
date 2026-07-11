import logging
import threading

from dicomhawk.server import new_config, new_server, Server


class _FakeWorker:
    def __init__(self):
        self.shutdown_called = False

    def shutdown(self):
        self.shutdown_called = True


class _FakeAE:
    def __init__(self):
        self.workers: list[_FakeWorker] = []

    def start_server(self, *args, **kwargs):
        worker = _FakeWorker()
        self.workers.append(worker)
        return worker


def _config(ports):
    return new_config(
        "127.0.0.1", ports, "TEST", None, None,
        ["echo"], ("1.2.840.10008.1.1", ["1.2.840.10008.1.2"]),
        [], {"find": [], "move": [], "get": []},
        1, None,
    )


def test_run_populates_listeners_and_stop_shuts_down_without_double_serving(monkeypatch):
    """Regression: self.listeners used to be a bare type annotation, never assigned,
    so stop() raised AttributeError; run() also called serve_forever() a second time
    on top of start_server(block=False)'s own daemon thread, meaning it never even
    reached registering the second port. Neither self.listeners nor a second
    serve_forever() call happens here anymore."""
    fake_ae = _FakeAE()
    server = new_server(logging.getLogger("test"), _config([104, 11112]), [])
    monkeypatch.setattr(server, "init", lambda: fake_ae)

    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    thread.join(timeout=0.5)

    assert thread.is_alive()  # run() blocks until stop(), it doesn't return early
    assert len(server.listeners) == 2  # one per port, both registered

    server.stop()
    thread.join(timeout=1)

    assert not thread.is_alive()
    assert all(w.shutdown_called for w in fake_ae.workers)


def test_stop_is_safe_with_no_listeners():
    server = new_server(logging.getLogger("test"), _config([104]), [])
    server.stop()  # used to be impossible to reach without run() ever assigning listeners


def test_init_applies_configured_timeouts():
    """Regression: a raw TCP connection that never sends a valid PDU still occupies a
    max_associations slot until acse_timeout/network_timeout expire — this proves the
    tighter, profile-configurable values actually reach the real pynetdicom AE."""
    config = new_config(
        "127.0.0.1", [11112], "TEST", None, None,
        ["echo"], ("1.2.840.10008.1.1", ["1.2.840.10008.1.2"]),
        [], {"find": [], "move": [], "get": []},
        1, None,
        acse_timeout=10, network_timeout=15,
    )
    ae = Server(logging.getLogger("test"), config, []).init()
    assert ae.acse_timeout == 10
    assert ae.network_timeout == 15


def test_init_leaves_pynetdicom_defaults_when_timeouts_unset():
    ae = Server(logging.getLogger("test"), _config([11112]), []).init()
    assert ae.acse_timeout == 30      # pynetdicom's own default, untouched
    assert ae.network_timeout == 60   # pynetdicom's own default, untouched
