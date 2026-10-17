"""Diagnóstico DVRIP/Xiongmai (F0-09 e F0-10). SOMENTE LEITURA.

Uso:
  python scripts/diag_dvrip.py info                # login, SystemInfo, capacidades e configurações
  python scripts/diag_dvrip.py alarms --minutes 10 # escuta passiva de alarmes (movimento/pessoa)

O cliente só aceita enviar os códigos da lista READ_ONLY_CODES; qualquer outro (ex.: 1040
SetConfig, 1400 PTZ, 1450 OPMachine/reboot) é bloqueado antes de sair. Campos sensíveis (serial,
MAC, senhas, Wi-Fi, tokens) são mascarados; a lista de usuários nunca é consultada.
Relatório JSON em docs/compatibility/raw/ (fora do Git).

Protocolo baseado em icsee-ptz (MIT, © dbuezas), asyncio_dvrip.py @ 5bf50bc6b5df.
O transporte criptografado (AES) exige o pacote `cryptography`:
  uv run --with cryptography scripts/diag_dvrip.py info
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import socket
import struct
import sys
import time
from datetime import datetime

from _common import ROOT, load_dotenv, mask

RAW_DIR = ROOT / "docs" / "compatibility" / "raw"
HEADER = struct.Struct("<BB2xII2xHI")  # mesmo layout do icsee-ptz ("BB2xII2xHI", little-endian nativo)

LOGIN, KEEPALIVE, SYSINFO, CONFIG_GET, ABILITY_GET, MONITOR_CLAIM, TIME_QUERY, ALARM_SUBSCRIBE, ALARM_INFO = (
    1000, 1006, 1020, 1042, 1360, 1413, 1452, 1500, 1504)
READ_ONLY_CODES = {LOGIN, KEEPALIVE, SYSINFO, CONFIG_GET, ABILITY_GET, MONITOR_CLAIM, TIME_QUERY, ALARM_SUBSCRIBE}
OK_CODES = {100, 515}
RET_MEANING = {100: "OK", 101: "erro desconhecido", 102: "versão não suportada", 103: "não permitido",
               104: "usuário já logado", 105: "não logado", 106: "usuário ou senha incorretos",
               107: "sem permissão", 203: "senha incorreta", 205: "conta bloqueada?",
               607: "não suportado"}

# Configurações lidas (1042), conforme o icsee-ptz. Nada de "Users", Wi-Fi ou credenciais.
CONFIGS = [
    "General.General", "Camera.Param", "Camera.ParamEx", "Camera.ClearFog", "Camera.WhiteLight",
    "Simplify.Encode", "AVEnc.VideoColor", "AVEnc.SmartH264V2", "AVEnc.VideoWidget",
    "Detect.MotionDetect", "Detect.HumanDetection", "Detect.BlindDetect", "Detect.LossDetect",
    "Detect.CarShapeDetection", "Detect.DetectTrack", "FbExtraStateCtrl",
    "fVideo.Volume", "fVideo.InVolume", "fVideo.VolumeIn", "fVideo.OsdLogo",
    "General.OneKeyMaskVideo", "General.TimingSleep", "General.ResumePtzState", "General.AutoMaintain",
    "Uart.PTZPreset", "Record",
    "NetWork.RTSP", "NetWork.OnvifPwdCheckout", "NetWork.Nat", "NetWork.PMS", "NetWork.Upnp",
    "NetWork.OnlineUpgrade", "NetWork.DisableXmServerConn", "NetWork.NetNTP",
]
ABILITIES = ["SystemFunction", "EncodeCapability", "Camera", "Encode264ability", "HumanRuleLimit"]
STATUS = ["SystemInfo", "StorageInfo", "WorkState"]

SENSITIVE_KEYS = ("serial", "sn", "mac", "password", "passwd", "pwd", "pass", "ssid", "key", "token",
                  "uuid", "hardwareid", "devid", "cloudid", "email", "phone", "user")
_AES_KEY, _AES_IV = b"dashoiahfarqdasr", b"\x00" * 16


def sofia_hash(password: str) -> str:
    md5 = hashlib.md5(password.encode()).digest()
    chars = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
    return "".join(chars[(a + b) % 62] for a, b in zip(md5[::2], md5[1::2]))


def sanitize(value):
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            lk = k.lower()
            hit = any(lk == s or lk.endswith(s) or lk.startswith(s) for s in SENSITIVE_KEYS)
            out[k] = "***" if hit and not isinstance(v, (dict, list, bool)) else sanitize(v)
        return out
    if isinstance(value, list):
        return [sanitize(v) for v in value]
    return value


class ReadOnlyDVRIP:
    def __init__(self, host: str, port: int = 34567, timeout: float = 8.0) -> None:
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.session = 0
        self.seq = 0
        self.encrypt = False
        self.not_encrypt: set[int] = set()
        self.alive_interval = 20
        self.unsolicited: list[tuple[int, dict]] = []

    # --- transporte ---
    def _send(self, code: int, payload: dict) -> None:
        if code not in READ_ONLY_CODES:
            raise PermissionError(f"código DVRIP {code} bloqueado: este cliente é somente leitura")
        data = json.dumps(payload, ensure_ascii=False).encode() + b"\x0a\x00"
        version = 0
        if self.encrypt and code not in self.not_encrypt:
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
            padded = data + b"\x00" * (-len(data) % 16)
            enc = Cipher(algorithms.AES(_AES_KEY), modes.CBC(_AES_IV)).encryptor()
            data = base64.b64encode(enc.update(padded) + enc.finalize()) + b"\x00"
            version = 1
        self.sock.sendall(HEADER.pack(255, version, self.session, self.seq, code, len(data)) + data)
        self.seq += 1

    def _recv_exact(self, n: int) -> bytes:
        buf = bytearray()
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("câmera fechou a conexão")
            buf.extend(chunk)
        return bytes(buf)

    def _parse(self, body: bytes) -> dict:
        stripped = body.rstrip(b"\x00").rstrip()
        if stripped and stripped[:1] not in (b"{", b"["):
            try:
                from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
                raw = base64.b64decode(stripped)
                raw = raw[: len(raw) - len(raw) % 16]
                dec = Cipher(algorithms.AES(_AES_KEY), modes.CBC(_AES_IV)).decryptor()
                stripped = (dec.update(raw) + dec.finalize()).rstrip(b"\x00").rstrip()
            except Exception:  # noqa: BLE001
                return {"_undecodable": len(body)}
        if not stripped:
            return {}
        try:
            return json.loads(stripped.decode("utf-8", errors="replace"))
        except ValueError:
            return {"_invalid_json": len(body)}

    def recv_packet(self) -> tuple[int, int, dict]:
        _h, _v, session, _seq, code, length = HEADER.unpack(self._recv_exact(HEADER.size))
        body = self._recv_exact(length) if length else b""
        return session, code, self._parse(body)

    def request(self, code: int, payload: dict) -> dict:
        self._send(code, payload)
        while True:
            session, reply_code, body = self.recv_packet()
            if reply_code == code + 1:
                if reply_code == LOGIN + 1 or session:
                    self.session = session or self.session
                return body
            self.unsolicited.append((reply_code, body))

    def _sid(self) -> str:
        return "0x%08X" % self.session

    # --- operações de leitura ---
    def login(self, user: str, password: str) -> dict:
        reply = self.request(LOGIN, {"EncryptType": "MD5", "LoginType": "DVRIP-Web",
                                     "PassWord": sofia_hash(password), "UserName": user})
        ret = reply.get("Ret")
        if ret not in OK_CODES:
            return {"ok": False, "ret": ret, "meaning": RET_MEANING.get(ret, "?")}
        self.session = int(reply["SessionID"], 16)
        self.alive_interval = reply.get("AliveInterval") or 20
        info = {"ok": True, "ret": ret, "alive_interval": self.alive_interval,
                "device_type": reply.get("DeviceType "), "channels": reply.get("ChannelNum"),
                "extra_channels": reply.get("ExtraChannel")}
        probe = self.request(SYSINFO, {"Name": "SystemInfo", "SessionID": self._sid()})
        info["plain_reply_empty"] = probe == {}
        if probe == {}:
            claim = self.request(MONITOR_CLAIM, {
                "Name": "OPMonitor",
                "OPMonitor": {"Action": "Claim", "Parameter": {"Channel": 0, "CombinMode": "CONNECT_ALL",
                                                                "StreamType": "Main", "TransMode": "TCP"}},
                "DHParameter": {"RandomStrA": base64.b64encode(os.urandom(3)).decode()[:4]},
                "SessionID": self._sid()})
            if "NotEncryptMsgID" in claim:
                self.not_encrypt = {int(m) for m in claim["NotEncryptMsgID"]}
                self.encrypt = True
        info["encrypted_transport"] = self.encrypt
        return info

    def get(self, code: int, name: str) -> dict:
        reply = self.request(code, {"Name": name, "SessionID": self._sid()})
        ret = reply.get("Ret")
        top = name.split(".")[0] if code == CONFIG_GET and name not in reply else name
        return {"ret": ret, "meaning": RET_MEANING.get(ret, "?"),
                "value": sanitize(reply.get(name, reply.get(top))) if ret in OK_CODES else None}

    def keepalive(self) -> None:
        self._send(KEEPALIVE, {"Name": "KeepAlive", "SessionID": self._sid()})

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


def credentials() -> tuple[str, str, str]:
    env = load_dotenv()
    for key in ("NVR_CAM_HOST", "NVR_CAM_USER", "NVR_CAM_PASSWORD"):
        if not env.get(key) or env[key] == "CHANGE_ME":
            raise SystemExit(f"Preencha {key} no .env")
    return env["NVR_CAM_HOST"], env["NVR_CAM_USER"], env["NVR_CAM_PASSWORD"]


def save(report: dict, prefix: str, password: str) -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    out = RAW_DIR / f"{prefix}_{datetime.now():%Y%m%d_%H%M%S}.json"
    out.write_text(mask(json.dumps(report, indent=2, ensure_ascii=False), password), encoding="utf-8")
    print(f"\nRelatório: {out.relative_to(ROOT)}")


def login_or_exit(cam: ReadOnlyDVRIP, user: str, password: str) -> dict:
    info = cam.login(user, password)
    if not info["ok"]:
        # Uma única tentativa: alguns firmwares bloqueiam a conta após falhas repetidas.
        raise SystemExit(f"Login DVRIP recusado: Ret {info['ret']} ({info['meaning']}). Não vou tentar de novo.")
    print(f"login OK; transporte criptografado: {info['encrypted_transport']}; "
          f"canais: {info['channels']}; keep-alive: {info['alive_interval']} s")
    return info


def cmd_info(port: int) -> int:
    host, user, password = credentials()
    cam = ReadOnlyDVRIP(host, port)
    try:
        report: dict = {"timestamp": datetime.now().astimezone().isoformat(), "login": login_or_exit(cam, user, password)}
        for group, code, names in (("status", SYSINFO, STATUS), ("abilities", ABILITY_GET, ABILITIES),
                                   ("configs", CONFIG_GET, CONFIGS)):
            report[group] = {}
            for name in names:
                try:
                    result = cam.get(code, name)
                except (TimeoutError, socket.timeout):
                    result = {"ret": None, "meaning": "sem resposta", "value": None}
                report[group][name] = result
                print(f"  [{group}] {name}: Ret {result['ret']} ({result['meaning']})")
        report["unsolicited"] = [{"code": c, "body": sanitize(b)} for c, b in cam.unsolicited]
    finally:
        cam.close()
    save(report, "dvrip_info", password)
    return 0


def cmd_alarms(port: int, minutes: float) -> int:
    host, user, password = credentials()
    cam = ReadOnlyDVRIP(host, port)
    events: list[dict] = []
    try:
        login_or_exit(cam, user, password)
        sub = cam.request(ALARM_SUBSCRIBE, {"Name": "", "SessionID": cam._sid()})
        print(f"assinatura de alarmes: Ret {sub.get('Ret')} ({RET_MEANING.get(sub.get('Ret'), '?')})")
        print(f"escutando por {minutes} min — passe na frente da câmera...")
        cam.sock.settimeout(1.0)
        end = time.monotonic() + minutes * 60
        next_alive = time.monotonic() + cam.alive_interval
        while time.monotonic() < end:
            if time.monotonic() >= next_alive:
                cam.keepalive()
                next_alive = time.monotonic() + cam.alive_interval
            try:
                _session, code, body = cam.recv_packet()
            except (TimeoutError, socket.timeout):
                continue
            except (ConnectionError, OSError) as exc:
                print(f"  {datetime.now():%H:%M:%S} conexão DVRIP perdida ({exc}); encerrando a escuta")
                events.append({"received_at": datetime.now().astimezone().isoformat(), "connection_lost": str(exc)})
                break
            if code == ALARM_INFO:
                payload = sanitize(body.get(body.get("Name", ""), body))
                event = {"received_at": datetime.now().astimezone().isoformat(), "payload": payload}
                events.append(event)
                print(f"  {event['received_at'][11:19]} ALARME {json.dumps(payload, ensure_ascii=False)}")
    finally:
        cam.close()
    print(f"{len(events)} alarme(s) recebido(s)")
    save({"minutes": minutes, "events": events}, "dvrip_alarms", password)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=34567)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("info")
    alarms = sub.add_parser("alarms")
    alarms.add_argument("--minutes", type=float, default=10)
    args = parser.parse_args()
    return cmd_info(args.port) if args.cmd == "info" else cmd_alarms(args.port, args.minutes)


if __name__ == "__main__":
    sys.exit(main())
