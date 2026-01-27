from api.models.user import User as UserModel

class UserRepository:
    def get_user(self, user_id):
        try:
            return UserModel.objects.get(id=user_id)
        except UserModel.DoesNotExist:
            return None
    
    def upsert_user(self, user_data: dict):
        user_id = user_data.get("id")
        if not user_id:
            raise ValueError("User ID is required for upsert operation")

        user, created = UserModel.objects.update_or_create(
            id=user_id,
            defaults=user_data
        )
        return user, created