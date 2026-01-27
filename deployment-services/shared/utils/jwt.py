import jwt

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
    payload = jwt.decode(token, secret, algorithms=["HS256"])
    return JWTUser(**payload)