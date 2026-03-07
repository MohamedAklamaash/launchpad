import logging

logger = logging.getLogger(__name__)


class NotificationService:
    """Send notifications to users about infrastructure events"""
    
    @staticmethod
    def send_provision_success(user_id: str, infra_id: str, infra_name: str):
        """Notify user of successful provisioning"""
        message = f"Infrastructure '{infra_name}' ({infra_id}) successfully provisioned."
        logger.info(f"[NOTIFICATION] {user_id}: {message}")
        # TODO: Implement actual notification (email, webhook, websocket, etc.)
    
    @staticmethod
    def send_provision_failure(user_id: str, infra_id: str, infra_name: str, error: str):
        """Notify user of provisioning failure"""
        message = f"""
Infrastructure '{infra_name}' ({infra_id}) failed during provisioning.
Reason: {error}
"""
        logger.error(f"[NOTIFICATION] {user_id}: {message}")
        # TODO: Implement actual notification
    
    @staticmethod
    def send_destroy_success(user_id: str, infra_id: str, infra_name: str):
        """Notify user of successful destruction"""
        message = f"Infrastructure '{infra_name}' ({infra_id}) successfully destroyed."
        logger.info(f"[NOTIFICATION] {user_id}: {message}")
        # TODO: Implement actual notification
    
    @staticmethod
    def send_destroy_failure(user_id: str, infra_id: str, infra_name: str, error: str):
        """Notify user of destruction failure"""
        message = f"""
Infrastructure '{infra_name}' ({infra_id}) failed during destruction.
Reason: {error}
Manual cleanup may be required.
"""
        logger.error(f"[NOTIFICATION] {user_id}: {message}")
        # TODO: Implement actual notification
