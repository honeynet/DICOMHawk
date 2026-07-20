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
        "127.0.0.1",
        ports,
        "TEST",
        None,
        None,
        ["echo"],
        ("1.2.840.10008.1.1", ["1.2.840.10008.1.2"]),
        [],
        {"find": [], "move": [], "get": []},
        1,
        None,
    )


def test_run_populates_listeners_and_stop_shuts_down_without_double_serving(
    monkeypatch,
):
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
    config = new_config(
        "127.0.0.1",
        [11112],
        "TEST",
        None,
        None,
        ["echo"],
        ("1.2.840.10008.1.1", ["1.2.840.10008.1.2"]),
        [],
        {"find": [], "move": [], "get": []},
        1,
        None,
        acse_timeout=10,
        network_timeout=15,
        dimse_timeout=20,
    )
    ae = Server(logging.getLogger("test"), config, []).init()
    assert ae.acse_timeout == 10
    assert ae.network_timeout == 15
    assert ae.dimse_timeout == 20


def test_init_leaves_pynetdicom_defaults_when_timeouts_unset():
    ae = Server(logging.getLogger("test"), _config([11112]), []).init()
    assert ae.acse_timeout == 30  # pynetdicom's own default, untouched
    assert ae.network_timeout == 60  # pynetdicom's own default, untouched
    assert ae.dimse_timeout == 30  # pynetdicom's own default, untouched


def test_get_only_profile_advertises_storage_as_scu():
    from profiles.profile import default_profile

    profile = default_profile()
    config = new_config(
        "127.0.0.1",
        [11112],
        "TEST",
        None,
        None,
        ["get"],
        profile.dicom.verification,
        profile.dicom.storage_classes[:1],
        profile.dicom.qr_classes,
        1,
        None,
    )
    ae = Server(logging.getLogger("test"), config, []).init()
    storage = next(
        context
        for context in ae.supported_contexts
        if context.abstract_syntax == profile.dicom.storage_classes[0][0]
    )
    assert storage.scu_role is True
    assert storage.scp_role is False


def test_second_listener_bind_failure_closes_first(monkeypatch):
    class _FailingAE(_FakeAE):
        def start_server(self, *args, **kwargs):
            if self.workers:
                raise OSError("address already in use")
            return super().start_server(*args, **kwargs)

    fake_ae = _FailingAE()
    server = new_server(logging.getLogger("test"), _config([104, 11112]), [])
    monkeypatch.setattr(server, "init", lambda: fake_ae)

    import pytest

    with pytest.raises(OSError, match="address already in use"):
        server.run()
    assert fake_ae.workers[0].shutdown_called
    assert server.listeners == []


def test_stop_racing_with_listener_start_closes_new_worker(monkeypatch):
    server = new_server(logging.getLogger("test"), _config([104]), [])

    class _RacingAE(_FakeAE):
        def start_server(self, *args, **kwargs):
            worker = super().start_server(*args, **kwargs)
            server.stop()
            return worker

    fake_ae = _RacingAE()
    monkeypatch.setattr(server, "init", lambda: fake_ae)
    server.run()
    assert fake_ae.workers[0].shutdown_called
    assert server.listeners == []
