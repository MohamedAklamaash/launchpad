
class User:
    def __init__(self, user_id: str, username: str, email: str, role: str, metadata:dict):
        self.user_id = user_id
        self.username = username
        self.email = email
        self.role = role
        self.metadata = metadata
    
    def __str__(self):
        return self.email