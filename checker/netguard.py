"""Outbound network guard for the public deployment.

On a public server, "audit this URL" would otherwise let anyone make the
server fetch addresses on its own private network (cloud metadata
endpoints, internal admin pages, localhost services) and read the result
back in the report. Playwright's request routing can't close that hole on
its own: redirect hops bypass page/context routes, so a public page that
302s to 169.254.169.254 would sail through.

Instead, every connection the headless browser (and the crawler's
robots.txt/sitemap fetches) makes is sent through a tiny local HTTP proxy.
The proxy resolves each destination itself, refuses anything that isn't a
globally routable address, and then connects to the exact IP it checked -
so redirects, iframes, fetch/XHR, WebSockets and DNS-rebinding tricks all
hit the same check. Only used when PUBLIC_MODE is on.
"""
import ipaddress
import select
import socket
import threading
from urllib.parse import urlsplit

MAX_HEADER_BYTES = 64 * 1024
BLOCKED_HEADER = "X-A11y-Auditor-Blocked"
BLOCKED_MESSAGE = ("Blocked: this page (or a redirect from it) points to a private or "
                   "internal network address, which the public version of this tool can't audit.")
CONNECT_TIMEOUT = 10
IDLE_TIMEOUT = 60


class BlockedAddress(Exception):
    pass


def _ip_is_public(ip):
    addr = ipaddress.ip_address(ip.split("%", 1)[0])
    if addr.version == 6 and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    return addr.is_global and not addr.is_multicast


def resolve_public(host, port):
    """Resolves `host` and returns one of its IPs, or raises BlockedAddress
    if it doesn't resolve or ANY of its addresses is private/internal
    (rejecting mixed answers stops a name from smuggling in an internal IP
    alongside a public one)."""
    host = (host or "").strip("[]")
    if not host:
        raise BlockedAddress("No host name in URL.")
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror:
        raise BlockedAddress(f"Could not resolve {host}.")
    ips = [info[4][0] for info in infos]
    if not ips:
        raise BlockedAddress(f"Could not resolve {host}.")
    for ip in ips:
        if not _ip_is_public(ip):
            raise BlockedAddress(
                f"{host} points to a private or internal network address, "
                f"which the public version of this tool can't audit."
            )
    return ips[0]


def check_url(url):
    """Friendly up-front check for user-entered URLs, so a blocked address
    gets a clear error instead of a generic browser connection failure."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise BlockedAddress("Only http:// and https:// URLs can be audited.")
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError:
        raise BlockedAddress(f"Invalid port in {url}.")
    resolve_public(parts.hostname, port)


# ---------------------------------------------------------------------------
# Filtering proxy
# ---------------------------------------------------------------------------

def _read_head(conn):
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = conn.recv(8192)
        if not chunk:
            return None, b""
        data += chunk
        if len(data) > MAX_HEADER_BYTES:
            return None, b""
    head, _, rest = data.partition(b"\r\n\r\n")
    return head.decode("latin-1"), rest


def _pipe(a, b):
    socks = [a, b]
    while True:
        readable, _, errored = select.select(socks, [], socks, IDLE_TIMEOUT)
        if errored or not readable:
            return
        for s in readable:
            data = s.recv(65536)
            if not data:
                return
            (b if s is a else a).sendall(data)


def _split_host_port(hostport, default_port):
    if hostport.startswith("["):
        host, _, port = hostport[1:].partition("]")
        port = port.lstrip(":")
    else:
        host, _, port = hostport.rpartition(":") if hostport.count(":") == 1 else (hostport, "", "")
    return host, int(port) if port else default_port


def _handle(client):
    upstream = None
    try:
        client.settimeout(CONNECT_TIMEOUT)
        head, rest = _read_head(client)
        if not head:
            return
        lines = head.split("\r\n")
        method, target, version = lines[0].split(" ", 2)

        if method.upper() == "CONNECT":
            host, port = _split_host_port(target, 443)
        else:
            parts = urlsplit(target)
            if parts.scheme != "http" or not parts.hostname:
                client.sendall(b"HTTP/1.1 400 Bad Request\r\nConnection: close\r\n\r\n")
                return
            host, port = parts.hostname, parts.port or 80

        try:
            ip = resolve_public(host, port)
        except BlockedAddress:
            client.sendall(f"HTTP/1.1 403 Forbidden\r\n{BLOCKED_HEADER}: 1\r\n"
                           f"Content-Length: 0\r\nConnection: close\r\n\r\n".encode())
            return

        upstream = socket.create_connection((ip, port), timeout=CONNECT_TIMEOUT)

        if method.upper() == "CONNECT":
            client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            if rest:
                upstream.sendall(rest)
        else:
            # Plain HTTP: rewrite to origin-form and force one request per
            # connection, so a reused proxy connection can never carry a
            # second request to a different (unchecked) host.
            path = parts.path or "/"
            if parts.query:
                path += "?" + parts.query
            headers = [h for h in lines[1:]
                       if not h.lower().startswith(("proxy-", "connection:", "keep-alive:"))]
            headers.append("Connection: close")
            request = f"{method} {path} {version}\r\n" + "\r\n".join(headers) + "\r\n\r\n"
            upstream.sendall(request.encode("latin-1") + rest)

        client.settimeout(None)
        upstream.settimeout(None)
        _pipe(client, upstream)
    except (OSError, ValueError):
        pass
    finally:
        for s in (client, upstream):
            if s is not None:
                try:
                    s.close()
                except OSError:
                    pass


class _GuardProxy:
    def __init__(self):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(128)
        self.url = f"http://127.0.0.1:{self._sock.getsockname()[1]}"
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while True:
            client, _ = self._sock.accept()
            threading.Thread(target=_handle, args=(client,), daemon=True).start()


_proxy = None
_proxy_lock = threading.Lock()


def proxy_url():
    """Starts the filtering proxy on first use and returns its URL."""
    global _proxy
    with _proxy_lock:
        if _proxy is None:
            _proxy = _GuardProxy()
        return _proxy.url
