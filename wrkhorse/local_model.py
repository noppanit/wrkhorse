"""Where a "local" model is allowed to live.

The home deployment must never send prompts to a cloud endpoint. Until now
that was enforced by hard-coding ``http://127.0.0.1:11434/v1`` (Ollama on the
same Mac). That blocks two real setups:

* a different local server on the same machine (LM Studio :1234, llama.cpp
  or ``mlx_lm.server`` :8080, vLLM :8000), and
* a model on *another* machine the owner controls (a second Mac, a Linux GPU
  box) reachable over the home LAN or a Tailscale tailnet.

Policy (fail closed):

* loopback IP literals (``127.0.0.0/8``, ``::1``) on any port are always local;
* private-network addresses are accepted **only** with
  ``WRKHORSE_ALLOW_LAN_MODEL=1``: RFC 1918, Tailscale/CGNAT ``100.64.0.0/10``
  and IPv6 ULA ``fc00::/7``. Link-local (incl. ``169.254.169.254`` cloud
  metadata), multicast, unspecified and public addresses are always refused;
* hostnames are accepted only with the LAN opt-in, only under private-use
  suffixes (``.local``, ``.lan``, ``.home.arpa``, ``.internal``, ``.ts.net``)
  and only if *every* address they resolve to passes the IP rule above;
* ``localhost`` is refused: on macOS it may resolve to ``::1`` first while
  Ollama listens on ``127.0.0.1`` only. Use the IP literal.

The path must be the OpenAI-compatible ``/v1`` root. No credentials, query or
fragment. Ollama ``:cloud`` model tags are refused because Ollama forwards
them to ollama.com.
"""

from __future__ import annotations

import ipaddress
import re
import socket
from urllib.parse import urlsplit, urlunsplit

DEFAULT_BASE_URL = "http://127.0.0.1:11434/v1"
DUMMY_API_KEY = "ollama-local"
LAN_SUFFIXES = (".local", ".lan", ".home.arpa", ".internal", ".ts.net")
_CGNAT = ipaddress.ip_network("100.64.0.0/10")
_HOSTNAME = re.compile(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?")
_MODEL_TAG = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,199}")


class LocalModelError(ValueError):
    """The configured model endpoint is not provably local."""


def _ip_scope(address: str) -> str | None:
    """Return "loopback", "lan" or None (refused) for one IP address."""
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return None
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if ip.is_loopback:
        return "loopback"
    if ip.is_link_local or ip.is_multicast or ip.is_unspecified or ip.is_reserved:
        return None
    if isinstance(ip, ipaddress.IPv4Address) and ip in _CGNAT:
        return "lan"
    if ip.is_private:
        return "lan"
    return None


def _resolve(host: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise LocalModelError(
            f"cannot resolve model host {host!r}; is that machine on and on the same network?"
        ) from exc
    return sorted({info[4][0] for info in infos})


def validate_model_tag(model: str | None) -> str:
    if not model or _MODEL_TAG.fullmatch(model) is None:
        raise LocalModelError("WRKHORSE_INFERENCE_MODEL must be a local model tag")
    # Ollama cloud models are tagged "name:cloud" or "name:SIZE-cloud"
    # (e.g. gpt-oss:120b-cloud). Ollama silently forwards them to ollama.com.
    tag = model.rsplit(":", 1)[-1].lower()
    if tag == "cloud" or tag.endswith("-cloud"):
        raise LocalModelError("Ollama cloud model tags run off-machine and are refused")
    return model


def validate_base_url(url: str, *, allow_lan: bool, resolve: bool = True) -> tuple[str, str]:
    """Return ``(normalised_base_url, scope)`` or raise LocalModelError.

    ``scope`` is "loopback" or "lan". With ``resolve=False`` hostnames are only
    checked syntactically (used at request time; config already resolved them).
    """
    parts = urlsplit((url or "").strip())
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise LocalModelError("model URL must be http(s)://HOST:PORT/v1")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise LocalModelError("model URL must not carry credentials, a query or a fragment")
    if parts.path.rstrip("/") != "/v1":
        raise LocalModelError("model URL must be the OpenAI-compatible /v1 root")
    try:
        parts.port
    except ValueError as exc:
        raise LocalModelError("model URL has an invalid port") from exc
    host = parts.hostname.lower()
    normalised = urlunsplit((parts.scheme, parts.netloc, "/v1", "", ""))

    scope = _ip_scope(host)
    if scope is None:
        try:
            ipaddress.ip_address(host.split("%", 1)[0])
            is_literal = True
        except ValueError:
            is_literal = False
        if is_literal:
            raise LocalModelError("model URL points at a public or reserved address")
        if host == "localhost":
            raise LocalModelError("use http://127.0.0.1:PORT/v1 instead of localhost")
        if not allow_lan:
            raise LocalModelError(
                "model host is not loopback; set WRKHORSE_ALLOW_LAN_MODEL=1 to use "
                "a model on another machine you control"
            )
        if _HOSTNAME.fullmatch(host) is None or not host.endswith(LAN_SUFFIXES):
            raise LocalModelError(
                "LAN model hostnames must end in one of " + ", ".join(LAN_SUFFIXES)
                + " (or use a private IP address)"
            )
        if resolve:
            addresses = _resolve(host)
            scopes = {_ip_scope(address) for address in addresses}
            if not addresses or None in scopes:
                raise LocalModelError(
                    f"model host {host!r} resolves to a non-private address; refused"
                )
        scope = "lan"
    if scope == "lan" and not allow_lan:
        raise LocalModelError(
            "model host is on the LAN; set WRKHORSE_ALLOW_LAN_MODEL=1 to allow it"
        )
    return normalised, scope
