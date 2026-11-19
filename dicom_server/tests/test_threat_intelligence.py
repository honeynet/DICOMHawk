"""
Threat Intelligence Test Suite

Checks the VPNDetection lookup in ThreatIntelligence with requests.get mocked:
no request without a key, the Bearer header and flags with one, and empty
fields, the other services untouched, when it fails.
"""

import os
import sys
from unittest.mock import Mock, patch

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.threat_intelligence_handler import ThreatIntelligence

VPN_ANSWER = {
    "ip": "185.159.157.20",
    "is_vpn": True,
    "is_tor": False,
    "is_relay": False,
    "asn": {"asn": 209103, "name": "Proton AG", "type": "hosting"},
}


def make_handler(vpndetection_api_key=""):
    return ThreatIntelligence(Mock(), Mock(), "abusekey", "ipqskey", "vtkey", vpndetection_api_key)


def response(status_code, body):
    return Mock(status_code=status_code, json=Mock(return_value=body), text=str(body))


def fake_get(vpndetection_response):
    def get(url, headers=None, params=None, timeout=None):
        if url.startswith("https://api.vpndetection.io/"):
            return vpndetection_response
        if "abuseipdb" in url:
            return response(200, {"data": {"countryCode": "CH", "isp": "Proton AG", "abuseConfidenceScore": 7}})
        if "ipqualityscore" in url:
            return response(200, {"fraud_score": 75, "proxy": True, "city": "Zurich", "vpn": True})
        return response(404, {})

    return get


def test_no_key_makes_no_request():
    with patch("core.threat_intelligence_handler.requests.get", side_effect=fake_get(None)) as get:
        rep_dat = make_handler().get_reputation_data("185.159.157.20")
    assert not any(call.args[0].startswith("https://api.vpndetection.io/") for call in get.call_args_list)
    assert rep_dat["vpndetection_vpn"] == ""
    assert rep_dat["vpndetection_tor"] == ""


def test_key_sends_bearer_and_fills_flags():
    with patch("core.threat_intelligence_handler.requests.get", side_effect=fake_get(response(200, VPN_ANSWER))) as get:
        rep_dat = make_handler("vd-key").get_reputation_data("185.159.157.20")
    calls = [call for call in get.call_args_list if call.args[0].startswith("https://api.vpndetection.io/")]
    assert len(calls) == 1
    assert calls[0].args[0] == "https://api.vpndetection.io/185.159.157.20"
    assert calls[0].kwargs["headers"] == {"Authorization": "Bearer vd-key"}
    assert rep_dat["vpndetection_vpn"] is True
    assert rep_dat["vpndetection_tor"] is False
    assert rep_dat["vpndetection_relay"] is False
    assert rep_dat["vpndetection_network"] == "hosting"
    assert rep_dat["vpn"] is True
    assert rep_dat["AbuseDBScore"] == 7


def test_error_answer_leaves_flags_empty():
    with patch("core.threat_intelligence_handler.requests.get", side_effect=fake_get(response(401, {"error": "bad key"}))):
        rep_dat = make_handler("bad-key").get_reputation_data("185.159.157.20")
    assert rep_dat["vpndetection_vpn"] == ""
    assert rep_dat["vpndetection_network"] == ""
    assert rep_dat["ip_quality_score"] == 75
    assert rep_dat["country"] == "CH"
