import os
import subprocess
from pathlib import Path


def test_egress_script_builds_forward_and_host_rules(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "iptables.log"
    docker = bindir / "docker"
    docker.write_text(
        "#!/bin/sh\n"
        "case \"$*\" in\n"
        "  *Id*) echo 1234567890abcdef ;;\n"
        "  *) echo 172.30.0.0/16 ;;\n"
        "esac\n"
    )
    iptables = bindir / "iptables"
    iptables.write_text(
        "#!/bin/sh\n"
        "echo \"$*\" >> \"$DICOMHAWK_TEST_LOG\"\n"
        "[ \"$1\" = -C ] && exit 1\n"
        "exit 0\n"
    )
    docker.chmod(0o755)
    iptables.chmod(0o755)

    env = {
        **os.environ,
        "PATH": f"{bindir}:{os.environ['PATH']}",
        "DICOMHAWK_FIREWALL_TEST": "1",
        "DICOMHAWK_TEST_LOG": str(log),
    }
    script = Path(__file__).parents[1] / "deploy" / "lockdown-egress.sh"
    subprocess.run([script, "apply"], env=env, check=True, capture_output=True)

    rules = log.read_text()
    assert "-I DOCKER-USER -s 172.30.0.0/16 -j DROP" in rules
    assert "-I DOCKER-USER -s 172.30.0.0/16 -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN" in rules
    assert "-I INPUT -i br-1234567890ab -s 172.30.0.0/16 -m conntrack --ctstate NEW -j DROP" in rules
