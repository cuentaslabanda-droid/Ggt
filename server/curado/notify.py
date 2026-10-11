"""Notification channels.

Every channel is enabled just by putting its credentials in /etc/curado.env
(see deploy/curado.env.example); nothing secret is stored in the database or
shown in the dashboard. Message channels receive every alert; call channels
(Asterisk with an analog line, Twilio) only critical ones.
"""

import base64
import json
import logging
import os
import re
import shutil
import smtplib
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
from email.message import EmailMessage
from pathlib import Path
from typing import Callable, Mapping

log = logging.getLogger("curado.notify")

HTTP_TIMEOUT = 15


def _list(value: str | None) -> list[str]:
    return [v.strip() for v in (value or "").split(",") if v.strip()]


def _http(url: str, data: bytes | None = None, headers: dict | None = None,
          method: str | None = None) -> bytes:
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
        return r.read()


def _post_json(url: str, body: dict, headers: dict | None = None) -> bytes:
    return _http(url, json.dumps(body).encode(),
                 {"Content-Type": "application/json", **(headers or {})})


def _post_form(url: str, fields: dict, headers: dict | None = None) -> bytes:
    return _http(url, urllib.parse.urlencode(fields).encode(),
                 {"Content-Type": "application/x-www-form-urlencoded", **(headers or {})})


class Channel:
    name = "channel"
    kind = "message"  # or "call"

    def send(self, subject: str, body: str, critical: bool) -> None:
        raise NotImplementedError


# --- Message channels -----------------------------------------------------------


class NtfyChannel(Channel):
    name = "ntfy"

    def __init__(self, url: str):
        self.url = url

    def send(self, subject, body, critical):
        # JSON publishing (to the server root) keeps accents in the title intact.
        u = urllib.parse.urlsplit(self.url)
        _post_json(f"{u.scheme}://{u.netloc}/", {
            "topic": u.path.strip("/"),
            "title": subject,
            "message": body,
            "priority": 5 if critical else 3,
            "tags": ["rotating_light" if critical else "warning"],
        })


class EmailChannel(Channel):
    name = "email"

    def __init__(self, host, port, user, password, sender, recipients, starttls):
        self.host, self.port = host, port
        self.user, self.password = user, password
        self.sender, self.recipients = sender, recipients
        self.starttls = starttls

    def send(self, subject, body, critical):
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = self.sender
        msg["To"] = ", ".join(self.recipients)
        if critical:
            msg["X-Priority"] = "1"
            msg["Importance"] = "high"
        msg.set_content(body)
        if self.port == 465:
            smtp = smtplib.SMTP_SSL(self.host, self.port, timeout=HTTP_TIMEOUT)
        else:
            smtp = smtplib.SMTP(self.host, self.port, timeout=HTTP_TIMEOUT)
        with smtp:
            if self.port != 465 and self.starttls:
                smtp.starttls()
            if self.user:
                smtp.login(self.user, self.password)
            smtp.send_message(msg)


class TelegramChannel(Channel):
    name = "telegram"

    def __init__(self, token: str, chat_ids: list[str]):
        self.token, self.chat_ids = token, chat_ids

    def send(self, subject, body, critical):
        for chat in self.chat_ids:
            _post_json(f"https://api.telegram.org/bot{self.token}/sendMessage",
                       {"chat_id": chat, "text": f"{subject}\n\n{body}"})


class CallMeBotWhatsApp(Channel):
    """Free WhatsApp messages to your own phone: https://www.callmebot.com"""

    name = "whatsapp"

    def __init__(self, targets: list[tuple[str, str]]):
        self.targets = targets  # (phone, apikey)

    def send(self, subject, body, critical):
        for phone, apikey in self.targets:
            q = urllib.parse.urlencode({"phone": phone, "text": f"{subject}\n{body}",
                                        "apikey": apikey})
            _http(f"https://api.callmebot.com/whatsapp.php?{q}")


class WebhookChannel(Channel):
    """Slack, Discord, Mattermost, Home Assistant... anything taking JSON."""

    name = "webhook"

    def __init__(self, urls: list[str]):
        self.urls = urls

    def send(self, subject, body, critical):
        text = f"{subject}\n{body}"
        for url in self.urls:
            _post_json(url, {"text": text, "content": text, "title": subject,
                             "message": body, "critical": critical})


class TwilioBase(Channel):
    def __init__(self, sid: str, token: str, sender: str, recipients: list[str]):
        self.sid, self.token, self.sender, self.recipients = sid, token, sender, recipients

    def _api(self, resource: str, fields: dict) -> None:
        auth = base64.b64encode(f"{self.sid}:{self.token}".encode()).decode()
        _post_form(f"https://api.twilio.com/2010-04-01/Accounts/{self.sid}/{resource}.json",
                   fields, {"Authorization": f"Basic {auth}"})


class TwilioSms(TwilioBase):
    name = "sms"

    def send(self, subject, body, critical):
        for to in self.recipients:
            self._api("Messages", {"From": self.sender, "To": to, "Body": f"{subject}\n{body}"})


class TwilioCall(TwilioBase):
    name = "llamada (Twilio)"
    kind = "call"

    def send(self, subject, body, critical):
        say = f'<Say language="es-ES">{_xml_escape(spoken(body))}</Say><Pause length="1"/>'
        twiml = f"<Response>{say * 3}</Response>"
        for to in self.recipients:
            self._api("Calls", {"From": self.sender, "To": to, "Twiml": twiml})


def spoken(text: str) -> str:
    """First paragraph of a message, written the way a voice should read it."""
    text = text.split("\n\n")[0]
    text = re.sub(r"(\d)\.(\d)", r"\1 coma \2", text)
    return text.replace("°C", " grados").replace("%", " por ciento")


def _xml_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# --- Asterisk (analog line through an FXO gateway) ---------------------------------


def synthesize(text: str, out_noext: Path) -> Path:
    """Text to an 8 kHz mono WAV that Asterisk can play. Uses pico2wave (better
    Spanish voice, package libttspico-utils) or espeak-ng."""
    out = out_noext.with_suffix(".wav")
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        raw = Path(tmp) / "voz.wav"
        if shutil.which("pico2wave"):
            cmd = ["pico2wave", "-l", "es-ES", "-w", str(raw), text]
        elif shutil.which("espeak-ng"):
            cmd = ["espeak-ng", "-v", "es", "-s", "140", "-w", str(raw), text]
        else:
            raise RuntimeError("falta un sintetizador de voz (sudo apt install libttspico-utils)")
        subprocess.run(cmd, check=True, capture_output=True, timeout=60)
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(raw), "-ar", "8000",
                        "-ac", "1", "-acodec", "pcm_s16le", str(out)],
                       check=True, capture_output=True, timeout=60)
    out.chmod(0o644)
    return out


class AsteriskCall(Channel):
    """Places calls by dropping .call files in Asterisk's outgoing spool.

    One analog line carries one call at a time, so numbers are called one
    after another (each call file gets a later mtime, which Asterisk honours).
    The dialplan (deploy/asterisk/extensions_curado.conf) plays the message
    and lets the person press 1 to acknowledge the alarm.
    """

    name = "llamada (línea analógica)"
    kind = "call"

    def __init__(self, numbers: list[str], channel_tmpl: str, spool: Path, sounds: Path,
                 caller_id: str = "Camara curado", gap_s: int = 120,
                 tts: Callable[[str, Path], Path] = synthesize):
        self.numbers = numbers
        self.channel_tmpl = channel_tmpl
        self.spool = spool
        self.sounds = sounds
        self.caller_id = caller_id
        self.gap_s = gap_s
        self.tts = tts

    def send(self, subject, body, critical):
        stamp = int(time.time())
        message = self.tts(f"{spoken(body)} Pulse 1 para confirmar que ha recibido la alarma.",
                           self.sounds / f"alarma-{stamp}")
        staging = self.sounds / "spool-tmp"
        staging.mkdir(parents=True, exist_ok=True)
        for i, number in enumerate(self.numbers):
            content = "\n".join([
                f"Channel: {self.channel_tmpl.format(number=number)}",
                f"CallerID: \"{self.caller_id}\"",
                "MaxRetries: 2",
                "RetryTime: 90",
                "WaitTime: 45",
                "Context: curado-alarma",
                "Extension: s",
                "Priority: 1",
                f"Setvar: MSG={message.with_suffix('')}",
                "",
            ])
            tmp = staging / f"curado-{stamp}-{i}.call"
            tmp.write_text(content)
            when = stamp + i * self.gap_s
            os.utime(tmp, (when, when))  # Asterisk waits until the file's mtime
            dest = self.spool / tmp.name
            try:
                os.replace(tmp, dest)  # atomic when on the same filesystem
            except OSError:
                shutil.copy2(tmp, dest.with_suffix(".tmp"))
                os.replace(dest.with_suffix(".tmp"), dest)
                tmp.unlink(missing_ok=True)
        self._cleanup()

    def _cleanup(self, keep_days: int = 7) -> None:
        limit = time.time() - keep_days * 86400
        for f in self.sounds.glob("alarma-*.wav"):
            if f.stat().st_mtime < limit:
                f.unlink(missing_ok=True)


# --- Building and sending -------------------------------------------------------------


def build_channels(env: Mapping[str, str], sounds_dir: Path) -> list[Channel]:
    ch: list[Channel] = []
    ntfy = env.get("CURADO_NTFY_URL") or env.get("CURADO_ALERT_URL")
    if ntfy:
        ch.append(NtfyChannel(ntfy))
    if env.get("CURADO_SMTP_HOST") and env.get("CURADO_EMAIL_TO"):
        port = int(env.get("CURADO_SMTP_PORT", "587"))
        ch.append(EmailChannel(
            env["CURADO_SMTP_HOST"], port, env.get("CURADO_SMTP_USER", ""),
            env.get("CURADO_SMTP_PASSWORD", ""),
            env.get("CURADO_SMTP_FROM") or env.get("CURADO_SMTP_USER", "curado@localhost"),
            _list(env["CURADO_EMAIL_TO"]),
            env.get("CURADO_SMTP_STARTTLS", "1") != "0",
        ))
    if env.get("CURADO_TELEGRAM_TOKEN") and env.get("CURADO_TELEGRAM_CHAT_IDS"):
        ch.append(TelegramChannel(env["CURADO_TELEGRAM_TOKEN"],
                                  _list(env["CURADO_TELEGRAM_CHAT_IDS"])))
    if env.get("CURADO_CALLMEBOT"):
        targets = [tuple(t.split(":", 1)) for t in _list(env["CURADO_CALLMEBOT"]) if ":" in t]
        if targets:
            ch.append(CallMeBotWhatsApp(targets))
    if env.get("CURADO_WEBHOOK_URLS"):
        ch.append(WebhookChannel(_list(env["CURADO_WEBHOOK_URLS"])))
    if env.get("CURADO_TWILIO_SID") and env.get("CURADO_TWILIO_TOKEN") and env.get("CURADO_TWILIO_FROM"):
        args = (env["CURADO_TWILIO_SID"], env["CURADO_TWILIO_TOKEN"], env["CURADO_TWILIO_FROM"])
        if env.get("CURADO_TWILIO_SMS_TO"):
            ch.append(TwilioSms(*args, _list(env["CURADO_TWILIO_SMS_TO"])))
        if env.get("CURADO_TWILIO_CALL_TO"):
            ch.append(TwilioCall(*args, _list(env["CURADO_TWILIO_CALL_TO"])))
    if env.get("CURADO_ASTERISK_NUMBERS"):
        ch.append(AsteriskCall(
            _list(env["CURADO_ASTERISK_NUMBERS"]),
            env.get("CURADO_ASTERISK_CHANNEL", "PJSIP/{number}@fxo"),
            Path(env.get("CURADO_ASTERISK_SPOOL", "/var/spool/asterisk/outgoing")),
            sounds_dir,
            env.get("CURADO_ASTERISK_CALLERID", "Camara curado"),
        ))
    return ch


class Notifier:
    def __init__(self, channels: list[Channel]):
        self.channels = channels

    def describe(self) -> list[dict]:
        return [{"name": c.name, "kind": c.kind} for c in self.channels]

    def send(self, subject: str, body: str, critical: bool = False,
             call: bool = False) -> dict[str, str]:
        """Sends through every message channel (and call channels if call).
        One failing channel never stops the others."""
        results = {}
        for c in self.channels:
            if c.kind == "call" and not call:
                continue
            try:
                c.send(subject, body, critical)
                results[c.name] = "ok"
            except Exception as e:  # noqa: BLE001 - report and keep going
                log.error("Canal %s falló: %s", c.name, e)
                results[c.name] = f"error: {e}"
        return results
