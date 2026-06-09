import hashlib
import hmac
from urllib.parse import urlencode

from allys.tma import validate_init_data


def test_validate_init_data() -> None:
    bot_token = "123:test"
    pairs = {"auth_date": "1", "query_id": "abc", "user": '{"id":1}'}
    check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    pairs["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()

    assert validate_init_data(urlencode(pairs), bot_token)["user"]["id"] == 1
