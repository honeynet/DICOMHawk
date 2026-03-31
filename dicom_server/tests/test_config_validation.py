"""
Tests for startup validation of placeholder API key values in config.py.

The bug: ABUSE_IP_API_KEY, IP_QUALITY_SCORE_API_KEY, and VIRUS_TOTAL_API_KEY
all default to the string "apikey" when the environment variable is not set.
The server starts silently with no warning, so threat-intelligence features
fail without any indication to the operator.

Expected: a warn_placeholder_keys() function emits a WARNING log for each
key that is missing or still set to its placeholder value.
"""

import logging
import importlib
import sys
import os
import pytest


def reload_config(env_overrides=None):
    """Reload config with given env overrides, restoring env afterwards."""
    original = {}
    keys = [
        "ABUSE_IP__KEY",
        "IP_QUALITY_SCORE_API_KEY",
        "VIRUS_TOTAL_API_KEY",
    ]
    env_overrides = env_overrides or {}
    for k in keys:
        original[k] = os.environ.get(k)
        if k in env_overrides:
            os.environ[k] = env_overrides[k]
        elif k in os.environ:
            del os.environ[k]

    # Force a fresh import of config.
    if "config" in sys.modules:
        del sys.modules["config"]

    # Ensure dicom_server directory is on path.
    dicom_dir = os.path.join(os.path.dirname(__file__), "..")
    if dicom_dir not in sys.path:
        sys.path.insert(0, dicom_dir)

    import config

    # Restore env.
    for k in keys:
        if original[k] is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = original[k]

    return config


class TestWarnPlaceholderKeys:
    """config.warn_placeholder_keys() must exist and emit warnings."""

    def test_function_exists(self):
        cfg = reload_config()
        assert hasattr(cfg, "warn_placeholder_keys"), (
            "config.py must export a warn_placeholder_keys() function"
        )

    def test_warns_when_all_keys_are_placeholders(self, caplog):
        cfg = reload_config()
        with caplog.at_level(logging.WARNING):
            cfg.warn_placeholder_keys()
        # All three keys default to "apikey" — all three should be warned about.
        assert len(caplog.records) >= 3, (
            f"Expected at least 3 warnings for placeholder keys, got {len(caplog.records)}"
        )

    def test_warns_mention_key_names(self, caplog):
        cfg = reload_config()
        with caplog.at_level(logging.WARNING):
            cfg.warn_placeholder_keys()
        messages = " ".join(r.message for r in caplog.records)
        for name in ("ABUSE_IP", "IP_QUALITY_SCORE", "VIRUS_TOTAL"):
            assert name in messages, (
                f"Warning messages should mention {name}, got: {messages!r}"
            )

    def test_no_warning_when_keys_are_set(self, caplog):
        cfg = reload_config(
            {
                "ABUSE_IP__KEY": "real-abuse-key",
                "IP_QUALITY_SCORE_API_KEY": "real-ipqs-key",
                "VIRUS_TOTAL_API_KEY": "real-vt-key",
            }
        )
        with caplog.at_level(logging.WARNING):
            cfg.warn_placeholder_keys()
        assert len(caplog.records) == 0, (
            f"Expected no warnings when all keys are set, got: {caplog.records}"
        )
