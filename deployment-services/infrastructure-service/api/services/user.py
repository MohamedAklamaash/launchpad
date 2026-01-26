from api.models.user import User

class UserService:
    def __init__(self):
        self.user_model = User()
    
    def get_user(self, user_id):
        return self.user_model.objects.get(id=user_id)
    
    def upsert_user(self, user_data:User):
        user_id = user_data.get("id")
        if not user_id:
            raise ValueError("User ID is required for upsert operation")

        user, created = User.objects.update_or_create(
            id=user_id,
            defaults=user_data
        )
        return user, created