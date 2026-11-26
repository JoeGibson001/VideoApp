"""Outbound proxy support and the firewall kill switch.

When PROXY_URL is set, every request the Importer makes to the internet goes through that proxy:

- yt-dlp gets `--proxy` (and native downloaders, so ffmpeg never fetches anything itself),
- poster/avatar downloads use an httpx client bound to the proxy,
- the yt-dlp self-update (pip) in the entrypoint uses `--proxy`.

On top of that the entrypoint (still root) runs `python -m vsimporter.proxy firewall`, which sets
iptables rules in the container's own network namespace: outgoing traffic is only allowed to the
proxy, to loopback and to the container's own Docker networks (the App). Anything else is rejected,
so a tool that ignores the proxy fails instead of leaking. If the rules cannot be applied (the
container lacks NET_ADMIN), the container refuses to start.

This module only uses the standard library so the entrypoint can run it before anything else.
"""

from __future__ import annotations

import ipaddress
import json
import os
import socket
import subprocess
import sys
from urllib.parse import quote, unquote, urlsplit

SCHEMES = {"http": 80, "https": 443, "socks5": 1080, "socks5h": 1080}


class ProxyConfigError(ValueError):
    pass


def normalize(url: str | None) -> str | None:
    """Validate PROXY_URL and return the form every tool should use, or None when unset.

    socks5:// becomes socks5h:// so host names are resolved by the proxy, never by local DNS.
    """
    url = (url or "").strip()
    if not url:
        return None
    if "://" not in url:
        raise ProxyConfigError("PROXY_URL needs a scheme, e.g. http://host:3128 or socks5://host:1080")
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme not in SCHEMES:
        raise ProxyConfigError(f"PROXY_URL scheme must be one of {', '.join(sorted(SCHEMES))} (got {scheme!r})")
    if scheme == "socks5":
        scheme = "socks5h"
    try:
        port = parts.port or SCHEMES[scheme]
    except ValueError as e:
        raise ProxyConfigError(f"PROXY_URL has an invalid port: {e}") from None
    host = parts.hostname
    if not host:
        raise ProxyConfigError("PROXY_URL has no host")
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        raise ProxyConfigError("PROXY_URL must be scheme://[user:pass@]host:port with nothing after the port")
    auth = ""
    if parts.username is not None:
        auth = quote(unquote(parts.username), safe="")
        if parts.password is not None:
            auth += ":" + quote(unquote(parts.password), safe="")
        auth += "@"
    host_part = f"[{host}]" if ":" in host else host
    return f"{scheme}://{auth}{host_part}:{port}"


def host_port(url: str) -> tuple[str, int]:
    parts = urlsplit(url)
    return parts.hostname or "", parts.port or SCHEMES[parts.scheme]


def masked(url: str | None) -> str | None:
    """The proxy URL without credentials, safe to show and log."""
    if not url:
        return None
    parts = urlsplit(url)
    host, port = host_port(url)
    host_part = f"[{host}]" if ":" in host else host
    user = "***@" if parts.username is not None else ""
    return f"{parts.scheme}://{user}{host_part}:{port}"


def subprocess_env(url: str | None) -> dict[str, str] | None:
    """Environment for child processes (yt-dlp and the deno it runs): proxy variables set."""
    if not url:
        return None
    env = dict(os.environ)
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        env[k] = url
    env["NO_PROXY"] = env["no_proxy"] = "localhost,127.0.0.1,::1"
    return env


# ---------------------------------------------------------------- kill switch


def build_rules(proxy_ips: list[str], port: int, local_nets: list[str]) -> list[list[str]]:
    """iptables/ip6tables arguments (without the binary) for one address family."""
    rules = [
        ["-P", "OUTPUT", "ACCEPT"],
        ["-F", "OUTPUT"],
        ["-A", "OUTPUT", "-o", "lo", "-j", "ACCEPT"],
        ["-A", "OUTPUT", "-m", "conntrack", "--ctstate", "ESTABLISHED,RELATED", "-j", "ACCEPT"],
    ]
    for net in local_nets:
        rules.append(["-A", "OUTPUT", "-d", net, "-j", "ACCEPT"])
    for ip in proxy_ips:
        rules.append(["-A", "OUTPUT", "-d", ip, "-p", "tcp", "--dport", str(port), "-j", "ACCEPT"])
    rules += [
        ["-A", "OUTPUT", "-p", "tcp", "-j", "REJECT", "--reject-with", "tcp-reset"],
        ["-A", "OUTPUT", "-j", "REJECT"],
        ["-P", "OUTPUT", "DROP"],
    ]
    return rules


def _log(level: str, msg: str, **extra: object) -> None:
    # Errors go to stderr: the entrypoint captures stdout of "normalize".
    out = sys.stderr if level == "ERROR" else sys.stdout
    print(json.dumps({"level": level, "logger": "firewall", "msg": msg, **extra}), file=out, flush=True)


def _local_networks() -> tuple[list[str], list[str]]:
    """The container's own Docker networks (where the App lives), per address family."""
    out = subprocess.run(["ip", "-j", "addr", "show"], check=True, capture_output=True, text=True).stdout
    v4: list[str] = []
    v6: list[str] = []
    for iface in json.loads(out):
        if iface.get("ifname") == "lo":
            continue
        for a in iface.get("addr_info", []):
            if a.get("scope") != "global":
                continue
            net = ipaddress.ip_interface(f"{a['local']}/{a['prefixlen']}").network
            (v4 if net.version == 4 else v6).append(str(net))
    return v4, v6


def _resolve(host: str, port: int) -> tuple[list[str], list[str]]:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise ProxyConfigError(f"cannot resolve the proxy host {host!r}: {e}") from None
    v4 = sorted({i[4][0] for i in infos if i[0] == socket.AF_INET})
    v6 = sorted({i[4][0] for i in infos if i[0] == socket.AF_INET6})
    return v4, v6


def _apply(binaries: list[str], rules: list[list[str]]) -> str:
    """Apply the rules with the first binary that works (nft backend, then legacy)."""
    last = ""
    for binary in binaries:
        try:
            for r in rules:
                subprocess.run([binary, "-w", *r], check=True, capture_output=True, text=True)
            listing = subprocess.run([binary, "-w", "-S", "OUTPUT"], check=True, capture_output=True, text=True).stdout
            if "-P OUTPUT DROP" not in listing:
                raise RuntimeError("default policy did not stick")
            return binary
        except (OSError, subprocess.CalledProcessError, RuntimeError) as e:
            last = getattr(e, "stderr", None) or str(e)
            try:
                subprocess.run([binary, "-w", "-P", "OUTPUT", "DROP"], capture_output=True)
            except OSError:
                pass
    raise RuntimeError(last.strip() or "iptables failed")


def firewall(url: str) -> None:
    host, port = host_port(url)
    p4, p6 = _resolve(host, port)
    n4, n6 = _local_networks()
    try:
        used = _apply(["iptables", "iptables-legacy"], build_rules(p4, port, n4))
    except RuntimeError as e:
        raise RuntimeError(
            "could not set the firewall that stops traffic bypassing the proxy "
            f"({e}). Give the importer container the NET_ADMIN capability (cap_add: [NET_ADMIN]), "
            "or set PROXY_KILL_SWITCH=false to run without it."
        ) from None
    try:
        _apply(["ip6tables", "ip6tables-legacy"], build_rules(p6, port, n6))
        v6 = "blocked except proxy"
    except RuntimeError as e:
        if n6:
            raise RuntimeError(f"the container has IPv6 but ip6tables failed ({e})") from None
        v6 = "no IPv6 in this container"
    _log("INFO", "firewall active: outgoing traffic only to the proxy and the container's own networks",
         proxy=masked(url), proxy_ips=p4 + p6, local_networks=n4 + n6, backend=used, ipv6=v6)


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else ""
    try:
        url = normalize(os.environ.get("PROXY_URL"))
        if cmd == "normalize":
            print(url or "")
            return 0
        if cmd == "firewall":
            if not url:
                return 0
            firewall(url)
            return 0
    except (ProxyConfigError, RuntimeError, OSError, subprocess.CalledProcessError) as e:
        _log("ERROR", f"proxy setup failed, refusing to start: {e}")
        return 1
    print("usage: python -m vsimporter.proxy normalize|firewall", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
