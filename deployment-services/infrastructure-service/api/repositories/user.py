from api.services.user import UserService
from api.models.user import User as UserModel

class UserRepository:
    def __init__(self):
        self.user_model = UserModel()
        self.user_service = UserService()
    
    def get_user(self, user_id):
        return self.user_service.get_user(user_id)
    
    def upsert_user(self, user_data):
        return self.user_service.upsert_user(user_data)