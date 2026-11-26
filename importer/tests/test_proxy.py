"""Proxy settings: URL handling, what each tool is given, and the firewall rules."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from vsimporter.adapters import RealAppClient, RealFetcher, RealYtDlp, real_adapters
from vsimporter.config import Settings
from vsimporter.proxy import ProxyConfigError, build_rules, masked, normalize, subprocess_env


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", None),
        ("  ", None),
        ("http://proxy:3128", "http://proxy:3128"),
        ("http://proxy", "http://proxy:80"),
        ("HTTPS://Proxy.example:8443/", "https://proxy.example:8443"),
        # Local DNS would reveal what is being fetched, so socks5 always resolves on the proxy.
        ("socks5://10.0.0.2:1080", "socks5h://10.0.0.2:1080"),
        ("socks5h://gluetun:1080", "socks5h://gluetun:1080"),
        ("socks5://us%40er:p%3Ass@h:1080", "socks5h://us%40er:p%3Ass@h:1080"),
        ("http://[fd00::1]:3128", "http://[fd00::1]:3128"),
    ],
)
def test_normalize(raw, expected):
    assert normalize(raw) == expected


@pytest.mark.parametrize(
    "raw", ["proxy:3128", "ftp://proxy:21", "socks4://p:1080", "http://:3128", "http://p:99999", "http://p:3128/x"]
)
def test_normalize_rejects(raw):
    with pytest.raises(ProxyConfigError):
        normalize(raw)


def test_bad_proxy_url_stops_settings(tmp_path):
    with pytest.raises(ValidationError):
        Settings(proxy_url="socks4://p:1080", data_dir=tmp_path)


def test_masked_hides_credentials():
    assert masked("socks5h://user:secret@host:1080") == "socks5h://***@host:1080"
    assert masked("http://host:3128") == "http://host:3128"
    assert masked(None) is None


def test_ytdlp_gets_proxy_and_native_downloader(tmp_path):
    s = Settings(proxy_url="socks5://p:1080", data_dir=tmp_path, ytdlp_cookies_file="")
    base = RealYtDlp(s)._base()
    i = base.index("--proxy")
    assert base[i + 1] == "socks5h://p:1080"
    assert base[base.index("--downloader") + 1] == "native"
    env = RealYtDlp(s)._env
    assert env["HTTPS_PROXY"] == env["ALL_PROXY"] == "socks5h://p:1080"


def test_no_proxy_means_no_proxy_args(tmp_path):
    s = Settings(data_dir=tmp_path, ytdlp_cookies_file="")
    assert "--proxy" not in RealYtDlp(s)._base()
    assert RealYtDlp(s)._env is None
    assert subprocess_env(None) is None


def test_fetcher_uses_proxy_and_app_client_does_not(tmp_path, monkeypatch):
    # Stray proxy variables in the environment must not change either client.
    monkeypatch.setenv("HTTPS_PROXY", "http://stray:1")
    monkeypatch.setenv("HTTP_PROXY", "http://stray:1")
    s = Settings(proxy_url="http://p:3128", data_dir=tmp_path)
    ad = real_adapters(s)
    assert isinstance(ad.fetcher, RealFetcher) and isinstance(ad.app, RealAppClient)

    def proxy_of(client, url):
        transport = client._transport_for_url(__import__("httpx").URL(url))
        pool = getattr(transport, "_pool", None)
        proxy = getattr(pool, "_proxy_url", None)
        return None if proxy is None else bytes(proxy.host).decode() + ":" + str(proxy.port)

    assert proxy_of(ad.fetcher.client, "https://i.ytimg.com/x.jpg") == "p:3128"
    assert proxy_of(ad.app.client, "http://app:8080/api/internal/exists") is None
    plain = real_adapters(Settings(data_dir=tmp_path))
    assert proxy_of(plain.fetcher.client, "https://i.ytimg.com/x.jpg") is None


def test_firewall_rules_default_deny():
    rules = build_rules(["10.0.0.2"], 1080, ["172.20.0.0/16"])
    flat = [" ".join(r) for r in rules]
    assert flat[0] == "-P OUTPUT ACCEPT" and flat[1] == "-F OUTPUT"
    assert "-A OUTPUT -o lo -j ACCEPT" in flat
    assert "-A OUTPUT -d 172.20.0.0/16 -j ACCEPT" in flat
    assert "-A OUTPUT -d 10.0.0.2 -p tcp --dport 1080 -j ACCEPT" in flat
    # Everything else is rejected, and the policy drops whatever slips past.
    assert flat[-3:] == [
        "-A OUTPUT -p tcp -j REJECT --reject-with tcp-reset",
        "-A OUTPUT -j REJECT",
        "-P OUTPUT DROP",
    ]


def test_firewall_rules_without_proxy_address_block_all_but_local():
    flat = [" ".join(r) for r in build_rules([], 1080, [])]
    assert not any("--dport" in r for r in flat)
    assert flat[-1] == "-P OUTPUT DROP"


def test_health_reports_proxy(svc):
    from fastapi.testclient import TestClient

    from vsimporter.api import create_app

    with TestClient(create_app(svc, background=False)) as client:
        body = client.get("/api/health").json()
    assert body["proxy"] == {"enabled": False, "url": None, "firewall": False}
