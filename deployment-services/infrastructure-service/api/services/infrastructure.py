from api.repositories.infrastructure import InfrastructureRepository
from api.serializers.infrastructure import InfrastructureSerializer
import logging

logger = logging.getLogger(__name__)

class InfrastructureService:
    def __init__(self):
        self.repo = InfrastructureRepository()
    
    def get_all_for_user(self, user_id):
        infras = self.repo.get_all_for_user(user_id)
        return InfrastructureSerializer.serialize_list(infras)

    def get_infrastructure(self, user_id, infra_id):
        infra = self.repo.get_by_id(user_id, infra_id)
        if infra:
            return InfrastructureSerializer.serialize_instance(infra)
        return None

    def create_infrastructure(self, user_id, infra_data):
        infra = self.repo.create(user_id, infra_data)
        return InfrastructureSerializer.serialize_instance(infra)
    
    def delete_infrastructure(self, user_id, infra_id):
        return self.repo.delete(user_id, infra_id)
    
    def update_infrastructure(self, user_id, infra_id, update_data):
        infra = self.repo.update(user_id, infra_id, update_data)
        if infra:
            return InfrastructureSerializer.serialize_instance(infra)
        return None