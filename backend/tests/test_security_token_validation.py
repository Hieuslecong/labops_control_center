"""Isolated regression tests for malformed signed user sessions."""
import hashlib
import hmac
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.security import AuthError, _b64e, verify_token


@pytest.mark.parametrize('payload', [
    {'sub': 'alice', 'role': 'ADMIN', 'exp': 'not-a-number'},
    {'sub': 'alice', 'role': 'ADMIN', 'exp': None},
    {'sub': 'alice', 'role': 'ADMIN', 'exp': []},
    ['not', 'an', 'object'],
    {'sub': 'alice', 'role': [], 'exp': 4102444800},
    {'sub': ['alice'], 'role': 'ADMIN', 'exp': 4102444800},
    {'sub': 'alice', 'role': 'ADMIN', 'exp': True},
])
def test_malformed_signed_token_is_rejected_as_unauthorized(payload):
    secret = 'isolated-test-secret'
    raw = json.dumps(payload).encode()
    signature = hmac.new(secret.encode(), raw, hashlib.sha256).digest()
    with pytest.raises(AuthError) as error:
        verify_token(f'{_b64e(raw)}.{_b64e(signature)}', secret=secret)
    assert error.value.status == 401
