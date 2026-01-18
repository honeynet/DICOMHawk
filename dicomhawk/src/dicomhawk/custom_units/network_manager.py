import subprocess
from services.blackhole_service import IBlackhole


class Blackhole(IBlackhole):

    def __init__(
        self, app_logger, exceptions_logger, mass_scanners_blocked, blackhole_file_path
    ):
        self.blackhole_file_path = blackhole_file_path
        try:
            self.logger = app_logger
            if mass_scanners_blocked:
                self.block_scanners(self.blackhole_file_path, "known_scanners")
            else:
                if self.is_scanners_blocked("known_scanners"):
                    self.allow_scanners("known_scanners")
                    self.exceptions_logger = exceptions_logger
        except Exception as e:
            pass

    def is_scanners_blocked(self, known_scanners):
        """

        Check if the ipset known_scanners exists

        """

        try:
            result = subprocess.run(
                ["ipset", "list", known_scanners],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            return result.returncode == 0
        except FileNotFoundError:
            self.exceptions_logger.exception(
                "ipset command not found. Make sure ipset is installed."
            )
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
            subprocess.run(
                f" ipset create {ipset_name} hash:ip", shell=True, check=True
            )
            self.logger.debug(f"IP set '{ipset_name}' created.")
        except subprocess.CalledProcessError as e:
            self.exceptions_logger.exception(f"Failed to create IP set: {e}")

    def add_ip_to_ipset(self, ipset_name, ip_address):
        try:
            subprocess.run(
                f" ipset add {ipset_name} {ip_address}", shell=True, check=True
            )
            self.logger.debug(
                f"IP address {ip_address} added to IP set '{ipset_name}'."
            )
        except subprocess.CalledProcessError as e:
            self.exceptions_logger.exception(
                f"Failed to add IP {ip_address} to IP set: {e}"
            )

    def setup_iptables_rule(self, ipset_name):
        try:
            subprocess.run(
                f" iptables -I INPUT -m set --match-set {ipset_name} src -j DROP",
                shell=True,
                check=True,
            )
            self.logger.debug(f"iptables rule added for IP set '{ipset_name}'.")
        except subprocess.CalledProcessError as e:
            self.exceptions_logger.exception(f"Failed to add iptables rule: {e}")

    """Null-route the known scanners IPs"""

    def block_scanners(self, source_list, set_name):
        try:
            self.create_ipset(set_name)
            for ip in self.get_known_scanners(source_list):
                self.add_ip_to_ipset(set_name, ip)

            self.setup_iptables_rule(set_name)
        except Exception as e:
            self.exceptions_logger.exception(
                "Unexpected error while blocking mass scanners", e
            )

    """Allow known scanners to interact with the server"""

    def allow_scanners(self, set_name):
        subprocess.run(
            f" iptables -D INPUT -m set --match-set {set_name} src -j DROP",
            shell=True,
            check=True,
        )

        subprocess.run(" ipset destroy {set_name}", shell=True, check=True)
