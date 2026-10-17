"""Sonda RTSP mínima: DESCRIBE com autenticação Basic/Digest (RFC 2326 + RFC 7616) e leitura do SDP.

Somente leitura: não faz SETUP/PLAY, não abre mídia. Mensagens de resultado nunca contêm a URL.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import os
import re
import time
from urllib.parse import unquote, urlsplit

from nvr.cameras.domain import MediaTrack, ProbeOutcome, ProbeResult, StreamProfile

_STATIC_PAYLOADS = {0: ("PCMU", 8000), 8: ("PCMA", 8000), 26: ("JPEG", 90000), 33: ("MP2T", 90000)}
_AUTH_PARAM = re.compile(r'(\w+)=(?:"([^"]*)"|([^,\s]+))')
_MAX_HEADER = 64 * 1024
_MAX_BODY = 256 * 1024


class _Response:
    def __init__(self, status: int, reason: str, headers: list[tuple[str, str]], body: bytes) -> None:
        self.status, self.reason, self.headers, self.body = status, reason, headers, body

    def header_values(self, name: str) -> list[str]:
        return [v for k, v in self.headers if k.lower() == name.lower()]


class _RtspError(Exception):
    pass


async def _read_response(reader: asyncio.StreamReader) -> _Response:
    head = await reader.readuntil(b"\r\n\r\n")
    if len(head) > _MAX_HEADER:
        raise _RtspError("cabeçalho grande demais")
    lines = head.decode("latin-1").split("\r\n")
    status_line = lines[0].split(" ", 2)
    if len(status_line) < 2 or not status_line[0].startswith("RTSP/"):
        raise _RtspError("resposta não é RTSP")
    headers = [(k.strip(), v.strip()) for k, _, v in (line.partition(":") for line in lines[1:] if line)]
    length = next((int(v) for k, v in headers if k.lower() == "content-length"), 0)
    if length > _MAX_BODY:
        raise _RtspError("corpo grande demais")
    body = await reader.readexactly(length) if length else b""
    reason = status_line[2] if len(status_line) > 2 else ""
    return _Response(int(status_line[1]), reason, headers, body)


def _digest_header(challenge: str, username: str, password: str, method: str, uri: str) -> str:
    params = {
        m.group(1).lower(): m.group(2) if m.group(2) is not None else m.group(3)
        for m in _AUTH_PARAM.finditer(challenge)
    }
    realm, nonce = params.get("realm", ""), params.get("nonce", "")
    algorithm = params.get("algorithm", "MD5").upper()
    hash_fn = hashlib.sha256 if algorithm.startswith("SHA-256") else hashlib.md5

    def h(data: str) -> str:
        return hash_fn(data.encode()).hexdigest()

    ha1 = h(f"{username}:{realm}:{password}")
    ha2 = h(f"{method}:{uri}")
    parts = [f'username="{username}"', f'realm="{realm}"', f'nonce="{nonce}"', f'uri="{uri}"']
    qop_options = [q.strip() for q in params.get("qop", "").split(",") if q.strip()]
    if "auth" in qop_options:
        cnonce, nc = os.urandom(8).hex(), "00000001"
        response = h(f"{ha1}:{nonce}:{nc}:{cnonce}:auth:{ha2}")
        parts += ["qop=auth", f"nc={nc}", f'cnonce="{cnonce}"']
    else:
        response = h(f"{ha1}:{nonce}:{ha2}")
    parts.append(f'response="{response}"')
    if "opaque" in params:
        parts.append(f'opaque="{params["opaque"]}"')
    if "algorithm" in params:
        parts.append(f"algorithm={params['algorithm']}")
    return "Digest " + ", ".join(parts)


def _authorization(resp: _Response, username: str, password: str, uri: str) -> str | None:
    challenges = resp.header_values("WWW-Authenticate")
    digest = next((c for c in challenges if c.lower().startswith("digest")), None)
    if digest:
        return _digest_header(digest[len("digest") :], username, password, "DESCRIBE", uri)
    if any(c.lower().startswith("basic") for c in challenges):
        return "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()
    return None


def parse_sdp(sdp: str) -> tuple[MediaTrack, ...]:
    tracks: list[MediaTrack] = []
    kind: str | None = None
    payloads: list[int] = []
    rtpmap: dict[int, tuple[str, int | None]] = {}

    def flush() -> None:
        if kind is None or not payloads:
            return
        pt = payloads[0]
        codec: str
        rate: int | None
        if pt in rtpmap:
            codec, rate = rtpmap[pt]
        elif pt in _STATIC_PAYLOADS:
            codec, rate = _STATIC_PAYLOADS[pt]
        else:
            codec, rate = f"PT{pt}", None
        tracks.append(MediaTrack(kind, codec.upper(), rate))

    for line in sdp.splitlines():
        if line.startswith("m="):
            flush()
            fields = line[2:].split()
            kind = fields[0] if fields else None
            payloads = [int(p) for p in fields[3:] if p.isdigit()]
            rtpmap = {}
        elif line.startswith("a=rtpmap:") and kind is not None:
            pt_text, _, encoding = line[len("a=rtpmap:") :].partition(" ")
            name, _, rest = encoding.partition("/")
            rate_text = rest.split("/")[0]
            if pt_text.isdigit():
                rtpmap[int(pt_text)] = (name, int(rate_text) if rate_text.isdigit() else None)
    flush()
    return tuple(tracks)


class RtspDescribeProbe:
    def __init__(self, timeout: float = 6.0) -> None:
        self._timeout = timeout

    async def probe(self, url: str, profile: StreamProfile) -> ProbeResult:
        started = time.monotonic()
        try:
            return await asyncio.wait_for(self._probe(url, profile, started), self._timeout)
        except TimeoutError:
            return ProbeResult(profile, ProbeOutcome.OFFLINE, "sem resposta (tempo esgotado)")
        except (ConnectionRefusedError, ConnectionResetError):
            return ProbeResult(profile, ProbeOutcome.OFFLINE, "conexão recusada")
        except OSError as exc:
            return ProbeResult(profile, ProbeOutcome.OFFLINE, f"falha de rede ({type(exc).__name__})")
        except (_RtspError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, ValueError) as exc:
            return ProbeResult(
                profile, ProbeOutcome.PROTOCOL_ERROR, f"resposta RTSP inválida ({type(exc).__name__})"
            )

    async def _probe(self, url: str, profile: StreamProfile, started: float) -> ProbeResult:
        parts = urlsplit(url)
        username = unquote(parts.username or "")
        password = unquote(parts.password or "")
        port = parts.port or 554
        netloc = f"[{parts.hostname}]" if parts.hostname and ":" in parts.hostname else parts.hostname or ""
        # Caminho e query exatamente como cadastrados (urlunsplit perderia o "?" final que câmeras
        # Xiongmai usam em ".../stream=0.sdp?").
        after_netloc = url.split("://", 1)[1]
        rest = after_netloc[len(after_netloc.split("/", 1)[0]) :] or "/"
        request_uri = f"rtsp://{netloc}:{port}{rest}"

        reader, writer = await asyncio.open_connection(parts.hostname, port)
        try:
            resp = await self._describe(reader, writer, request_uri, cseq=1, authorization=None)
            if resp.status == 401 and username:
                auth = _authorization(resp, username, password, request_uri)
                if auth:
                    if reader.at_eof():
                        writer.close()
                        reader, writer = await asyncio.open_connection(parts.hostname, port)
                    resp = await self._describe(reader, writer, request_uri, cseq=2, authorization=auth)
        finally:
            writer.close()
        latency = int((time.monotonic() - started) * 1000)

        if resp.status == 200:
            tracks = parse_sdp(resp.body.decode("utf-8", errors="replace"))
            summary = ", ".join(f"{t.kind} {t.codec}" for t in tracks) or "sem faixas no SDP"
            return ProbeResult(profile, ProbeOutcome.ONLINE, summary, tracks, latency)
        if resp.status == 401:
            return ProbeResult(
                profile, ProbeOutcome.AUTH_ERROR, "a câmera recusou usuário/senha", (), latency
            )
        if resp.status == 404:
            return ProbeResult(
                profile, ProbeOutcome.NOT_FOUND, "caminho RTSP não encontrado na câmera", (), latency
            )
        return ProbeResult(
            profile, ProbeOutcome.PROTOCOL_ERROR, f"a câmera respondeu {resp.status}", (), latency
        )

    @staticmethod
    async def _describe(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        uri: str,
        cseq: int,
        authorization: str | None,
    ) -> _Response:
        lines = [
            f"DESCRIBE {uri} RTSP/1.0",
            f"CSeq: {cseq}",
            "Accept: application/sdp",
            "User-Agent: nvr-probe/1",
        ]
        if authorization:
            lines.append(f"Authorization: {authorization}")
        writer.write(("\r\n".join(lines) + "\r\n\r\n").encode("latin-1"))
        await writer.drain()
        return await _read_response(reader)
