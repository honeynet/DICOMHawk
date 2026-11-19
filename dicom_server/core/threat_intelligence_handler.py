import requests
from datetime import datetime
from services.threat_intelligence_service import IThreatIntelligence


class ThreatIntelligence(IThreatIntelligence):
    def __init__(
        self,
        app_logger,
        exceptions_logger,
        abuse_ip_api_key,
        ip_quality_score_api_key,
        virus_total_api_key,
        vpndetection_api_key="",
    ):
        self.abuse_ip_api_key = abuse_ip_api_key
        self.ip_quality_score_api_key = ip_quality_score_api_key
        self.virus_total_api_key = virus_total_api_key
        self.vpndetection_api_key = vpndetection_api_key
        self.exceptions_logger = exceptions_logger
        self.logger = app_logger

    # Get IP security score from ABUSEIPDB

    def getIPSecurityScore(self, ip):

        api_key = self.abuse_ip_api_key
        url = "https://api.abuseipdb.com/api/v2/check"
        headers = {"Accept": "application/json", "Key": api_key}
        params = {"ipAddress": ip, "maxAgeInDays": 90}

        try:
            response = requests.get(url, headers=headers, params=params)
            if response.status_code == 200:
                data = response.json()["data"]
                country = data.get("countryCode", "N/A")
                isp = data.get("isp", "N/A")
                abuseIPConfidenceScore = data["abuseConfidenceScore"]
                return [isp, abuseIPConfidenceScore, country]
            else:
                self.exceptions_logger.exception(
                    f" {response.status_code} - {response.json().get('errors', [{'detail': 'Unknown error'}])[0]['detail']}"
                )
        except Exception:
            self.exceptions_logger.exception(
                f'Unexpected error while getting IP security score from "abuseipdb.com"'
            )

    # Get IP security score from IPQUALITYSCORE

    def getIpqualityScore(self, ip):
        try:
            api_key = self.ip_quality_score_api_key
            url = f"https://ipqualityscore.com/api/json/ip/{api_key}/{ip}"
            response = requests.get(url)
            if response.status_code == 200:
                data = response.json()
                return [
                    data.get("fraud_score", "N/A"),
                    data.get("proxy", "N/A"),
                    data.get("city", "N/A"),
                    data.get("bot_status", "N/A"),
                    data.get("vpn", "N/A"),
                    data.get("latitude", "N/A"),
                    data.get("longitude", "N/A"),
                ]
            return {"service": "IPQualityScore", "error": response.text}
        except Exception:
            self.exceptions_logger.exception(
                f'Unexpected error while getting IP quality score from "ipqualityscore.com"'
            )

    # Get IP security score from VIRUSTOTAL

    def getVirusTotalScore(self, ip):
        try:
            url = f"https://www.virustotal.com/api/v3/ip_addresses/{ip}"
            headers = {"x-apikey": self.virus_total_api_key}
            response = requests.get(url, headers=headers)
            if response.status_code == 200:
                data = response.json()["data"]
                # print(data)
                result_counts = {}

                for analysis in data["attributes"]["last_analysis_results"].values():
                    result = analysis["result"]
                    result_counts[result] = result_counts.get(result, 0) + 1
                return result_counts
        except Exception:
            self.exceptions_logger.exception(
                f'Unexpected error while getting IP information from "virustotal.com"'
            )

    # Get VPN, Tor and relay flags from VPNDETECTION (skipped without a key)

    def getVpnDetection(self, ip):
        if not self.vpndetection_api_key:
            return None
        try:
            url = f"https://api.vpndetection.io/{ip}"
            headers = {"Authorization": f"Bearer {self.vpndetection_api_key}"}
            response = requests.get(url, headers=headers, timeout=10)
            if response.status_code == 200:
                data = response.json()
                return [
                    data.get("is_vpn", "N/A"),
                    data.get("is_tor", "N/A"),
                    data.get("is_relay", "N/A"),
                    (data.get("asn") or {}).get("type", "N/A"),
                ]
            self.exceptions_logger.error(
                f'VPNDetection returned {response.status_code} for {ip}: {response.text}'
            )
        except Exception:
            self.exceptions_logger.exception(
                f'Unexpected error while getting VPN flags from "vpndetection.io"'
            )

    # Build reputation object

    def get_reputation_data(self, ip):

        current_time = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")
        abusedb = self.getIPSecurityScore(ip)
        abusedb_object = abusedb if abusedb else ["", "", ""]
        ip_quality = self.getIpqualityScore(ip)
        ip_quality_score = ip_quality if ip_quality else ["", "", "", "", "", "", ""]

        vt = self.getVirusTotalScore(ip)
        virus_total = vt if vt else {}
        vpn_detection = self.getVpnDetection(ip) or ["", "", "", ""]
        try:
            rep_dat = {}
            rep_dat["timestamp"] = str(current_time)
            rep_dat["virus_total_results"] = virus_total
            rep_dat["ip"] = ip
            rep_dat["ip_quality_score"] = ip_quality_score[0]
            rep_dat["proxy"] = ip_quality_score[1]
            rep_dat["region"] = ip_quality_score[2]
            rep_dat["vpn"] = ip_quality_score[4]
            rep_dat["country"] = abusedb_object[2]
            rep_dat["ISP"] = abusedb_object[0]
            rep_dat["AbuseDBScore"] = abusedb_object[1]
            rep_dat["vpndetection_vpn"] = vpn_detection[0]
            rep_dat["vpndetection_tor"] = vpn_detection[1]
            rep_dat["vpndetection_relay"] = vpn_detection[2]
            rep_dat["vpndetection_network"] = vpn_detection[3]
            return rep_dat
        except Exception:
            self.exceptions_logger.exception(
                "Unexpected error while building IP reputation object"
            )
