"""
Tests for music_bot.downloader.youtube hardening against YouTube bot-wall.
These tests don't hit network - they validate options building, env parsing,
and IP protection logic.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Ensure repo root is in path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from music_bot.downloader.youtube import (
    _env_bool,
    _env_float,
    _env_int,
    _env_list,
    _env_str,
    _get_base_ydl_options,
    _get_client_sets_to_try,
    _get_extractor_args,
    _get_impersonate_target,
    _get_player_clients,
    _get_player_skip,
    _is_bot_wall_error,
    _is_url,
)


def test_env_parsing():
    with patch.dict(os.environ, {"TEST_BOOL": "true", "TEST_INT": "42", "TEST_FLOAT": "3.14", "TEST_LIST": "tv,web_embedded, ios"}):
        assert _env_bool("TEST_BOOL", False) is True
        assert _env_int("TEST_INT", 0) == 42
        assert abs(_env_float("TEST_FLOAT", 0.0) - 3.14) < 0.001
        assert _env_list("TEST_LIST", []) == ["tv", "web_embedded", "ios"]
        assert _env_str("TEST_LIST", None) == "tv,web_embedded, ios"


def test_default_player_clients_avoids_po_token_clients():
    # Ensure default doesn't include android/web which require PO token
    with patch.dict(os.environ, {}, clear=False):
        # Remove any YTDLP_* that could affect
        for k in list(os.environ.keys()):
            if k.startswith("YTDLP_"):
                del os.environ[k]
        clients = _get_player_clients()
        assert isinstance(clients, list)
        assert len(clients) > 0
        # Should not contain risky clients by default
        risky = {"android", "web", "web_music", "web_creator", "mweb"}
        # web_embedded and tv_embedded are OK
        for c in clients:
            assert c not in risky, f"Default clients should not include risky PO-token client {c}, got {clients}"
        # Should contain at least tv and web_embedded
        assert "tv" in clients
        assert "web_embedded" in clients


def test_env_override_clients():
    with patch.dict(os.environ, {"YTDLP_PLAYER_CLIENTS": "tv,visionos,ios"}):
        clients = _get_player_clients()
        assert clients == ["tv", "visionos", "ios"]


def test_use_ytdlp_default_clients():
    with patch.dict(os.environ, {"YTDLP_USE_YTDLP_DEFAULT_CLIENTS": "true"}):
        clients = _get_player_clients()
        assert clients == []


def test_client_sets_to_try():
    with patch.dict(os.environ, {}, clear=False):
        for k in list(os.environ.keys()):
            if k.startswith("YTDLP_"):
                del os.environ[k]
        sets = _get_client_sets_to_try()
        assert isinstance(sets, list)
        assert len(sets) >= 1
        # First set should be the primary
        assert "tv" in sets[0]
        # All sets should be non-empty
        for s in sets:
            assert len(s) > 0


def test_player_skip_default():
    with patch.dict(os.environ, {}, clear=False):
        for k in list(os.environ.keys()):
            if k.startswith("YTDLP_"):
                del os.environ[k]
        skip = _get_player_skip()
        assert isinstance(skip, list)
        # Default should be configs to reduce requests
        assert "configs" in skip


def test_player_skip_env_none():
    with patch.dict(os.environ, {"YTDLP_PLAYER_SKIP": "none"}):
        skip = _get_player_skip()
        assert skip == []


def test_impersonation_disabled_by_env():
    with patch.dict(os.environ, {"YTDLP_ENABLE_IMPERSONATION": "false"}):
        target = _get_impersonate_target()
        assert target is None

    with patch.dict(os.environ, {"YTDLP_IMPERSONATE": "false"}):
        # Even if curl_cffi available, explicit false should disable
        # Need to also ensure ENABLE is true
        with patch.dict(os.environ, {"YTDLP_ENABLE_IMPERSONATION": "true"}):
            target = _get_impersonate_target()
            assert target is None


def test_impersonation_custom_target():
    with patch.dict(os.environ, {"YTDLP_IMPERSONATE": "safari", "YTDLP_ENABLE_IMPERSONATION": "true"}):
        target = _get_impersonate_target()
        if target is None:
            pytest.skip("curl_cffi/yt-dlp impersonation is not available in this environment")
        # yt-dlp 2026.x wants a typed ImpersonateTarget, not a plain string.
        assert str(target).startswith("safari"), target
        assert getattr(target, "client", target) == "safari"


def test_base_ydl_options_hardening():
    with patch.dict(os.environ, {}, clear=False):
        for k in list(os.environ.keys()):
            if k.startswith("YTDLP_"):
                del os.environ[k]
        opts = _get_base_ydl_options()
        # Check IP protection options exist
        assert "retries" in opts
        assert "fragment_retries" in opts
        assert "extractor_retries" in opts
        assert "socket_timeout" in opts
        assert "sleep_interval" in opts
        assert "max_sleep_interval" in opts
        assert "sleep_interval_requests" in opts
        assert "geo_bypass" in opts
        assert "concurrent_fragment_downloads" in opts
        assert opts["concurrent_fragment_downloads"] == 1  # avoid flagging
        assert "http_headers" in opts
        assert "Accept-Language" in opts["http_headers"]
        # Should not have cookies
        assert "cookiefile" not in opts


def test_extractor_args_structure():
    clients = ["tv", "web_embedded", "visionos"]
    args = _get_extractor_args(clients)
    assert "youtube" in args
    assert "player_client" in args["youtube"]
    assert args["youtube"]["player_client"] == clients
    # Should include max_comments to reduce requests
    assert "max_comments" in args["youtube"]


def test_is_bot_wall_error_detection():
    class FakeExc(Exception):
        pass

    assert _is_bot_wall_error(FakeExc("Sign in to confirm you're not a bot. Use --cookies-from-browser or --cookies for the authentication."))
    assert _is_bot_wall_error(FakeExc("ERROR: [youtube] dQw4w9WgXcQ: Sign in to confirm you're not a bot"))
    assert _is_bot_wall_error(FakeExc("HTTP Error 429: Too Many Requests"))
    assert not _is_bot_wall_error(FakeExc("Some other error"))


def test_is_url():
    assert _is_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert _is_url("https://youtu.be/dQw4w9WgXcQ")
    assert _is_url("https://www.tiktok.com/@user/video/123")
    assert not _is_url("never gonna give you up")
    assert not _is_url("https://example.com")


def test_format_env_override():
    with patch.dict(os.environ, {"YTDLP_FORMAT": "bestaudio/best"}):
        from music_bot.downloader.youtube import _get_format

        assert _get_format() == "bestaudio/best"

    with patch.dict(os.environ, {}, clear=False):
        for k in list(os.environ.keys()):
            if k.startswith("YTDLP_"):
                del os.environ[k]
        from music_bot.downloader.youtube import _get_format

        fmt = _get_format()
        # Default should prefer 251 like CLI example
        assert "251" in fmt


def test_ip_protection_rate_limit_env():
    # Ensure rate limit can be disabled
    with patch.dict(os.environ, {"YTDLP_ENABLE_RATE_LIMIT": "false"}):
        from music_bot.downloader.youtube import _enforce_rate_limit

        # Should not sleep when disabled
        import time

        start = time.monotonic()
        _enforce_rate_limit()
        elapsed = time.monotonic() - start
        assert elapsed < 0.5


def test_ydl_options_respects_env_overrides():
    with patch.dict(
        os.environ,
        {
            "YTDLP_SOCKET_TIMEOUT": "60",
            "YTDLP_RETRIES": "5",
            "YTDLP_SLEEP_INTERVAL": "2.5",
            "YTDLP_GEO_BYPASS": "false",
            "YTDLP_FORCE_IPV4": "true",
        },
    ):
        opts = _get_base_ydl_options()
        assert opts["socket_timeout"] == 60
        assert opts["retries"] == 5
        assert abs(opts["sleep_interval"] - 2.5) < 0.001
        assert opts["geo_bypass"] is False
        assert opts["force_ipv4"] is True
