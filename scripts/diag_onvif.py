"""Diagnóstico ONVIF (F0-08). SOMENTE LEITURA: apenas operações Get*.

Uso: python scripts/diag_onvif.py [--port 8899]

1. Testa se o serviço responde e se exige autenticação (mesmas chamadas sem e com credencial).
2. Com credencial (WS-Security UsernameToken digest), lista serviços, capacidades, perfis de mídia,
   PTZ, imaging e eventos. Números de série/IDs de hardware são mascarados.
Relatório JSON em docs/compatibility/raw/ (fora do Git).
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from _common import ROOT, load_dotenv, mask

RAW_DIR = ROOT / "docs" / "compatibility" / "raw"
NS = {
    "device": "http://www.onvif.org/ver10/device/wsdl",
    "media": "http://www.onvif.org/ver10/media/wsdl",
    "ptz": "http://www.onvif.org/ver20/ptz/wsdl",
    "imaging": "http://www.onvif.org/ver20/imaging/wsdl",
    "events": "http://www.onvif.org/ver10/events/wsdl",
}
SENSITIVE_TAGS = {"SerialNumber", "HardwareId", "MACAddress", "Password"}


def envelope(body: str, user: str | None, password: str | None) -> bytes:
    header = ""
    if user is not None and password is not None:
        nonce = os.urandom(16)
        created = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        digest = base64.b64encode(hashlib.sha1(nonce + created.encode() + password.encode()).digest()).decode()
        header = f"""<s:Header><Security s:mustUnderstand="1" xmlns="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd">
<UsernameToken><Username>{user}</Username>
<Password Type="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordDigest">{digest}</Password>
<Nonce EncodingType="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-soap-message-security-1.0#Base64Binary">{base64.b64encode(nonce).decode()}</Nonce>
<Created xmlns="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd">{created}</Created>
</UsernameToken></Security></s:Header>"""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">{header}<s:Body>{body}</s:Body></s:Envelope>""".encode()


def call(url: str, ns_key: str, op: str, inner: str = "", auth: tuple[str, str] | None = None) -> dict:
    if not op.startswith("Get"):
        raise PermissionError(f"operação ONVIF {op} bloqueada: diagnóstico somente leitura")
    body = f'<{op} xmlns="{NS[ns_key]}">{inner}</{op}>'
    data = envelope(body, *(auth or (None, None)))
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/soap+xml; charset=utf-8"})
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            return {"http": resp.status, "xml": resp.read().decode("utf-8", errors="replace")}
    except urllib.error.HTTPError as exc:
        return {"http": exc.code, "xml": exc.read().decode("utf-8", errors="replace")}
    except OSError as exc:
        return {"http": None, "xml": "", "error": str(exc)}


def to_dict(xml_text: str) -> object:
    """Converte o corpo SOAP em dict simples (sem namespaces), mascarando campos sensíveis."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return {"_unparsed": xml_text[:300]}

    def conv(el: ET.Element) -> object:
        tag = el.tag.split("}")[-1]
        children = list(el)
        attrs = {f"@{k.split('}')[-1]}": v for k, v in el.attrib.items()}
        if tag in SENSITIVE_TAGS:
            return "***"
        if not children:
            text = (el.text or "").strip()
            return {**attrs, "#text": text} if attrs and text else (attrs or text)
        out: dict = dict(attrs)
        for child in children:
            key = child.tag.split("}")[-1]
            val = conv(child)
            if key in out:
                out[key] = out[key] if isinstance(out[key], list) else [out[key]]
                out[key].append(val)
            else:
                out[key] = val
        return out

    body = next((el for el in root.iter() if el.tag.endswith("}Body")), root)
    return conv(body)


def fault(result: dict) -> str | None:
    if result.get("http") is None:
        return result.get("error", "sem resposta")
    if result["http"] != 200 or ":Fault" in result["xml"] or "<Fault" in result["xml"]:
        reason = re.search(r"<[^>]*Text[^>]*>([^<]+)<", result["xml"])
        sub = re.findall(r"<[^>]*Value>([^<]+)<", result["xml"])
        return f"HTTP {result['http']}: {reason.group(1) if reason else ''} {' / '.join(sub[-2:])}".strip()
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=8899)
    args = parser.parse_args()
    env = load_dotenv()
    host, user, password = env["NVR_CAM_HOST"], env["NVR_CAM_USER"], env["NVR_CAM_PASSWORD"]
    device_url = f"http://{host}:{args.port}/onvif/device_service"
    report: dict = {"timestamp": datetime.now().astimezone().isoformat(), "device_url": device_url, "calls": {}}

    def run(label: str, url: str, ns_key: str, op: str, inner: str = "", authed: bool = True) -> object | None:
        result = call(url, ns_key, op, inner, (user, password) if authed else None)
        err = fault(result)
        status = "OK" if err is None else f"FALHA ({err})"
        print(f"  {label}: {status}")
        parsed = to_dict(result["xml"]) if err is None else None
        report["calls"][label] = {"ok": err is None, "error": err, "response": parsed}
        return parsed

    print("== sem credencial")
    run("GetSystemDateAndTime (anônimo)", device_url, "device", "GetSystemDateAndTime", authed=False)
    anon_info = run("GetDeviceInformation (anônimo)", device_url, "device", "GetDeviceInformation", authed=False)
    anon_profiles = None
    print("== com credencial")
    run("GetDeviceInformation", device_url, "device", "GetDeviceInformation")
    services = run("GetServices", device_url, "device", "GetServices", "<IncludeCapability>false</IncludeCapability>")
    run("GetCapabilities", device_url, "device", "GetCapabilities", "<Category>All</Category>")

    xaddrs: dict[str, str] = {}
    if isinstance(services, dict):
        entries = services.get("GetServicesResponse", {}).get("Service", [])
        for s in entries if isinstance(entries, list) else [entries]:
            for key, ns in NS.items():
                if s.get("Namespace") == ns:
                    xaddrs[key] = s.get("XAddr", "").replace("0.0.0.0", host)
    report["xaddrs"] = xaddrs
    media_url = xaddrs.get("media", device_url)

    anon_profiles = run("GetProfiles (anônimo)", media_url, "media", "GetProfiles", authed=False)
    profiles = run("GetProfiles", media_url, "media", "GetProfiles")
    token = None
    if isinstance(profiles, dict):
        plist = profiles.get("GetProfilesResponse", {}).get("Profiles", [])
        plist = plist if isinstance(plist, list) else [plist]
        token = plist[0].get("@token") if plist and isinstance(plist[0], dict) else None
        for p in plist:
            if isinstance(p, dict):
                inner = f'<ProfileToken>{p.get("@token")}</ProfileToken>'
                run(f"GetSnapshotUri[{p.get('@token')}]", media_url, "media", "GetSnapshotUri", inner)
    if "ptz" in xaddrs:
        run("PTZ GetNodes", xaddrs["ptz"], "ptz", "GetNodes")
        run("PTZ GetConfigurations", xaddrs["ptz"], "ptz", "GetConfigurations")
        if token:
            run("PTZ GetPresets", xaddrs["ptz"], "ptz", "GetPresets", f"<ProfileToken>{token}</ProfileToken>")
    if "imaging" in xaddrs and isinstance(profiles, dict):
        plist = profiles.get("GetProfilesResponse", {}).get("Profiles", [])
        first = plist[0] if isinstance(plist, list) else plist
        src = (first.get("VideoSourceConfiguration") or {}).get("SourceToken") if isinstance(first, dict) else None
        if src:
            run("Imaging GetOptions", xaddrs["imaging"], "imaging", "GetOptions", f"<VideoSourceToken>{src}</VideoSourceToken>")
    if "events" in xaddrs:
        run("Events GetEventProperties", xaddrs["events"], "events", "GetEventProperties")

    report["auth_enforced"] = {
        "GetDeviceInformation": anon_info is None,
        "GetProfiles": anon_profiles is None,
    }
    print(f"\nexige autenticação? DeviceInformation: {anon_info is None}; Profiles: {anon_profiles is None}")
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    out = RAW_DIR / f"onvif_{datetime.now():%Y%m%d_%H%M%S}.json"
    out.write_text(mask(json.dumps(report, indent=2, ensure_ascii=False), password), encoding="utf-8")
    print(f"Relatório: {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
