import jwt


class ScopedTokenRejected(jwt.InvalidTokenError):
    """Raised when a token carries a narrow-purpose `scope` claim (currently
    auth-service's 5-minute password_reset token, minted from an email+OTP pair, not a
    login) somewhere a full session is expected. Signature and expiry checks alone
    aren't enough to keep such a token from being replayed as a session credential — it
    still carries the user's real sub/role."""


class JWTUser:
    def __init__(self, **payload):
        self.__dict__.update(payload)
        if 'sub' in payload:
            self.id = payload['sub']
        self.is_active = True
        self.is_authenticated = True
        self.payload = payload

    def __str__(self):
        return str(self.payload)

    def __repr__(self):
        return str(self.payload)

    def __getitem__(self, key):
        return self.payload.get(key)
    
    def get(self, key, default=None):
        return self.payload.get(key, default)

    def to_dict(self):
        return self.payload

def decode_jwt(token: str, secret: str) -> JWTUser:
    payload = jwt.decode(token, secret, algorithms=["HS256"], leeway=300)
    if payload.get("scope"):
        raise ScopedTokenRejected("Token scope is not a valid session credential")
    return JWTUser(**payload)