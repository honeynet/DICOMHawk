import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).parents[1]
SCRIPT = REPO / "setup.sh"

# Records every invocation, then answers the handful of queries the script actually reads.
FAKE_DOCKER = r"""#!/bin/sh
echo "$*" >> "$DICOMHAWK_TEST_LOG"
case "$*" in
  "info") [ "${DICOMHAWK_TEST_DAEMON_DOWN:-0}" = 1 ] && exit 1; exit 0 ;;
  "compose version --short") echo "${DICOMHAWK_TEST_COMPOSE_VERSION:-2.29.1}"; exit 0 ;;
  "compose ps --format {{.Health}} dicomhawk") echo "${DICOMHAWK_TEST_HEALTH:-healthy}"; exit 0 ;;
  *"dicomhawk seed"*) exit "${DICOMHAWK_TEST_SEED_EXIT:-0}" ;;
esac
exit 0
"""

# No listening sockets, so the busy-port warning never fires and stderr stays predictable.
FAKE_SS = "#!/bin/sh\nexit 0\n"

# Never let a test reach real sudo; docker passes through so daemon state still shows.
FAKE_SUDO = (
    "#!/bin/sh\n"
    'echo "sudo $*" >> "$DICOMHAWK_TEST_LOG"\n'
    'case "$1" in docker) shift; exec docker "$@" ;; esac\n'
    "exit 0\n"
)

# --defaults must never reach the interface; failing loudly beats a screen nobody can answer.
FAKE_WHIPTAIL = "#!/bin/sh\n" 'echo "WHIPTAIL $*" >> "$DICOMHAWK_TEST_LOG"\n' "exit 1\n"

# Cleared per case: a value exported in the developer's shell would silently change assertions.
SEEDED_ANSWERS = (
    "DICOMHAWK_PROFILE",
    "DICOMHAWK_AE_TITLE",
    "DICOMHAWK_PORTS",
    "DICOMHAWK_WEB_PORT",
    "DICOMHAWK_OPERATOR_PORT",
    "DICOMHAWK_OPERATOR_TOKEN",
    "DICOMHAWK_BACKEND_SERVER",
    "DICOMHAWK_PUBLIC_BASE_URL",
    "DICOMHAWK_TRUSTED_PROXY",
    "DICOMHAWK_SECURE_COOKIES",
    "DICOMHAWK_ANALYSIS",
    "DICOMHAWK_FINGERPRINT",
)


def _harness(tmp_path, **extra_env):
    """Copy the script into a scratch tree with fake docker/ss on PATH."""
    root = tmp_path / "repo"
    (root / "src" / "profiles" / "fujifilm").mkdir(parents=True)
    (root / "src" / "profiles" / "fujifilm" / "fujifilm.yaml").write_text("")
    shutil.copy(SCRIPT, root / "setup.sh")
    shutil.copy(REPO / ".env.example", root / ".env.example")

    bindir = tmp_path / "bin"
    bindir.mkdir()
    # sudo is faked unconditionally: no test may ever reach the real one and prompt on the host.
    fakes = (
        ("docker", FAKE_DOCKER),
        ("ss", FAKE_SS),
        ("sudo", FAKE_SUDO),
        ("whiptail", FAKE_WHIPTAIL),
    )
    for name, body in fakes:
        (bindir / name).write_text(body)
        (bindir / name).chmod(0o755)

    log = tmp_path / "docker.log"
    log.write_text("")

    env = {key: value for key, value in os.environ.items() if key not in SEEDED_ANSWERS}
    env["PATH"] = f"{bindir}:{env['PATH']}"
    env["DICOMHAWK_TEST_LOG"] = str(log)
    env["DICOMHAWK_HEALTH_TIMEOUT"] = "1"
    env.update(extra_env)
    return root, env, log


def _run(root, env, *args):
    return subprocess.run(
        [str(root / "setup.sh"), *args], env=env, capture_output=True, text=True
    )


def _env_values(path):
    values = {}
    for line in path.read_text().splitlines():
        if line.startswith("DICOMHAWK_") and "=" in line:
            key, _, value = line.partition("=")
            values[key] = value
    return values


def _seed_args(log):
    return next(c for c in log.read_text().splitlines() if "dicomhawk seed" in c)


def test_the_repo_is_resolved_from_the_script_not_the_working_directory(tmp_path):
    # Regression: resolving via dirname meant a PATH without it wrote .env into the caller's cwd.
    root, env, _log = _harness(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    result = subprocess.run(
        [str(root / "setup.sh"), "--defaults", "--no-start"],
        cwd=elsewhere,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert (root / ".env").exists()
    assert not (elsewhere / ".env").exists()


def test_defaults_never_launches_the_interface(tmp_path):
    # Regression: an unguarded step opened a screen, failed with no terminal, and read as Back.
    root, env, log = _harness(tmp_path)
    assert _run(root, env, "--defaults", "--no-start").returncode == 0
    assert "WHIPTAIL" not in log.read_text()


def test_defaults_writes_a_complete_env_file(tmp_path):
    root, env, _log = _harness(tmp_path)
    assert _run(root, env, "--defaults", "--no-start").returncode == 0

    written = _env_values(root / ".env")
    example = _env_values(root / ".env.example")
    # Every variable the example documents must survive, or a deployment silently loses a setting.
    assert set(example).issubset(written)
    assert written["DICOMHAWK_PORTS"] == "104"
    assert written["DICOMHAWK_TRACES"] == "/opt/dicomhawk/storage"


def test_defaults_agree_with_the_examples_own_values(tmp_path):
    # Drift would make --defaults disagree with the file every doc points operators at.
    root, env, _log = _harness(tmp_path)
    _run(root, env, "--defaults", "--no-start")
    assert _env_values(root / ".env") == _env_values(root / ".env.example")


def test_defaults_preserves_the_examples_comments(tmp_path):
    # The generated file is the operator's reference; stripping the comments would lose it.
    root, env, _log = _harness(tmp_path)
    _run(root, env, "--defaults", "--no-start")
    assert "# DICOMHawk runtime configuration." in (root / ".env").read_text()


def test_env_file_is_not_world_readable(tmp_path):
    # It holds the operator token.
    root, env, _log = _harness(tmp_path)
    _run(root, env, "--defaults", "--no-start")
    assert (root / ".env").stat().st_mode & 0o077 == 0


def test_answers_are_seeded_from_the_environment(tmp_path):
    root, env, _log = _harness(
        tmp_path,
        DICOMHAWK_PROFILE="fujifilm",
        DICOMHAWK_AE_TITLE="SYNAPSE",
        DICOMHAWK_OPERATOR_TOKEN="t0ken",
    )
    assert _run(root, env, "--defaults", "--no-start").returncode == 0

    written = _env_values(root / ".env")
    assert written["DICOMHAWK_PROFILE"] == "fujifilm"
    assert written["DICOMHAWK_AE_TITLE"] == "SYNAPSE"
    assert written["DICOMHAWK_OPERATOR_TOKEN"] == "t0ken"


def test_an_unanswered_optional_variable_stays_commented(tmp_path):
    # Uncommenting it empty would override the profile's own origin with nothing.
    root, env, _log = _harness(tmp_path)
    _run(root, env, "--defaults", "--no-start")
    assert "DICOMHAWK_PUBLIC_BASE_URL" not in _env_values(root / ".env")


def test_an_answered_optional_variable_is_uncommented(tmp_path):
    root, env, _log = _harness(
        tmp_path, DICOMHAWK_PUBLIC_BASE_URL="https://pacs.example.org"
    )
    _run(root, env, "--defaults", "--no-start")
    written = _env_values(root / ".env")
    assert written["DICOMHAWK_PUBLIC_BASE_URL"] == "https://pacs.example.org"


def test_default_ports_write_no_override(tmp_path):
    root, env, _log = _harness(tmp_path)
    _run(root, env, "--defaults", "--no-start")
    assert not (root / "docker-compose.override.yml").exists()


def test_custom_ports_override_rather_than_append(tmp_path):
    # Compose appends ports lists, so without !override the base 104 stays published and unserved.
    root, env, _log = _harness(tmp_path, DICOMHAWK_PORTS="11112,2762")
    _run(root, env, "--defaults", "--no-start")

    override = (root / "docker-compose.override.yml").read_text()
    assert "ports: !override" in override
    assert '- "11112:11112"' in override
    assert '- "2762:2762"' in override
    # Match the mapping, not the prose: the file's own comment mentions 104:104.
    assert '- "104:104"' not in override
    # The operator API must stay bound to host loopback whatever port it moves to.
    assert '- "127.0.0.1:8081:8081"' in override


def test_override_keeps_publishing_every_profiles_dicomweb_ports(tmp_path):
    # Only the active profile listens, but which ports exist is part of the device fingerprint.
    root, env, _log = _harness(tmp_path, DICOMHAWK_PORTS="11112")
    _run(root, env, "--defaults", "--no-start")

    override = (root / "docker-compose.override.yml").read_text()
    for port in (8042, 9080, 10080, 12080, 13080):
        assert f'- "{port}:{port}"' in override


def test_a_stale_override_is_removed_when_ports_return_to_default(tmp_path):
    root, env, _log = _harness(tmp_path, DICOMHAWK_PORTS="11112")
    _run(root, env, "--defaults", "--no-start")
    assert (root / "docker-compose.override.yml").exists()

    # Explicitly, because a reconfigure now keeps any answer it is not given a new value for.
    env["DICOMHAWK_PORTS"] = "104"
    _run(root, env, "--defaults", "--no-start", "--reconfigure")
    # Left behind, it would keep publishing the previous run's ports.
    assert not (root / "docker-compose.override.yml").exists()


def test_an_unowned_compose_override_is_never_changed(tmp_path):
    root, env, _log = _harness(tmp_path, DICOMHAWK_PORTS="11112")
    override = root / "docker-compose.override.yml"
    original = "services:\n  dicomhawk:\n    volumes: [custom:/data]\n"
    override.write_text(original)

    result = _run(root, env, "--defaults", "--no-start")

    assert result.returncode != 0
    assert "refusing" in result.stderr
    assert override.read_text() == original
    assert not (root / ".env").exists()


def test_generated_override_is_written_atomically(tmp_path):
    assert ".compose.tmp." in SCRIPT.read_text()
    assert '} >"$tmp"\n    mv "$tmp" "$OVERRIDE_FILE"' in SCRIPT.read_text()


def test_a_custom_port_survives_a_reconfigure_that_does_not_mention_it(tmp_path):
    root, env, _log = _harness(tmp_path, DICOMHAWK_PORTS="11112")
    _run(root, env, "--defaults", "--no-start")

    del env["DICOMHAWK_PORTS"]
    _run(root, env, "--defaults", "--no-start", "--reconfigure")
    assert _env_values(root / ".env")["DICOMHAWK_PORTS"] == "11112"
    assert (root / "docker-compose.override.yml").exists()


def test_existing_env_is_not_silently_overwritten(tmp_path):
    root, env, _log = _harness(tmp_path)
    _run(root, env, "--defaults", "--no-start")

    result = _run(root, env, "--defaults", "--no-start")
    assert result.returncode != 0
    assert "--reconfigure" in result.stderr


def test_keep_existing_configuration_does_not_reseed():
    source = SCRIPT.read_text()
    assert "keep) KEEP_EXISTING=1; DO_SEED=0" in source


def test_guided_installer_does_not_offer_an_unmounted_custom_profile():
    step = (
        SCRIPT.read_text().split("step_profile()", 1)[1].split("step_ae_title()", 1)[0]
    )
    assert '"custom"' not in step


def test_reconfigure_overwrites_an_existing_env(tmp_path):
    root, env, _log = _harness(tmp_path)
    _run(root, env, "--defaults", "--no-start")

    env["DICOMHAWK_AE_TITLE"] = "SECOND"
    assert _run(root, env, "--defaults", "--no-start", "--reconfigure").returncode == 0
    assert _env_values(root / ".env")["DICOMHAWK_AE_TITLE"] == "SECOND"


def test_old_compose_is_refused_by_version(tmp_path):
    # 2.17 predates the !override tag; generating the file anyway would publish both ports.
    root, env, _log = _harness(tmp_path, DICOMHAWK_TEST_COMPOSE_VERSION="2.17.3")
    result = _run(root, env, "--defaults", "--no-start")
    assert result.returncode != 0
    assert "2.24" in result.stderr
    assert not (root / ".env").exists()


def test_a_newer_compose_major_is_accepted(tmp_path):
    # Regression: a plain string compare reads 5.1.3 as lower than 2.24.
    root, env, _log = _harness(tmp_path, DICOMHAWK_TEST_COMPOSE_VERSION="5.1.3")
    assert _run(root, env, "--defaults", "--no-start").returncode == 0


def test_the_minimum_compose_version_itself_is_accepted(tmp_path):
    root, env, _log = _harness(tmp_path, DICOMHAWK_TEST_COMPOSE_VERSION="2.24")
    assert _run(root, env, "--defaults", "--no-start").returncode == 0


def test_unreachable_daemon_is_reported_before_anything_is_written(tmp_path):
    root, env, _log = _harness(tmp_path, DICOMHAWK_TEST_DAEMON_DOWN="1")
    result = _run(root, env, "--defaults", "--no-start")
    assert result.returncode != 0
    assert "daemon" in result.stderr.lower()
    assert not (root / ".env").exists()


def _isolate_path(env, *extra):
    """Cut PATH down to the fakes plus named tools, so absence is real and not just shadowed."""
    bindir = Path(env["PATH"].split(os.pathsep)[0])
    # Cases that isolate PATH are the ones testing an absent whiptail, so drop the fake.
    (bindir / "whiptail").unlink(missing_ok=True)
    tools = (
        "bash",
        "sort",
        "head",
        "sed",
        "grep",
        "mktemp",
        "cat",
        "rm",
        "mv",
        "chmod",
        *extra,
    )
    for tool in tools:
        target = bindir / tool
        if not target.exists():
            target.symlink_to(shutil.which(tool))
    env["PATH"] = str(bindir)
    return bindir


def _fake_sudo(bindir, on_docker_ce):
    """Override the default fake so installing docker-ce has a visible side effect."""
    (bindir / "sudo").write_text(
        "#!/bin/sh\n"
        'echo "sudo $*" >> "$DICOMHAWK_TEST_LOG"\n'
        f'case "$*" in *docker-ce*) {on_docker_ce} ;; esac\n'
        "exit 0\n"
    )
    (bindir / "sudo").chmod(0o755)


def test_an_absent_whiptail_is_installed_before_the_questions(tmp_path):
    root, env, log = _harness(tmp_path)
    _isolate_path(env, "apt-get")

    result = _run(root, env)
    assert "apt-get install -y whiptail" in log.read_text()
    # The fake cannot really provide whiptail, so the run still stops rather than asking nothing.
    assert result.returncode != 0
    assert "whiptail" in result.stderr
    assert not (root / ".env").exists()


def test_no_install_refuses_instead_of_touching_the_host(tmp_path):
    root, env, log = _harness(tmp_path)
    _isolate_path(env)

    result = _run(root, env, "--no-install")
    assert result.returncode != 0
    assert "whiptail" in result.stderr and "--no-install" in result.stderr
    assert "apt-get" not in log.read_text()


def test_a_current_docker_is_never_reinstalled(tmp_path):
    root, env, log = _harness(tmp_path)
    assert _run(root, env, "--defaults", "--no-start").returncode == 0
    assert "docker-ce" not in log.read_text()


def test_an_absent_docker_is_installed_from_dockers_own_repository(tmp_path):
    root, env, log = _harness(tmp_path)
    bindir = _isolate_path(env, "dpkg", "apt-get", "cp")

    # Move docker aside so the script has to install it, and let the fake sudo put it back.
    staged = tmp_path / "docker-to-be-installed"
    shutil.move(str(bindir / "docker"), staged)
    _fake_sudo(
        bindir,
        'cp "$DICOMHAWK_TEST_DOCKER_SRC" "$DICOMHAWK_TEST_BIN/docker";'
        ' chmod 755 "$DICOMHAWK_TEST_BIN/docker"',
    )
    env["DICOMHAWK_TEST_DOCKER_SRC"] = str(staged)
    env["DICOMHAWK_TEST_BIN"] = str(bindir)

    assert _run(root, env, "--defaults", "--no-start").returncode == 0

    calls = log.read_text()
    assert "download.docker.com" in calls
    assert "docker-ce" in calls and "docker-compose-plugin" in calls
    # Otherwise every later docker command an operator types needs sudo.
    assert "usermod -aG docker" in calls
    assert (root / ".env").exists()


def test_an_old_compose_plugin_is_upgraded_rather_than_refused(tmp_path):
    # 2.17 is present but predates !override, so it counts as missing, not as a hard stop.
    root, env, log = _harness(tmp_path, DICOMHAWK_TEST_COMPOSE_VERSION="2.17.3")
    _isolate_path(env, "dpkg", "apt-get")

    result = _run(root, env, "--defaults", "--no-start")
    assert "docker-compose-plugin" in log.read_text()
    # The fake cannot really upgrade itself, so the run still stops at the version gate.
    assert result.returncode != 0
    assert "2.24" in result.stderr


def test_full_run_builds_starts_and_seeds_in_order(tmp_path):
    root, env, log = _harness(tmp_path)
    assert _run(root, env, "--defaults").returncode == 0

    calls = log.read_text().splitlines()
    build = next(i for i, c in enumerate(calls) if c == "compose build")
    up = next(i for i, c in enumerate(calls) if c == "compose up -d")
    seed = next(i for i, c in enumerate(calls) if "dicomhawk seed" in c)
    assert build < up < seed


def test_seed_carries_the_chosen_collection_and_modality(tmp_path):
    root, env, log = _harness(tmp_path)
    _run(root, env, "--defaults")

    seed = _seed_args(log)
    assert "--collection TCGA-LUAD" in seed
    assert "--modality CT" in seed
    assert "--max-images 30" in seed


def test_seed_omits_optional_flags_that_were_left_blank(tmp_path):
    # An empty --osm-city would resolve to no country and silently drop the built-in list.
    root, env, log = _harness(tmp_path)
    _run(root, env, "--defaults")

    seed = _seed_args(log)
    assert "--osm-city" not in seed
    assert "--honey-url" not in seed


def test_the_seed_plan_is_announced_before_the_silent_download(tmp_path):
    # The first live run looked hung here: seeding prints nothing for minutes on end.
    root, env, _log = _harness(tmp_path)
    result = _run(root, env, "--defaults")

    assert "TCGA-LUAD" in result.stdout
    assert "several minutes" in result.stdout


def test_no_seed_still_builds_and_starts(tmp_path):
    root, env, log = _harness(tmp_path)
    assert _run(root, env, "--defaults", "--no-seed").returncode == 0

    calls = log.read_text()
    assert "compose up -d" in calls
    assert "dicomhawk seed" not in calls


def test_no_start_touches_nothing(tmp_path):
    root, env, log = _harness(tmp_path)
    _run(root, env, "--defaults", "--no-start")

    calls = log.read_text()
    assert "compose build" not in calls
    assert "compose up -d" not in calls


def test_a_container_that_never_becomes_healthy_fails_and_dumps_logs(tmp_path):
    root, env, log = _harness(tmp_path, DICOMHAWK_TEST_HEALTH="starting")
    result = _run(root, env, "--defaults")
    assert result.returncode != 0
    assert "compose logs --tail=50 dicomhawk" in log.read_text()


def test_an_unhealthy_container_fails_without_waiting_out_the_timeout(tmp_path):
    root, env, log = _harness(tmp_path, DICOMHAWK_TEST_HEALTH="unhealthy")
    result = _run(root, env, "--defaults")
    assert result.returncode != 0
    assert "compose logs --tail=50 dicomhawk" in log.read_text()


def test_a_failed_seed_leaves_the_honeypot_running(tmp_path):
    # TCIA being unreachable is ordinary; it must not fail an otherwise complete install.
    root, env, _log = _harness(tmp_path, DICOMHAWK_TEST_SEED_EXIT="1")
    result = _run(root, env, "--defaults")
    assert result.returncode == 0
    assert "seed" in result.stderr.lower()


def test_the_republished_dicomweb_ports_match_the_compose_file():
    # The override replaces the whole list, so a port added to compose but not here stops being published.
    import yaml

    compose = yaml.safe_load((REPO / "docker-compose.yml").read_text())
    # Last field is the container port; the operator entry also carries a 127.0.0.1 host_ip.
    published = {
        int(str(entry).split(":")[-1])
        for entry in compose["services"]["dicomhawk"]["ports"]
    }
    # Everything the script does not rebuild from an answer must be carried over verbatim.
    carried = published - {104, 8080, 8081}

    declared = SCRIPT.read_text().split("DICOMWEB_PUBLISHED=(")[1].split(")")[0]
    assert {int(port) for port in declared.split()} == carried


def test_every_variable_written_is_actually_consumed(tmp_path):
    # A knob nothing reads is worse than no knob; mechanised instead of left to a pre-commit grep.
    root, env, _log = _harness(tmp_path)
    _run(root, env, "--defaults", "--no-start")

    haystack = "\n".join(
        path.read_text()
        for path in [
            *(REPO / "src").rglob("*.py"),
            *(REPO / "deploy").glob("*"),
            REPO / "docker-compose.yml",
        ]
        if path.is_file()
    )
    for key in _env_values(root / ".env"):
        assert key in haystack, f"{key} is written to .env but nothing reads it"


def test_the_analysis_and_fingerprint_switches_are_readable_from_the_environment(
    tmp_path,
):
    # Without these, disabling either under Docker means editing the tracked compose command list.
    import click
    import typer

    from commands.serve import serve_app

    command = typer.main.get_command(serve_app)
    context = click.Context(command)
    for name, envvar in (
        ("analysis", "DICOMHAWK_ANALYSIS"),
        ("fingerprint", "DICOMHAWK_FINGERPRINT"),
    ):
        param = next(p for p in command.params if p.name == name)
        assert param.envvar == envvar
        assert param.type_cast_value(context, "false") is False
        assert param.type_cast_value(context, "true") is True


def test_unknown_option_is_rejected(tmp_path):
    root, env, _log = _harness(tmp_path)
    result = _run(root, env, "--wat")
    assert result.returncode != 0
    assert "Unknown option" in result.stderr


def test_reconfigure_keeps_a_token_it_did_not_ask_about(tmp_path):
    # A --defaults reconfigure silently wiped a generated operator token, leaving the API open.
    root, env, _log = _harness(tmp_path, DICOMHAWK_OPERATOR_TOKEN="keep-me")
    _run(root, env, "--defaults", "--no-start")
    assert _env_values(root / ".env")["DICOMHAWK_OPERATOR_TOKEN"] == "keep-me"

    del env["DICOMHAWK_OPERATOR_TOKEN"]
    _run(root, env, "--defaults", "--no-start", "--reconfigure")
    assert _env_values(root / ".env")["DICOMHAWK_OPERATOR_TOKEN"] == "keep-me"


def test_an_exported_value_still_beats_the_saved_one(tmp_path):
    root, env, _log = _harness(tmp_path, DICOMHAWK_AE_TITLE="FIRST")
    _run(root, env, "--defaults", "--no-start")

    env["DICOMHAWK_AE_TITLE"] = "SECOND"
    _run(root, env, "--defaults", "--no-start", "--reconfigure")
    assert _env_values(root / ".env")["DICOMHAWK_AE_TITLE"] == "SECOND"


def test_a_non_numeric_port_is_refused_before_anything_is_written(tmp_path):
    # Otherwise it reaches .env and surfaces minutes later as an opaque `compose up` failure.
    root, env, _log = _harness(tmp_path, DICOMHAWK_PORTS="abc")
    result = _run(root, env, "--defaults", "--no-start")
    assert result.returncode != 0
    assert not (root / ".env").exists()


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("DICOMHAWK_PROFILE", "/host/custom.yaml"),
        ("DICOMHAWK_AE_TITLE", "THIS-AE-TITLE-IS-TOO-LONG"),
        ("DICOMHAWK_ANALYSIS", "perhaps"),
        ("DICOMHAWK_FINGERPRINT", "1"),
        ("DICOMHAWK_PUBLIC_BASE_URL", "not a url"),
        ("DICOMHAWK_TRUSTED_PROXY", "999.1.1.1"),
    ],
)
def test_invalid_exported_answers_are_refused_before_writing(tmp_path, name, value):
    root, env, _log = _harness(tmp_path, **{name: value})
    result = _run(root, env, "--defaults", "--no-start")
    assert result.returncode != 0
    assert not (root / ".env").exists()


def test_an_out_of_range_port_is_refused(tmp_path):
    root, env, _log = _harness(tmp_path, DICOMHAWK_WEB_PORT="70000")
    assert _run(root, env, "--defaults", "--no-start").returncode != 0


def test_a_repeated_port_within_the_dimse_list_is_refused(tmp_path):
    root, env, _log = _harness(tmp_path, DICOMHAWK_PORTS="104,104")
    assert _run(root, env, "--defaults", "--no-start").returncode != 0


def test_a_dimse_port_colliding_with_the_web_port_is_refused(tmp_path):
    # Compose reports this as a failure about neither port in particular.
    root, env, _log = _harness(tmp_path, DICOMHAWK_PORTS="8080")
    result = _run(root, env, "--defaults", "--no-start")
    assert result.returncode != 0
    assert "more than once" in result.stderr


def test_a_failed_build_reports_the_step_and_dumps_logs(tmp_path):
    root, env, log = _harness(tmp_path)
    bindir = Path(env["PATH"].split(os.pathsep)[0])
    (bindir / "docker").write_text(
        FAKE_DOCKER.replace('exit 0\n"""', 'exit 0\n"""')
        .rstrip()
        .replace(
            "esac\nexit 0", 'esac\ncase "$*" in "compose build") exit 1 ;; esac\nexit 0'
        )
    )
    (bindir / "docker").chmod(0o755)

    result = _run(root, env, "--defaults")
    assert result.returncode != 0
    assert "Build failed" in result.stderr
    assert "compose logs --tail=50 dicomhawk" in log.read_text()


def test_a_failed_start_reports_the_step(tmp_path):
    root, env, log = _harness(tmp_path)
    bindir = Path(env["PATH"].split(os.pathsep)[0])
    (bindir / "docker").write_text(
        FAKE_DOCKER.rstrip().replace(
            "esac\nexit 0", 'esac\ncase "$*" in "compose up -d") exit 1 ;; esac\nexit 0'
        )
    )
    (bindir / "docker").chmod(0o755)

    result = _run(root, env, "--defaults")
    assert result.returncode != 0
    assert "Startup failed" in result.stderr


def test_a_busy_port_is_warned_about_but_not_fatal(tmp_path):
    root, env, _log = _harness(tmp_path)
    bindir = Path(env["PATH"].split(os.pathsep)[0])
    (bindir / "ss").write_text('#!/bin/sh\necho "LISTEN 0 0 0.0.0.0:104 0.0.0.0:*"\n')
    (bindir / "ss").chmod(0o755)

    result = _run(root, env, "--defaults", "--no-start")
    assert result.returncode == 0
    assert "104" in result.stderr and "in use" in result.stderr


def test_the_summary_names_how_to_authenticate_to_the_operator_api(tmp_path):
    # The 401 sends a Basic challenge, so a browser asks for a username that is never checked.
    root, env, _log = _harness(tmp_path, DICOMHAWK_OPERATOR_TOKEN="s3cret")
    result = _run(root, env, "--defaults", "--no-seed")

    assert "s3cret" in result.stdout
    assert "username blank" in result.stdout
    assert "DICOMHAWK_OPERATOR_TOKEN" in result.stdout


def test_the_summary_warns_when_the_operator_api_is_unauthenticated(tmp_path):
    root, env, _log = _harness(tmp_path)
    result = _run(root, env, "--defaults", "--no-seed")
    assert "no token set" in result.stderr


def test_a_plaintext_deployment_relaxes_the_secure_cookie(tmp_path):
    # A Secure cookie is discarded over plain HTTP, so the granted session never survives.
    root, env, _log = _harness(tmp_path)
    _run(root, env, "--defaults", "--no-start")
    assert _env_values(root / ".env")["DICOMHAWK_SECURE_COOKIES"] == "false"


def test_a_declared_tls_frontend_keeps_the_profiles_own_behaviour(tmp_path):
    root, env, _log = _harness(
        tmp_path, DICOMHAWK_PUBLIC_BASE_URL="https://pacs.example.org"
    )
    _run(root, env, "--defaults", "--no-start")
    assert _env_values(root / ".env")["DICOMHAWK_SECURE_COOKIES"] == ""


def test_a_trusted_proxy_also_counts_as_tls_in_front(tmp_path):
    root, env, _log = _harness(tmp_path, DICOMHAWK_TRUSTED_PROXY="10.0.0.5")
    _run(root, env, "--defaults", "--no-start")
    assert _env_values(root / ".env")["DICOMHAWK_SECURE_COOKIES"] == ""


def test_an_explicit_secure_cookie_choice_is_not_overridden(tmp_path):
    root, env, _log = _harness(tmp_path, DICOMHAWK_SECURE_COOKIES="true")
    _run(root, env, "--defaults", "--no-start")
    assert _env_values(root / ".env")["DICOMHAWK_SECURE_COOKIES"] == "true"
