import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "deploy" / "lockdown-egress.sh"
SUBNET = "172.30.0.0/16"
BRIDGE = "br-1234567890ab"

# The three rules as the kernel stores them after install (normalised ctstate, comment before -j).
KERNEL_RULES = [
    f"-A DOCKER-USER -s {SUBNET} -m conntrack --ctstate RELATED,ESTABLISHED "
    "-m comment --comment dicomhawk-egress -j RETURN",
    f"-A DOCKER-USER -s {SUBNET} -m comment --comment dicomhawk-egress -j DROP",
    f"-A INPUT -i {BRIDGE} -s {SUBNET} -m conntrack --ctstate NEW "
    "-m comment --comment dicomhawk-egress -j DROP",
]

# Minimal stateful iptables: -S prints a chain's rules, -I/-A add, -D removes a matching rule.
FAKE_IPTABLES = r"""#!/bin/sh
echo "$*" >> "$DICOMHAWK_TEST_LOG"
op=$1; chain=$2
subnet=""; target=""; prev=""
for a in "$@"; do
  [ "$prev" = "-s" ] && subnet=$a
  [ "$prev" = "-j" ] && target=$a
  prev=$a
done
case "$op" in
  -S)
    [ -f "$DICOMHAWK_TEST_STATE" ] && grep "^-A $chain " "$DICOMHAWK_TEST_STATE"
    exit 0 ;;
  -I|-A)
    ct=""; prev=""
    for a in "$@"; do [ "$prev" = "--ctstate" ] && ct=$a; prev=$a; done
    line="-A $chain -s $subnet"
    [ -n "$ct" ] && line="$line -m conntrack --ctstate $ct"
    line="$line -m comment --comment dicomhawk-egress -j $target"
    echo "$line" >> "$DICOMHAWK_TEST_STATE"
    exit 0 ;;
  -D)
    tmp="${DICOMHAWK_TEST_STATE}.tmp"; : > "$tmp"; removed=0
    while IFS= read -r line; do
      if [ "$removed" = 0 ] \
         && printf '%s' "$line" | grep -q "^-A $chain " \
         && printf '%s' "$line" | grep -qF -- "-s $subnet " \
         && printf '%s' "$line" | grep -qF -- "-j $target"; then
        removed=1; continue
      fi
      printf '%s\n' "$line" >> "$tmp"
    done < "$DICOMHAWK_TEST_STATE"
    mv "$tmp" "$DICOMHAWK_TEST_STATE"
    [ "$removed" = 1 ] && exit 0 || exit 1 ;;
  -C) exit 1 ;;
esac
exit 0
"""

FAKE_DOCKER = (
    "#!/bin/sh\n"
    "case \"$*\" in\n"
    "  *Id*) echo 1234567890abcdef ;;\n"
    f"  *) echo {SUBNET} ;;\n"
    "esac\n"
)


def _harness(tmp_path, seed_rules=()):
    """Install fake docker/iptables on PATH and return (env, state_file, log_file)."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "docker").write_text(FAKE_DOCKER)
    (bindir / "iptables").write_text(FAKE_IPTABLES)
    (bindir / "docker").chmod(0o755)
    (bindir / "iptables").chmod(0o755)

    state = tmp_path / "state"
    state.write_text("".join(f"{r}\n" for r in seed_rules))
    log = tmp_path / "iptables.log"
    log.write_text("")

    env = {
        **os.environ,
        "PATH": f"{bindir}:{os.environ['PATH']}",
        "DICOMHAWK_FIREWALL_TEST": "1",
        "DICOMHAWK_TEST_LOG": str(log),
        "DICOMHAWK_TEST_STATE": str(state),
    }
    return env, state, log


def _run(env, action):
    return subprocess.run(
        [SCRIPT, action], env=env, capture_output=True, text=True
    )


def test_apply_inserts_rules_in_kernel_canonical_order(tmp_path):
    env, _state, log = _harness(tmp_path)  # clean host, no rules yet
    assert _run(env, "apply").returncode == 0

    rules = log.read_text()
    # Inserted in the kernel's stored order (comment before -j) so check/remove match it later.
    assert f"-I DOCKER-USER -s {SUBNET} -m comment --comment dicomhawk-egress -j DROP" in rules
    assert (
        f"-I DOCKER-USER -s {SUBNET} -m conntrack --ctstate RELATED,ESTABLISHED "
        "-m comment --comment dicomhawk-egress -j RETURN"
    ) in rules
    assert (
        f"-I INPUT -i {BRIDGE} -s {SUBNET} -m conntrack --ctstate NEW "
        "-m comment --comment dicomhawk-egress -j DROP"
    ) in rules


def test_apply_is_idempotent_against_already_installed_rules(tmp_path):
    # Regression: the old -C probe missed its own rules, so a second apply stacked duplicates.
    env, _state, log = _harness(tmp_path, seed_rules=KERNEL_RULES)
    assert _run(env, "apply").returncode == 0
    assert "-I " not in log.read_text()  # nothing re-inserted


def test_check_matches_kernel_normalized_rules(tmp_path):
    # The core bug: rules are installed, yet `check` used to exit 1 ("Bad rule").
    env, _state, _log = _harness(tmp_path, seed_rules=KERNEL_RULES)
    assert _run(env, "check").returncode == 0


def test_check_fails_when_rules_absent(tmp_path):
    env, _state, _log = _harness(tmp_path)  # empty state
    assert _run(env, "check").returncode != 0


def test_remove_deletes_the_installed_rules(tmp_path):
    env, state, _log = _harness(tmp_path, seed_rules=KERNEL_RULES)
    assert _run(env, "remove").returncode == 0
    assert state.read_text().strip() == ""  # all three rules gone


def test_remove_purges_duplicate_rules(tmp_path):
    # A host left in the old duplicated state must come back fully clean.
    env, state, _log = _harness(tmp_path, seed_rules=list(KERNEL_RULES) + list(KERNEL_RULES))
    assert _run(env, "remove").returncode == 0
    assert state.read_text().strip() == ""
