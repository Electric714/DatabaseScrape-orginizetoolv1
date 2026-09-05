"""Public-only egress, with DNS resolution pinned to the actual connection."""
import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit

import httpx


def public_address(value: str) -> bool:
    address = ipaddress.ip_address(value)
    return address.is_global and not address.is_multicast and not (
        isinstance(address, ipaddress.IPv6Address)
        and (address.ipv4_mapped or address.sixtofour or address.teredo)
    )


async def validate_public_url(url: str) -> list[str]:
    try:
        parts = urlsplit(url)
        if (parts.scheme not in {"http", "https"} or not parts.hostname
                or parts.username is not None or parts.password is not None
                or parts.port not in {None, 80, 443} or "%" in parts.hostname
                or "\\" in url or any(ord(c) < 32 for c in url)):
            raise ValueError("Only public HTTP(S) URLs on ports 80/443 are allowed")
        answers = await asyncio.wait_for(asyncio.get_running_loop().getaddrinfo(
            parts.hostname, parts.port or (443 if parts.scheme == "https" else 80),
            type=socket.SOCK_STREAM,
        ), timeout=10)
        addresses = list(dict.fromkeys(answer[4][0] for answer in answers))
        if not addresses or not all(public_address(ip) for ip in addresses):
            raise ValueError("Destination resolves to a non-public address")
        return addresses
    except (OSError, UnicodeError) as exc:
        raise ValueError("Cannot resolve a public destination") from exc


class PublicTransport(httpx.AsyncHTTPTransport):
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        addresses = await validate_public_url(str(request.url))
        # Keep the original Host and TLS certificate name while connecting only
        # to a vetted numeric IP. A second DNS answer cannot rebind the socket.
        pinned = httpx.Request(
            request.method, request.url.copy_with(host=addresses[0]),
            headers=request.headers, stream=request.stream,
            extensions={**request.extensions, "sni_hostname": request.url.host},
        )
        return await super().handle_async_request(pinned)
