from app.utils.security import create_access_token, decode_access_token
from datetime import timedelta
import jwt
import pytest

def test_create_access_token():
    user_id= "6ae9kdjfdkjfkdh"
    token= create_access_token(user_id)
    
    assert token is not None
    assert isinstance(token, str)
    
    payload= decode_access_token(token)
    
    assert payload["sub"] == user_id
    assert "iat" in payload
    assert "exp" in payload
def test_decode_invalid_token():
    with pytest.raises(jwt.InvalidTokenError):
        decode_access_token("invalid.token.value")
        
def test_decode_tampered_token():
    orignal_token= create_access_token("user123")
    tampered_token= orignal_token[:-1] + ( "A" if orignal_token[-1] != "A" else "B")
    with pytest.raises(jwt.InvalidTokenError):
        decode_access_token(tampered_token)
        
def test_decode_expired_token():
    token= create_access_token(
        user_id="6ae9kdjfdkjfkdh",
        expires_delta= timedelta(seconds= -1)
    )
    
    with pytest.raises(jwt.ExpiredSignatureError):
        decode_access_token(token)