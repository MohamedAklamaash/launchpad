import jwt

class JWTUser:
    def __init__(self, sub, email, iat, exp):
        self.id = sub
        self.sub = sub
        self.email = email
        self.iat = iat
        self.exp = exp

    def __str__(self):
        return self.email

def decode_jwt(token: str, secret: str) -> JWTUser:
    payload = jwt.decode(token, secret, algorithms=["HS256"])
    return JWTUser(**payload)