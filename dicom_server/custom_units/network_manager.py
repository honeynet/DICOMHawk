import ipaddress
import re
import subprocess

from services.blackhole_service import IBlackhole


_IPSET_NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.:-]{0,31}$")


class Blackhole(IBlackhole):

    def __init__(
        self, app_logger, exceptions_logger, mass_scanners_blocked, blackhole_file_path
    ):
        self.blackhole_file_path = blackhole_file_path
        self.logger = app_logger
        self.exceptions_logger = exceptions_logger

        try:
            if mass_scanners_blocked:
                self.block_scanners(self.blackhole_file_path, "known_scanners")
            else:
                if self.is_scanners_blocked("known_scanners"):
                    self.allow_scanners("known_scanners")
        except Exception:
            self.exceptions_logger.exception(
                "Unexpected error while initializing Blackhole network manager"
            )

    @staticmethod
    def _validate_ipset_name(ipset_name: str) -> None:
        if not isinstance(ipset_name, str) or not _IPSET_NAME_RE.fullmatch(ipset_name):
            raise ValueError(
                "Invalid ipset name. Allowed: [A-Za-z0-9_][A-Za-z0-9_.:-]{0,31}"
            )

    @staticmethod
    def _validate_ip_address(ip_address: str) -> None:
        try:
            ipaddress.ip_address(ip_address)
        except ValueError as e:
            raise ValueError(f"Invalid IP address: {ip_address}") from e

    def is_scanners_blocked(self, known_scanners):
        """

        Check if the ipset known_scanners exists

        """

        try:
            self._validate_ipset_name(known_scanners)
            result = subprocess.run(
                ["ipset", "list", known_scanners],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
            return result.returncode == 0
        except FileNotFoundError:
            self.exceptions_logger.exception(
                "ipset command not found. Make sure ipset is installed."
            )
            return False
        except ValueError:
            self.exceptions_logger.exception("Invalid ipset name provided")
            return False

    def get_known_scanners(self, scanners_file):
        """

        Get the known scanners list

        """
        knownScanners = []
        try:
            with open(scanners_file, "r") as file:
                for line in file:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        ip_address = line.split("#")[0].strip()
                        knownScanners.append(ip_address)
        except Exception as e:
            self.exceptions_logger.exception(f"Error reading file: {e}")
        return knownScanners

    def create_ipset(self, ipset_name):
        """

        Create an ipset on the kernal

        """
        try:
            self._validate_ipset_name(ipset_name)
            subprocess.run(
                ["ipset", "create", ipset_name, "hash:ip", "--exist"],
                check=True,
            )
            self.logger.debug(f"IP set '{ipset_name}' created.")
        except (ValueError, FileNotFoundError):
            self.exceptions_logger.exception("Failed to create IP set")
        except subprocess.CalledProcessError as e:
            self.exceptions_logger.exception(f"Failed to create IP set: {e}")

    def add_ip_to_ipset(self, ipset_name, ip_address):
        try:
            self._validate_ipset_name(ipset_name)
            self._validate_ip_address(ip_address)
            subprocess.run(
                ["ipset", "add", ipset_name, ip_address, "--exist"],
                check=True,
            )
            self.logger.debug(
                f"IP address {ip_address} added to IP set '{ipset_name}'."
            )
        except (ValueError, FileNotFoundError):
            self.exceptions_logger.exception("Failed to add IP to IP set")
        except subprocess.CalledProcessError as e:
            self.exceptions_logger.exception(
                f"Failed to add IP {ip_address} to IP set: {e}"
            )

    def setup_iptables_rule(self, ipset_name):
        try:
            self._validate_ipset_name(ipset_name)
            subprocess.run(
                [
                    "iptables",
                    "-I",
                    "INPUT",
                    "-m",
                    "set",
                    "--match-set",
                    ipset_name,
                    "src",
                    "-j",
                    "DROP",
                ],
                check=True,
            )
            self.logger.debug(f"iptables rule added for IP set '{ipset_name}'.")
        except (ValueError, FileNotFoundError):
            self.exceptions_logger.exception("Failed to add iptables rule")
        except subprocess.CalledProcessError as e:
            self.exceptions_logger.exception(f"Failed to add iptables rule: {e}")

    """Null-route the known scanners IPs"""

    def block_scanners(self, source_list, set_name):
        try:
            self.create_ipset(set_name)
            for ip in self.get_known_scanners(source_list):
                self.add_ip_to_ipset(set_name, ip)

            self.setup_iptables_rule(set_name)
        except Exception:
            self.exceptions_logger.exception(
                "Unexpected error while blocking mass scanners"
            )

    """Allow known scanners to interact with the server"""

    def allow_scanners(self, set_name):
        try:
            self._validate_ipset_name(set_name)
            subprocess.run(
                [
                    "iptables",
                    "-D",
                    "INPUT",
                    "-m",
                    "set",
                    "--match-set",
                    set_name,
                    "src",
                    "-j",
                    "DROP",
                ],
                check=True,
            )
            subprocess.run(["ipset", "destroy", set_name], check=True)
        except (ValueError, FileNotFoundError):
            self.exceptions_logger.exception("Failed to allow scanners")
        except subprocess.CalledProcessError as e:
            self.exceptions_logger.exception(f"Failed to allow scanners: {e}")
