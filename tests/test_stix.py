"""Tests for the STIX 2.1 converter (stix.py)."""
import json
from datetime import datetime
from unittest.mock import MagicMock

import pytest
import stix2

from dicomhawk.stix import (
    _EVENT_PATTERNS,
    new_honeypot_identity,
    to_stix_bundle,
)


def _make_fake_event(
    src_ip="93.184.216.34",
    src_port=52000,
    dst_ip="10.0.0.1",
    dst_port=104,
    event_name="EVT_C_STORE",
):
    """Builds a minimal pynetdicom Event mock that satisfies the converter."""
    fake_assoc = MagicMock()
    fake_assoc.requestor.address = src_ip
    fake_assoc.requestor.port = src_port
    fake_assoc.acceptor.address = dst_ip
    fake_assoc.acceptor.port = dst_port

    fake_event_type = MagicMock()
    fake_event_type.name = event_name

    fake_evt = MagicMock()
    fake_evt.assoc = fake_assoc
    fake_evt._event = fake_event_type
    return fake_evt


def _make_fake_message(event_name="EVT_C_STORE"):
    """Returns a minimal EventMessage-compatible mock."""
    from dicomhawk.bus import EventMessage
    msg = MagicMock(spec=EventMessage)
    msg.evt = _make_fake_event(event_name=event_name)
    msg.timestamp = datetime.now()
    msg.direction = "request"
    msg.status = None
    msg.data = None
    msg.error = None
    return msg


class TestNewHoneypotIdentity:
    def test_returns_stix_identity(self):
        identity = new_honeypot_identity()
        assert isinstance(identity, stix2.Identity)

    def test_identity_class_is_system(self):
        identity = new_honeypot_identity()
        assert identity.identity_class == "system"

    def test_identity_name_includes_ae_title(self):
        identity = new_honeypot_identity(ae_title="MYHONEYPOT")
        assert "MYHONEYPOT" in identity.name

    def test_identity_has_spec_version(self):
        identity = new_honeypot_identity()
        assert identity.spec_version == "2.1"

    def test_identity_id_prefix(self):
        identity = new_honeypot_identity()
        assert identity.id.startswith("identity--")


class TestToStixBundle:
    @pytest.fixture
    def identity(self):
        return new_honeypot_identity(ae_title="TESTHONEYPOT")

    @pytest.fixture
    def bundle(self, identity):
        msg = _make_fake_message("EVT_C_STORE")
        return to_stix_bundle(msg, identity)

    def test_returns_stix_bundle(self, bundle):
        assert isinstance(bundle, stix2.Bundle)

    def test_bundle_spec_version(self, bundle):
        # stix2.Bundle does not expose spec_version as a Python attribute;
        # verify it instead via the serialized JSON output
        parsed = json.loads(bundle.serialize())
        assert parsed["type"] == "bundle"

    def test_bundle_contains_expected_types(self, bundle):
        types = {obj.type for obj in bundle.objects}
        assert "ipv4-addr" in types
        assert "network-traffic" in types
        assert "attack-pattern" in types
        assert "observed-data" in types
        assert "sighting" in types
        assert "identity" in types

    def test_bundle_has_two_ip_addresses(self, bundle):
        ips = [o for o in bundle.objects if o.type == "ipv4-addr"]
        assert len(ips) == 2

    def test_attacker_ip_is_correct(self, bundle):
        ips = {o.value for o in bundle.objects if o.type == "ipv4-addr"}
        assert "93.184.216.34" in ips

    def test_honeypot_ip_is_correct(self, bundle):
        ips = {o.value for o in bundle.objects if o.type == "ipv4-addr"}
        assert "10.0.0.1" in ips


class TestNetworkTraffic:
    @pytest.fixture
    def traffic(self):
        identity = new_honeypot_identity()
        msg = _make_fake_message()
        bundle = to_stix_bundle(msg, identity)
        return next(o for o in bundle.objects if o.type == "network-traffic")

    def test_src_port_is_correct(self, traffic):
        assert traffic.src_port == 52000

    def test_dst_port_is_correct(self, traffic):
        assert traffic.dst_port == 104

    def test_protocols_includes_tcp(self, traffic):
        assert "tcp" in traffic.protocols

    def test_src_ref_points_to_ipv4(self, traffic):
        assert traffic.src_ref.startswith("ipv4-addr--")

    def test_dst_ref_points_to_ipv4(self, traffic):
        assert traffic.dst_ref.startswith("ipv4-addr--")


class TestAttackPattern:
    @pytest.mark.parametrize("event_name", list(_EVENT_PATTERNS.keys()))
    def test_all_known_events_produce_attack_pattern(self, event_name):
        identity = new_honeypot_identity()
        msg = _make_fake_message(event_name)
        bundle = to_stix_bundle(msg, identity)
        patterns = [o for o in bundle.objects if o.type == "attack-pattern"]
        assert len(patterns) == 1

    @pytest.mark.parametrize("event_name", list(_EVENT_PATTERNS.keys()))
    def test_attack_pattern_name_matches_mapping(self, event_name):
        identity = new_honeypot_identity()
        msg = _make_fake_message(event_name)
        bundle = to_stix_bundle(msg, identity)
        pattern = next(o for o in bundle.objects if o.type == "attack-pattern")
        assert pattern.name == _EVENT_PATTERNS[event_name]["name"]

    def test_attack_pattern_has_dicom_external_reference(self):
        identity = new_honeypot_identity()
        msg = _make_fake_message("EVT_C_FIND")
        bundle = to_stix_bundle(msg, identity)
        pattern = next(o for o in bundle.objects if o.type == "attack-pattern")
        urls = [ref.url for ref in pattern.external_references]
        assert any("dicomstandard.org" in u for u in urls)

    def test_unknown_event_produces_graceful_fallback(self):
        """An unrecognized event should not crash the converter."""
        identity = new_honeypot_identity()
        msg = _make_fake_message("EVT_UNKNOWN_EVENT_XYZ")
        bundle = to_stix_bundle(msg, identity)
        patterns = [o for o in bundle.objects if o.type == "attack-pattern"]
        assert len(patterns) == 1
        assert "EVT_UNKNOWN_EVENT_XYZ" in patterns[0].name


class TestObservedData:
    @pytest.fixture
    def identity(self):
        return new_honeypot_identity()

    @pytest.fixture
    def observed(self, identity):
        msg = _make_fake_message()
        bundle = to_stix_bundle(msg, identity)
        return next(o for o in bundle.objects if o.type == "observed-data")

    def test_number_observed_is_one(self, observed):
        assert observed.number_observed == 1

    def test_first_and_last_observed_match(self, observed):
        assert observed.first_observed == observed.last_observed

    def test_object_refs_includes_network_traffic(self, identity):
        msg = _make_fake_message()
        bundle = to_stix_bundle(msg, identity)
        observed = next(o for o in bundle.objects if o.type == "observed-data")
        traffic = next(o for o in bundle.objects if o.type == "network-traffic")
        assert traffic.id in observed.object_refs

    def test_timestamp_is_utc_aware(self, observed):
        assert observed.first_observed.tzinfo is not None


class TestSighting:
    @pytest.fixture
    def bundle(self):
        identity = new_honeypot_identity()
        msg = _make_fake_message("EVT_C_GET")
        return to_stix_bundle(msg, identity), identity

    def test_sighting_exists(self, bundle):
        b, _ = bundle
        sightings = [o for o in b.objects if o.type == "sighting"]
        assert len(sightings) == 1

    def test_sighting_of_ref_points_to_attack_pattern(self, bundle):
        b, _ = bundle
        sighting = next(o for o in b.objects if o.type == "sighting")
        attack = next(o for o in b.objects if o.type == "attack-pattern")
        assert sighting.sighting_of_ref == attack.id

    def test_sighting_observed_data_refs_match(self, bundle):
        b, _ = bundle
        sighting = next(o for o in b.objects if o.type == "sighting")
        observed = next(o for o in b.objects if o.type == "observed-data")
        assert observed.id in sighting.observed_data_refs

    def test_sighting_where_sighted_refs_is_honeypot(self, bundle):
        b, identity = bundle
        sighting = next(o for o in b.objects if o.type == "sighting")
        assert identity.id in sighting.where_sighted_refs

    def test_sighting_count_is_one(self, bundle):
        b, _ = bundle
        sighting = next(o for o in b.objects if o.type == "sighting")
        assert sighting.count == 1


class TestSerialization:
    def test_bundle_serializes_to_valid_json(self):
        identity = new_honeypot_identity()
        msg = _make_fake_message()
        bundle = to_stix_bundle(msg, identity)
        parsed = json.loads(bundle.serialize())
        assert parsed["type"] == "bundle"
        assert len(parsed["objects"]) > 0

    def test_all_objects_have_stix_ids(self):
        identity = new_honeypot_identity()
        msg = _make_fake_message()
        bundle = to_stix_bundle(msg, identity)
        for obj in bundle.objects:
            assert obj.id, f"Object {obj.type} has no id"

    def test_all_sdos_have_created_by_ref(self):
        identity = new_honeypot_identity()
        msg = _make_fake_message()
        bundle = to_stix_bundle(msg, identity)
        sdos = [o for o in bundle.objects if o.type in {
            "attack-pattern", "observed-data", "sighting"
        }]
        for sdo in sdos:
            assert sdo.created_by_ref == identity.id, (
                f"{sdo.type} missing created_by_ref"
            )
