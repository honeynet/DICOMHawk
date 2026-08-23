"""The Compose deployment runs one role per container, so `--service` decides what gets built."""

import logging
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

import analysis.component as analysis_component_module
import commands.serve as serve_module
from commands.serve import serve_app

runner = CliRunner()


class _AlreadySetEvent:
    """Lets the non-DIMSE roles fall straight out of their wait instead of blocking the test."""

    def __init__(self):
        self._set = True

    def set(self) -> None:
        self._set = True

    def is_set(self) -> bool:
        return self._set

    def wait(self, timeout=None) -> bool:
        return True


class _FakeComponent:
    def __init__(self, label: str, built: list[str]):
        self.label = label
        self.store = SimpleNamespace()
        built.append(label)

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def sink(self, artifact) -> None:
        pass


@pytest.fixture
def built(monkeypatch, tmp_path):
    """Record every role-specific part serve() constructs, without starting anything real."""
    names: list[str] = []
    captured: dict = {}

    def component(label, sink_at=None):
        def factory(*args, **_kwargs):
            if sink_at is not None:
                captured[f"{label}_sink"] = args[sink_at]
            return _FakeComponent(label, names)

        return factory

    def fake_dimse_factory(_repo, _bus, _max_bytes, sink=None):
        names.append("dimse-handlers")
        captured["dimse_sink"] = sink
        return SimpleNamespace(get=lambda _op: None)

    def fake_dicomweb(_prof, _repo, _bus, _host, _proxy, sink):
        captured["dicomweb_sink"] = sink
        return _FakeComponent("dicomweb", names)

    def fake_new_dicomhawk(_srv, components):
        names.append("dimse-server")
        return SimpleNamespace(
            start=lambda: [c.start() for c in components],
            stop=lambda: [c.stop() for c in reversed(components)],
        )

    monkeypatch.setattr(
        serve_module, "threading", SimpleNamespace(Event=_AlreadySetEvent)
    )
    monkeypatch.setattr(serve_module, "new_store", lambda _t: SimpleNamespace())
    monkeypatch.setattr(
        serve_module, "new_repo", lambda _db, _store: SimpleNamespace(stop=lambda: None)
    )
    monkeypatch.setattr(serve_module, "new_server", lambda *_a, **_k: SimpleNamespace())
    monkeypatch.setattr(serve_module, "new_dicomhawk", fake_new_dicomhawk)
    monkeypatch.setattr(serve_module, "new_dimse_factory", fake_dimse_factory)
    monkeypatch.setattr(
        serve_module, "new_web_component", component("web-combined", sink_at=9)
    )
    monkeypatch.setattr(
        serve_module, "new_attacker_web_component", component("web-attacker", sink_at=9)
    )
    monkeypatch.setattr(
        serve_module, "new_operator_component", component("web-operator", sink_at=9)
    )
    monkeypatch.setattr(serve_module, "new_dicomweb_component", fake_dicomweb)
    monkeypatch.setattr(
        analysis_component_module,
        "new_analysis_component",
        component("analysis-worker"),
    )
    monkeypatch.setattr(
        analysis_component_module,
        "new_analysis_sink_component",
        component("analysis-sink"),
    )

    import fingerprint.component as fingerprint_component_module

    monkeypatch.setattr(
        fingerprint_component_module,
        "new_fingerprint_component",
        component("fingerprint"),
    )

    def run(service: str):
        names.clear()
        captured.clear()
        result = runner.invoke(
            serve_app,
            [
                "--service",
                service,
                "--profile",
                "generic-pacs",
                "-db",
                str(tmp_path / "dicomhawk.db"),
                "--analysis-db",
                str(tmp_path / "analysis.db"),
                "--fingerprint-db",
                str(tmp_path / "fingerprint.db"),
                "-t",
                str(tmp_path / "traces"),
                "-l",
                str(tmp_path / "events.log"),
                "--operator-host",
                "0.0.0.0",
                "--allow-remote-operator",
            ],
        )
        assert result.exit_code == 0, result.output
        return list(names), dict(captured)

    return run


# A wrong row means a container duplicates a worker or binds a neighbour's port.
ROLE_MATRIX = {
    "all": {
        "analysis-worker",
        "fingerprint",
        "web-combined",
        "dicomweb",
        "dimse-handlers",
        "dimse-server",
    },
    "dimse": {"analysis-sink", "dimse-handlers", "dimse-server"},
    "web": {"analysis-sink", "fingerprint", "web-attacker"},
    "operator": {"analysis-sink", "fingerprint", "web-operator"},
    "dicomweb": {"analysis-sink", "dicomweb"},
    "analysis": {"analysis-worker"},
}


@pytest.mark.parametrize("service", sorted(ROLE_MATRIX))
def test_each_role_builds_only_its_own_parts(built, service):
    names, _ = built(service)

    assert set(names) == ROLE_MATRIX[service]


@pytest.mark.parametrize(
    "service", ["dimse", "web", "dicomweb", "operator", "analysis"]
)
def test_only_the_analysis_role_runs_a_worker(built, service):
    names, _ = built(service)

    assert ("analysis-worker" in names) == (service == "analysis")
    assert ("analysis-sink" in names) == (service != "analysis")


@pytest.mark.parametrize(
    ("service", "key"),
    [
        ("dimse", "dimse_sink"),
        ("dicomweb", "dicomweb_sink"),
        ("web", "web-attacker_sink"),
    ],
)
def test_ingress_roles_receive_a_sink_that_reaches_the_shared_store(
    built, service, key
):
    _names, captured = built(service)

    assert captured[key] is not None


def test_roles_that_capture_nothing_are_wired_without_a_sink(built):
    _names, captured = built("operator")

    assert captured["web-operator_sink"] is None


def test_the_operator_role_never_serves_the_attacker_surface(built):
    names, _ = built("operator")

    assert "web-attacker" not in names
    assert "web-combined" not in names


def test_exactly_one_role_binds_the_dimse_listener(built):
    binding = [
        service for service in ROLE_MATRIX if "dimse-server" in built(service)[0]
    ]

    assert binding == ["all", "dimse"]


def test_an_unknown_role_is_refused(built, tmp_path):
    result = runner.invoke(serve_app, ["--service", "everything"])

    assert result.exit_code != 0
    assert "service must be one of" in result.output


def test_the_role_names_match_the_compose_services():
    import yaml

    from pathlib import Path

    compose = yaml.safe_load(
        (Path(__file__).parents[1] / "docker-compose.yml").read_text()
    )
    # "all" is the single-process mode used outside Docker; the rest are containers.
    assert set(compose["services"]) == set(ROLE_MATRIX) - {"all"}


def test_logs_distinguish_a_role_gated_collector_from_a_disabled_one(built, caplog):
    with caplog.at_level(logging.INFO, logger="commands.serve"):
        built("dimse")

    assert "Fingerprinting: not served by the dimse role" in caplog.text
    assert "Fingerprinting: disabled" not in caplog.text
