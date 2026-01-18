from abc import ABC, abstractmethod


class IThreatIntelligence(ABC):
    @abstractmethod
    def get_reputation_data(self, rep_dat, ip, ip_scanned) -> dict:
        pass

    def getVirusTotalScore(self, ip) -> dict:
        pass

    def getIpqualityScore(self, ip) -> dict:
        pass

    def getIPSecurityScore(self, ip) -> dict:
        pass
