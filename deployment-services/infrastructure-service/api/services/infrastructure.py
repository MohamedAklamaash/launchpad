from api.models.infrastructure import Infrastructure
from api.models.user import User
import logging

logger = logging.getLogger(__name__)

class InfrastructureService:
    def __init__(self):
        self.infrastructure_model = Infrastructure()
        self.user_model = User()
    
    def get_infrastructure(self,user_id):
        user = self.user_model.objects.get(id=user_id)
        logger.info(user.infrastructures.all())
        return self.infrastructure_model.objects.filter(user_id=user_id).values_list()

    def create_infrastructure(self, user_id, infra_data):
        infra = self.infrastructure_model(user_id=user_id, **infra_data)
        infra.save()
        return infra
    
    def delete_infrastructure(self, user_id, infra_id):
        infra = self.infrastructure_model.objects.filter(user_id=user_id, id=infra_id)
        if infra.exists():
            infra.delete()
            return True
        return False
    
    def update_infrastructure(self, user_id, infra_id, update_data):
        infra = self.infrastructure_model.objects.filter(user_id=user_id, id=infra_id)
        if infra.exists():
            infra.update(**update_data)
            return infra.first()
        return None