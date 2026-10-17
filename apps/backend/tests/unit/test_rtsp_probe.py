"""Sonda RTSP contra um servidor RTSP falso em processo (sem câmera, sem MediaMTX)."""

import asyncio
import base64
import hashlib
import re
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

import pytest

from nvr.cameras.adapters.rtsp_probe import RtspDescribeProbe, parse_sdp
from nvr.cameras.domain import ProbeOutcome, StreamProfile

SDP = (
    "v=0\r\no=- 0 0 IN IP4 127.0.0.1\r\ns=cam\r\nt=0 0\r\n"
    "m=video 0 RTP/AVP 96\r\na=rtpmap:96 H265/90000\r\na=control:track0\r\n"
    "m=audio 0 RTP/AVP 8\r\na=control:track1\r\n"
)
USER, PASSWORD, REALM, NONCE = "admin", "s3nh@forte", "IPC", "abc123nonce"


def _md5(s: str) -> str:
    return hashlib.md5(s.encode()).hexdigest()  # noqa: S324 - RTSP Digest usa MD5


def _expected_digest(params: dict[str, str]) -> str:
    ha1 = _md5(f"{USER}:{REALM}:{PASSWORD}")
    ha2 = _md5(f"DESCRIBE:{params['uri']}")
    if params.get("qop") == "auth":
        return _md5(f"{ha1}:{NONCE}:{params['nc']}:{params['cnonce']}:auth:{ha2}")
    return _md5(f"{ha1}:{NONCE}:{ha2}")


Handler = Callable[[str, dict[str, str]], tuple[int, list[str], str]]


def camera_handler(mode: str, path: str = "/stream1") -> Handler:
    """mode: digest | digest-qop | basic | open."""

    def handle(request_line: str, headers: dict[str, str]) -> tuple[int, list[str], str]:
        uri = request_line.split()[1]
        if not uri.endswith(path):
            return 404, [], ""
        auth = headers.get("authorization", "")
        if mode == "open":
            return 200, ["Content-Type: application/sdp"], SDP
        if mode == "basic":
            if auth == "Basic " + base64.b64encode(f"{USER}:{PASSWORD}".encode()).decode():
                return 200, ["Content-Type: application/sdp"], SDP
            return 401, [f'WWW-Authenticate: Basic realm="{REALM}"'], ""
        if auth.startswith("Digest "):
            params = dict((k, a or b) for k, a, b in re.findall(r'(\w+)=(?:"([^"]*)"|([^,\s]+))', auth))
            if params.get("uri") == uri and params.get("response") == _expected_digest(params):
                return 200, ["Content-Type: application/sdp"], SDP
        qop = ', qop="auth"' if mode == "digest-qop" else ""
        return (
            401,
            [
                f'WWW-Authenticate: Digest realm="{REALM}", nonce="{NONCE}"{qop}',
                f'WWW-Authenticate: Basic realm="{REALM}"',
            ],
            "",
        )

    return handle


@asynccontextmanager
async def fake_rtsp_server(handler: Handler) -> AsyncIterator[int]:
    async def serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while True:
                head = (await reader.readuntil(b"\r\n\r\n")).decode()
                lines = head.split("\r\n")
                headers = {
                    k.strip().lower(): v.strip() for k, _, v in (ln.partition(":") for ln in lines[1:] if ln)
                }
                status, extra, body = handler(lines[0], headers)
                reason = {200: "OK", 401: "Unauthorized", 404: "Not Found"}.get(status, "X")
                out = [
                    f"RTSP/1.0 {status} {reason}",
                    f"CSeq: {headers.get('cseq', '0')}",
                    *extra,
                    f"Content-Length: {len(body.encode())}",
                ]
                writer.write(("\r\n".join(out) + "\r\n\r\n" + body).encode())
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()

    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        yield port
    finally:
        server.close()
        await server.wait_closed()


def url(port: int, user: str = USER, password: str = "s3nh%40forte", path: str = "/stream1") -> str:
    return f"rtsp://{user}:{password}@127.0.0.1:{port}{path}"


@pytest.mark.parametrize("mode", ["digest", "digest-qop", "basic", "open"])
async def test_online_with_each_auth_scheme(mode: str) -> None:
    async with fake_rtsp_server(camera_handler(mode)) as port:
        result = await RtspDescribeProbe().probe(url(port), StreamProfile.MAIN)
    assert result.outcome is ProbeOutcome.ONLINE, result.detail
    assert [(t.kind, t.codec, t.clock_rate) for t in result.tracks] == [
        ("video", "H265", 90000),
        ("audio", "PCMA", 8000),
    ]
    assert result.latency_ms is not None


async def test_wrong_password_is_auth_error() -> None:
    async with fake_rtsp_server(camera_handler("digest")) as port:
        result = await RtspDescribeProbe().probe(url(port, password="errada"), StreamProfile.MAIN)
    assert result.outcome is ProbeOutcome.AUTH_ERROR


async def test_unknown_path_is_not_found() -> None:
    async with fake_rtsp_server(camera_handler("open")) as port:
        result = await RtspDescribeProbe().probe(url(port, path="/outro"), StreamProfile.SUB)
    assert result.outcome is ProbeOutcome.NOT_FOUND
    assert result.profile is StreamProfile.SUB


async def test_credentials_in_path_are_sent_as_is() -> None:
    """Formato Xiongmai: sem userinfo, credenciais no caminho."""
    seen: list[str] = []

    def handler(request_line: str, headers: dict[str, str]) -> tuple[int, list[str], str]:
        seen.append(request_line.split()[1])
        return 200, [], SDP

    async with fake_rtsp_server(handler) as port:
        path = "/user=admin&password=abc&channel=1&stream=0.sdp?"
        result = await RtspDescribeProbe().probe(f"rtsp://127.0.0.1:{port}{path}", StreamProfile.MAIN)
    assert result.outcome is ProbeOutcome.ONLINE
    assert seen == [f"rtsp://127.0.0.1:{port}{path}"]  # inclusive o "?" final


async def test_connection_refused_is_offline() -> None:
    async with fake_rtsp_server(camera_handler("open")) as port:
        pass  # servidor fechado: porta livre e recusando
    result = await RtspDescribeProbe(timeout=2).probe(url(port), StreamProfile.MAIN)
    assert result.outcome is ProbeOutcome.OFFLINE


async def test_silent_server_times_out_as_offline() -> None:
    async def silent(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await asyncio.sleep(5)
        writer.close()

    server = await asyncio.start_server(silent, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        result = await RtspDescribeProbe(timeout=0.5).probe(url(port), StreamProfile.MAIN)
    finally:
        server.close()
    assert result.outcome is ProbeOutcome.OFFLINE
    assert "tempo esgotado" in result.detail


async def test_non_rtsp_response_is_protocol_error() -> None:
    async def http(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.readuntil(b"\r\n\r\n")
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(http, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        result = await RtspDescribeProbe(timeout=2).probe(url(port), StreamProfile.MAIN)
    finally:
        server.close()
    assert result.outcome is ProbeOutcome.PROTOCOL_ERROR


async def test_result_never_contains_credentials() -> None:
    async with fake_rtsp_server(camera_handler("digest")) as port:
        results = [
            await RtspDescribeProbe().probe(url(port), StreamProfile.MAIN),
            await RtspDescribeProbe().probe(url(port, password="errada"), StreamProfile.MAIN),
            await RtspDescribeProbe().probe(url(port, path="/x"), StreamProfile.MAIN),
        ]
    for r in results:
        assert "s3nh" not in repr(r)
        assert "errada" not in repr(r)


def test_parse_sdp_handles_dynamic_and_static_payloads() -> None:
    tracks = parse_sdp(SDP + "m=application 0 RTP/AVP 107\r\na=rtpmap:107 vnd.onvif.metadata/90000\r\n")
    assert [(t.kind, t.codec) for t in tracks] == [
        ("video", "H265"),
        ("audio", "PCMA"),
        ("application", "VND.ONVIF.METADATA"),
    ]
