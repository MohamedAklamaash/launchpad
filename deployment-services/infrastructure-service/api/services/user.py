from api.repositories.user import UserRepository

class UserService:
    def __init__(self):
        self.repository = UserRepository()
    
    def get_user(self, user_id):
        return self.repository.get_user(user_id)
    
    def upsert_user(self, user_data: dict):
        return self.repository.upsert_user(user_data)