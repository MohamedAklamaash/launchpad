import logging

logger = logging.getLogger(__name__)

class ECRClient:
    def __init__(self, session):
        self.client = session.client('ecr')
    
    def get_image_uri(self, repository_url, tag):
        return f"{repository_url}:{tag}"
