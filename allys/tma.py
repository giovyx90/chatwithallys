import hashlib
import hmac
import json
import time
from base64 import urlsafe_b64decode, urlsafe_b64encode
from urllib.parse import parse_qsl


def validate_init_data(init_data: str, bot_token: str) -> dict:
    pairs = dict(parse_qsl(init_data, strict_parsing=True))
    received_hash = pairs.pop("hash", None)
    if not received_hash:
        raise ValueError("missing hash")
    data_check_string = "\n".join(f"{key}={value}" for key, value in sorted(pairs.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    calculated = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calculated, received_hash):
        raise ValueError("invalid initData hash")
    if "user" in pairs:
        pairs["user"] = json.loads(pairs["user"])
    if "chat" in pairs:
        pairs["chat"] = json.loads(pairs["chat"])
        if "id" in pairs["chat"]:
            pairs["chat_id"] = pairs["chat"]["id"]
    return pairs


def sign_session(payload: dict, secret: str, ttl_seconds: int = 7 * 86400) -> str:
    data = {**payload, "exp": int(time.time()) + ttl_seconds}
    body = urlsafe_b64encode(json.dumps(data, separators=(",", ":"), sort_keys=True).encode()).decode().rstrip("=")
    sig = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def validate_session(token: str, secret: str) -> dict:
    body, sep, sig = (token or "").partition(".")
    if not sep or not body or not sig:
        raise ValueError("invalid session")
    expected = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        raise ValueError("invalid session signature")
    padded = body + "=" * (-len(body) % 4)
    payload = json.loads(urlsafe_b64decode(padded.encode()).decode())
    if int(payload.get("exp", 0)) < int(time.time()):
        raise ValueError("expired session")
    user = {
        "id": int(payload["user_id"]),
        "username": payload.get("username"),
        "first_name": payload.get("first_name"),
        "last_name": payload.get("last_name"),
    }
    return {"user": user, "chat_id": payload.get("chat_id"), "auth_date": payload.get("iat")}
