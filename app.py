# /// script
# requires-python = ">=3.10"
# dependencies = ["google-genai>=2.25", "keyring>=25"]
# ///
"""Koe Studio（声スタジオ）：Gemini TTS で自分の声を登録（Voice Replication）し、演技を付けて読み上げる小さなローカルアプリ。

起動：`uv run app.py`（または「起動（Mac）.command」「起動（Windows）.bat」をダブルクリック）。
- APIキーは画面で入力し、OS の安全な保管場所（macOS キーチェーン／Windows 資格情報マネージャー）に保存する。
  環境変数 GEMINI_API_KEY があればそちらを優先する。
- 画面は http://127.0.0.1:<port>/ で、このパソコンからしか開けない。操作には起動ごとに作る合言葉（トークン）が要る。
- 作った音声は、このフォルダの「出力」に保存する。
Google とは無関係の非公式ツールです。 / Unofficial tool, not affiliated with Google.
"""
import base64
import contextvars
import getpass
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import threading
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import keyring
import keyring.errors
from google import genai

VERSION = "0.1.0"
HERE = Path(__file__).resolve().parent
OUT = HERE / "出力"
KEY_SERVICE = "gemini-api-key"
KEY_USER = getpass.getuser()
MODELS = {"flash": "gemini-3.8-flash-tts", "lite": "gemini-3.8-flash-lite-tts"}
PREBUILT = ["Zephyr", "Puck", "Charon", "Kore", "Fenrir", "Leda", "Orus", "Aoede", "Callirrhoe", "Autonoe",
            "Enceladus", "Iapetus", "Umbriel", "Algieba", "Despina", "Erinome", "Algenib", "Rasalgethi",
            "Laomedeia", "Achernar", "Alnilam", "Schedar", "Gacrux", "Pulcherrima", "Achird",
            "Zubenelgenubi", "Vindemiatrix", "Sadachbia", "Sadaltager", "Sulafat"]
MIME = {"m4a": "audio/mp4", "mp4": "audio/mp4", "wav": "audio/wav", "mp3": "audio/mpeg"}
HINTS = {  # (日本語, English)
    400: ("キーが正しくないか、送った内容の形が合っていません。", "The key is invalid or the request was malformed."),
    402: ("前払い残高とクレジットが0です。Google AI Studio の Billing を確認してください。", "Your prepaid balance and credits are 0. Check Billing in Google AI Studio."),
    403: ("このキーのプロジェクトでは使えません。", "This key's project can't use this feature."),
    404: ("モデルか声が見つかりません。", "The model or voice was not found."),
    429: ("利用上限に達しました（無料枠は1モデル1日10回）。時間をおくか、有料枠にしてください。",
          "Rate limit reached (the free tier allows 10 requests per model per day). Wait, or switch to the paid tier."),
}
MSG = {  # (日本語, English)
    "no_key": ("APIキーが設定されていません。「設定」から入力してください。", "No API key is set. Enter one in Settings."),
    "synthetic": ("合成音声またはAIの透かしが入っていると判定されました。変換・加工していない、本人の生の録音を使ってください（音声変換ソフトで作った声は登録できません）。",
                  "The recording was judged to contain synthetic speech or an AI watermark. Use an unedited recording of your own voice (voices made with voice changers can't be registered)."),
    "safety": ("安全確認に通りませんでした。見本と同意文が同じ人・同じ部屋・同じマイクで録られているか確認してください。",
               "The safety check failed. Make sure the sample and the consent statement were recorded by the same person, in the same room, with the same microphone."),
    "bad_key": ("APIキーが正しくありません。Google AI Studio でキーをコピーし直して、もう一度貼り付けてください。",
                "The API key is not valid. Copy it again from Google AI Studio and paste it here."),
    "key_format": ("APIキーの形が正しくありません。Google AI Studio でコピーしたキーをそのまま貼り付けてください。",
                   "This doesn't look like an API key. Paste the key exactly as copied from Google AI Studio."),
    "key_save_fail": ("キーを保存できませんでした（{e}）。環境変数 GEMINI_API_KEY に入れて起動する方法もあります。",
                      "Couldn't save the key ({e}). You can also start the app with the GEMINI_API_KEY environment variable."),
    "need_name": ("声の名前を入れてください。", "Enter a name for the voice."),
    "need_rec": ("{label}の録音を入れてください（m4a / wav / mp3）。", "Add the {label} recording (m4a / wav / mp3)."),
    "src": ("見本", "sample"), "con": ("同意文", "consent"),
    "need_text": ("台詞と声を選んでください。", "Enter a script and choose a voice."),
    "reason": ("理由", "Reason"),
    "forbidden": ("許可されていないアクセスです。", "Access denied."),
    "too_big": ("ファイルが大きすぎます（40MBまで）。", "The file is too large (40 MB max)."),
}
LANG = contextvars.ContextVar("lang", default="ja")


def tr(key, **kw):
    """画面の言語（X-Lang）に合わせた文を返す。"""
    return MSG[key][LANG.get() == "en"].format(**kw)
TOKEN = secrets.token_urlsafe(24)
_client = None


def stored_key():
    """(キー, 保存場所) を返す。無ければ (None, None)。"""
    env = os.environ.get("GEMINI_API_KEY", "").strip()
    if env:
        return env, "env"
    try:
        k = keyring.get_password(KEY_SERVICE, KEY_USER)
    except Exception as e:  # 保管場所が使えない環境（一部の Linux など）
        print(f"[キーの保管場所を読めません] {e}", file=sys.stderr, flush=True)
        return None, None
    return (k, "keyring") if k else (None, None)


def client():
    global _client
    if _client is None:
        key, _ = stored_key()
        if not key:
            raise ValueError(tr("no_key"))
        _client = genai.Client(api_key=key)
    return _client


def explain(e):
    """API の例外を、利用者が原因を判断できる日本語にする。全文は起動したウィンドウに残す。"""
    msg = str(e).replace("\\n", "\n")
    print(f"[API エラー] {msg}", file=sys.stderr, flush=True)
    if "synthetic speech" in msg:
        return tr("synthetic")
    if "safety checks" in msg:
        return tr("safety")
    if "API key not valid" in msg or "API_KEY_INVALID" in msg:
        return tr("bad_key")
    m = re.search(r"Original error: (.+?)(?: \[type\.googleapis|\n|$)", msg)
    detail = m.group(1) if m else msg[:300]
    code = getattr(e, "status_code", None)
    hint = HINTS.get(code, ("", ""))[LANG.get() == "en"]
    return f"HTTP {code}: {hint} {tr('reason')}: {detail[:400]}"


def save_key(body):
    global _client
    key = (body.get("key") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_\-]{20,200}", key):
        raise ValueError(tr("key_format"))
    test = genai.Client(api_key=key)
    try:
        test.voices.list(type_=["replicated"])   # キーが使えるかを、料金のかからない呼び出しで確かめる
    except Exception as e:
        raise ValueError(explain(e))
    try:
        keyring.set_password(KEY_SERVICE, KEY_USER, key)
    except Exception as e:
        raise ValueError(tr("key_save_fail", e=e))
    _client = test
    return {"ok": True}


def delete_key():
    global _client
    try:
        keyring.delete_password(KEY_SERVICE, KEY_USER)
    except keyring.errors.PasswordDeleteError:
        pass
    _client = None
    return {"ok": True}


def list_voices():
    out = []
    for t in ("replicated", "prompted"):
        for v in (client().voices.list(type_=[t]).voices or []):
            out.append({"id": v.id, "name": v.display_name, "type": t})
    return out


def register(body):
    name = (body.get("name") or "").strip()
    src, con = body.get("source") or {}, body.get("consent") or {}
    if not name:
        raise ValueError(tr("need_name"))
    for label, f in ((tr("src"), src), (tr("con"), con)):
        ext = (f.get("filename") or "").rsplit(".", 1)[-1].lower()
        if ext not in MIME or not f.get("data"):
            raise ValueError(tr("need_rec", label=label))
        f["mime"] = MIME[ext]
    v = client().voices.create(store=True, voice={
        "model": MODELS["flash"], "type": "replicated", "display_name": name,
        "replicated": {"source_audio": {"mime_type": src["mime"], "data": src["data"]},
                       "consent_audio": {"mime_type": con["mime"], "data": con["data"]}}}, timeout=300)
    return {"id": v.id, "name": name}


def speak(body):
    text = (body.get("text") or "").strip()
    voice = body.get("voice") or ""
    if not text or not voice:
        raise ValueError(tr("need_text"))
    part = {"type": "text", "text": text}
    if (body.get("style") or "").strip():
        part["annotations"] = [{"type": "speech_metadata", "style": body["style"].strip()}]
    model = MODELS.get(body.get("model"), MODELS["flash"])
    it = client().interactions.create(model=model, input=[{"type": "user_input", "content": [part]}],
                                      response_format={"type": "audio"},
                                      generation_config={"speech_config": [{"voice": voice}]}, timeout=180)
    wav = base64.b64decode(it.output_audio.data)
    OUT.mkdir(exist_ok=True)
    plain = re.sub(r"<[^>]*>", "", text)   # <laugh> などのタグはファイル名に入れない
    stem = "".join(c for c in plain[:16] if c not in '/\\:*?"<>|\n\t ')
    path = OUT / f"{datetime.now():%Y%m%d_%H%M%S}_{stem or 'voice'}.wav"
    path.write_bytes(wav)
    return {"file": path.name, "audio": base64.b64encode(wav).decode()}


def open_output():
    OUT.mkdir(exist_ok=True)
    if sys.platform == "darwin":
        subprocess.run(["open", str(OUT)], check=False)
    elif os.name == "nt":
        os.startfile(str(OUT))  # noqa: S606 — 利用者のフォルダを開くだけ
    else:
        subprocess.run(["xdg-open", str(OUT)], check=False)
    return {"ok": True}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # 端末に余計なログを出さない
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _authorized(self):
        LANG.set("en" if self.headers.get("X-Lang", "").startswith("en") else "ja")
        return secrets.compare_digest(self.headers.get("X-Token", ""), TOKEN)

    def do_GET(self):
        if self.path == "/":
            html = (HERE / "index.html").read_text(encoding="utf-8")
            html = html.replace("__TOKEN__", TOKEN).replace("__PREBUILT__", json.dumps(PREBUILT))
            html = html.replace("__VERSION__", VERSION)
            return self._send(200, html.encode(), "text/html; charset=utf-8")
        if not self._authorized():
            return self._send(403, {"error": tr("forbidden")})
        if self.path == "/api/status":
            _, where = stored_key()
            return self._send(200, {"configured": bool(where), "source": where})
        if self.path == "/api/voices":
            try:
                return self._send(200, {"voices": list_voices()})
            except ValueError as e:
                return self._send(400, {"error": str(e)})
            except Exception as e:
                return self._send(502, {"error": explain(e)})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        if not self._authorized():
            return self._send(403, {"error": tr("forbidden")})
        try:
            n = int(self.headers.get("Content-Length", 0))
            if n > 40 * 1024 * 1024:
                return self._send(413, {"error": tr("too_big")})
            body = json.loads(self.rfile.read(n) or b"{}")
            routes = {
                "/api/key": lambda: save_key(body),
                "/api/key/delete": delete_key,
                "/api/register": lambda: register(body),
                "/api/speak": lambda: speak(body),
                "/api/delete": lambda: (client().voices.delete(id=body["id"]), {"ok": True})[1],
                "/api/open-output": open_output,
            }
            if self.path not in routes:
                return self._send(404, {"error": "not found"})
            return self._send(200, routes[self.path]())
        except ValueError as e:
            return self._send(400, {"error": str(e)})
        except Exception as e:
            return self._send(502, {"error": explain(e)})


def free_port(start=8790):
    for p in range(start, start + 50):
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", p)) != 0:
                return p
    raise SystemExit("空いているポートが見つかりません。")


def main():
    port = free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    print(f"Koe Studio {VERSION}：{url} をブラウザで開いています（止めるときはこのウィンドウで Ctrl+C）", flush=True)
    if "--no-browser" not in sys.argv:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n終了しました。")


if __name__ == "__main__":
    main()
