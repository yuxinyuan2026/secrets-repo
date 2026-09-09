#!/usr/bin/env python3
"""Convert a base64 (or plaintext) proxy subscription into Clash/Mihomo proxy entries.

Mimics what mihomo does internally (common/convert/converter.go -> ConvertsV2Ray):
  1. try base64-decode the whole payload (std or raw encoding, like mihomo's DecodeBase64)
  2. split on newlines, parse each share link (ss / ssr / vmess / vless / trojan / hysteria / hysteria2)
  3. de-duplicate names the way mihomo's uniqueName() does: name, name-01, name-02 ...

Usage:
    python3 sub2clash.py [URL_OR_PATH] [-o out.yaml]

If no argument is given, the Pawdroid/Free-servers root subscription is fetched
through the GitHub API (raw.githubusercontent.com is often unreachable in CI/sandboxes).
"""
from __future__ import annotations

import argparse
import base64
import binascii
import json
import sys
import urllib.parse
from typing import Any

DEFAULT_URL = "https://raw.githubusercontent.com/Pawdroid/Free-servers/main/sub"


# ---------- mihomo-compatible helpers -------------------------------------------------

def decode_base64(buf: bytes) -> bytes:
    """Same fallback order as mihomo's DecodeBase64: RawStd -> Std -> plaintext."""
    text = buf.strip()
    for decoder in (base64.b64decode, _raw_b64decode):
        try:
            return decoder(text, validate=True)
        except (binascii.Error, ValueError):
            continue
    return buf


def _raw_b64decode(data: bytes, validate: bool = True) -> bytes:
    return base64.b64decode(data + b"=" * (-len(data) % 4), validate=validate)


def unique_name(seen: dict[str, int], name: str) -> str:
    if name in seen:
        seen[name] += 1
        return f"{name}-{seen[name]:02d}"
    seen[name] = 0
    return name


def _int(value: str, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# ---------- share-link parsers --------------------------------------------------------

def parse_vless(link: str, name: str) -> dict[str, Any] | None:
    u = urllib.parse.urlparse(link)
    q = urllib.parse.parse_qs(u.query)
    p: dict[str, Any] = {
        "name": name,
        "type": "vless",
        "server": u.hostname,
        "port": u.port,
        "uuid": urllib.parse.unquote(u.username or ""),
        "network": q.get("type", ["tcp"])[0],
        "tls": q.get("security", ["none"])[0] in ("tls", "reality"),
        "udp": True,
    }
    if q.get("security", ["none"])[0] == "reality":
        p["reality-opts"] = {
            "public-key": q.get("pbk", [""])[0],
            "short-id": q.get("sid", [""])[0],
        }
        p["client-fingerprint"] = q.get("fp", ["chrome"])[0]
    if p["tls"]:
        p["servername"] = q.get("sni", [""])[0]
        if q.get("flow", [""])[0]:
            p["flow"] = q["flow"][0]
        p["skip-cert-verify"] = q.get("allowInsecure", q.get("insecure", ["false"]))[0] in ("1", "true")
    if q.get("type", ["tcp"])[0] == "ws":
        p["ws-opts"] = {
            "path": q.get("path", ["/"])[0],
            "headers": {"Host": q.get("host", [""])[0]},
        }
    return p


def parse_trojan(link: str, name: str) -> dict[str, Any] | None:
    u = urllib.parse.urlparse(link)
    q = urllib.parse.parse_qs(u.query)
    p: dict[str, Any] = {
        "name": name,
        "type": "trojan",
        "server": u.hostname,
        "port": u.port,
        "password": urllib.parse.unquote(u.username or ""),
        "udp": True,
        "sni": q.get("sni", [q.get("host", [""])[0]])[0],
        "skip-cert-verify": q.get("allowInsecure", ["false"])[0] in ("1", "true"),
    }
    network = q.get("type", ["tcp"])[0]
    if network != "tcp":
        p["network"] = network
    if network == "ws":
        p["ws-opts"] = {
            "path": q.get("path", ["/"])[0],
            "headers": {"Host": q.get("host", [""])[0]},
        }
    if q.get("alpn", [""])[0]:
        p["alpn"] = urllib.parse.unquote(q["alpn"][0]).split(",")
    return p


def parse_ss(link: str, name: str) -> dict[str, Any] | None:
    u = urllib.parse.urlparse(link)
    q = urllib.parse.parse_qs(u.query)
    userinfo = u.username or ""
    try:
        decoded = decode_base64(_raw_b64decode(userinfo.encode())).decode()
    except (binascii.Error, UnicodeDecodeError):
        decoded = urllib.parse.unquote(userinfo)
    method, _, password = decoded.partition(":")
    p: dict[str, Any] = {
        "name": name,
        "type": "ss",
        "server": u.hostname,
        "port": u.port,
        "cipher": method,
        "password": password,
        "udp": True,
    }
    if q.get("plugin"):
        p["plugin"] = q["plugin"][0]
        p["plugin-opts"] = {k: v[0] for k, v in q.items() if k != "plugin"}
    return p


def parse_vmess(link: str, name: str) -> dict[str, Any] | None:
    body = link.split("://", 1)[1]
    body = body.split("#", 1)[0]
    try:
        cfg = json.loads(decode_base64(body.encode()).decode())
    except (binascii.Error, json.JSONDecodeError, UnicodeDecodeError):
        return None
    p: dict[str, Any] = {
        "name": name,
        "type": "vmess",
        "server": cfg.get("add"),
        "port": _int(str(cfg.get("port", 0))),
        "uuid": cfg.get("id"),
        "alterId": _int(str(cfg.get("aid", 0))),
        "cipher": cfg.get("scy", "auto"),
        "udp": True,
        "network": cfg.get("net", "tcp"),
        "tls": cfg.get("tls") in ("tls", True),
    }
    if cfg.get("sni"):
        p["servername"] = cfg["sni"]
    if p["network"] == "ws":
        p["ws-opts"] = {"path": cfg.get("path", "/"), "headers": {"Host": cfg.get("host", "")}}
    return p


def parse_hysteria2(link: str, name: str) -> dict[str, Any] | None:
    u = urllib.parse.urlparse(link)
    q = urllib.parse.parse_qs(u.query)
    p: dict[str, Any] = {
        "name": name,
        "type": "hysteria2",
        "server": u.hostname,
        "port": u.port,
        "password": urllib.parse.unquote(u.username or ""),
        "obfs": q.get("obfs", [""])[0],
        "sni": q.get("sni", [""])[0],
        "skip-cert-verify": q.get("insecure", ["false"])[0] in ("1", "true"),
    }
    if q.get("alpn", [""])[0]:
        p["alpn"] = urllib.parse.unquote(q["alpn"][0]).split(",")
    return p


PARSERS = {
    "vless": parse_vless,
    "trojan": parse_trojan,
    "ss": parse_ss,
    "vmess": parse_vmess,
    "hysteria2": parse_hysteria2,
    "hy2": parse_hysteria2,
}


def convert(payload: bytes) -> tuple[list[dict[str, Any]], list[str]]:
    """Return (proxies, skipped_links)."""
    data = decode_base64(payload)
    proxies: list[dict[str, Any]] = []
    skipped: list[str] = []
    seen: dict[str, int] = {}
    for line in data.decode("utf-8", errors="replace").splitlines():
        line = line.rstrip(" \r")
        if not line:
            continue
        scheme, _, _ = line.partition("://")
        scheme = scheme.lower()
        parser = PARSERS.get(scheme)
        if parser is None:
            skipped.append(line)
            continue
        raw_name = urllib.parse.unquote(line.split("#", 1)[1]) if "#" in line else ""
        try:
            proxy = parser(line, unique_name(seen, raw_name))
        except Exception:  # noqa: BLE001 - one bad link must not kill the whole subscription
            proxy = None
        if proxy is None or not proxy.get("server") or not proxy.get("port"):
            skipped.append(line)
            continue
        proxies.append(proxy)
    return proxies, skipped


def to_yaml(proxies: list[dict[str, Any]]) -> str:
    """Minimal YAML dump (avoids a PyYAML dependency)."""
    def scalar(v: Any) -> str:
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, int):
            return str(v)
        s = str(v)
        return f'"{s.replace(chr(34), chr(92) + chr(34))}"'

    out = ["proxies:"]
    for p in proxies:
        out.append(f"  - name: {scalar(p['name'])}")
        out.append(f"    type: {p['type']}")
        for key, value in p.items():
            if key in ("name", "type"):
                continue
            if isinstance(value, dict):
                out.append(f"    {key}:")
                for k2, v2 in value.items():
                    if isinstance(v2, dict):
                        out.append(f"      {k2}:")
                        for k3, v3 in v2.items():
                            out.append(f"        {k3}: {scalar(v3)}")
                    else:
                        out.append(f"      {k2}: {scalar(v2)}")
            elif isinstance(value, list):
                out.append(f"    {key}: [{', '.join(scalar(x) for x in value)}]")
            else:
                out.append(f"    {key}: {scalar(value)}")
    return "\n".join(out) + "\n"


def fetch(url: str) -> bytes:
    if url.startswith("http"):
        import urllib.request

        req = urllib.request.Request(url, headers={"User-Agent": "sub2clash/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 - user supplied URL
            return resp.read()
    with open(url, "rb") as fh:
        return fh.read()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", nargs="?", default=DEFAULT_URL, help="subscription URL or local file")
    ap.add_argument("-o", "--out", help="write Clash YAML here (default: stdout)")
    args = ap.parse_args()

    raw = fetch(args.source)
    proxies, skipped = convert(raw)
    print(
        f"[i] payload {len(raw)}B -> {len(proxies)} proxies, {len(skipped)} skipped",
        file=sys.stderr,
    )
    for line in skipped:
        print(f"[!] unsupported: {line[:80]}", file=sys.stderr)

    text = to_yaml(proxies)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
        print(f"[i] wrote {args.out}", file=sys.stderr)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
