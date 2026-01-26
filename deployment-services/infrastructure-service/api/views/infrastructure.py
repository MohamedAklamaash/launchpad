from django.views.decorators.csrf import csrf_exempt
from rest_framework.decorators import api_view
from rest_framework.response import Response
from api.services.infrastructure import InfrastructureService
from django.http import HttpRequest

class InfrastructureView:
    def __init__(self):
        self.infrastructure_service = InfrastructureService()

    @csrf_exempt
    @api_view(['GET'])
    def get_infrastructure(self,request:HttpRequest):
        return Response(self.infrastructure_service.get_infrastructure(user_id=request.user.id))
    
    @csrf_exempt
    @api_view(['POST'])
    def create_infrastructure(self,request:HttpRequest):
        infra_data = request.data
        infra = self.infrastructure_service.create_infrastructure(user_id=request.user.id, infra_data=infra_data)
        return Response({'id': infra.id, 'message': 'Infrastructure created successfully'})
    
    @csrf_exempt
    @api_view(['DELETE'])
    def delete_infrastructure(self,request:HttpRequest, infra_id):
        success = self.infrastructure_service.delete_infrastructure(user_id=request.user.id, infra_id=infra_id)
        if success:
            return Response({'message': 'Infrastructure deleted successfully'})
        return Response({'message': 'Infrastructure not found'}, status=404)
    
    @csrf_exempt
    @api_view(['PUT'])
    def update_infrastructure(self,request:HttpRequest, infra_id):
        update_data = request.data
        infra = self.infrastructure_service.update_infrastructure(user_id=request.user.id, infra_id=infra_id, update_data=update_data)
        if infra:
            return Response({'message': 'Infrastructure updated successfully'})
        return Response({'message': 'Infrastructure not found'}, status=404)