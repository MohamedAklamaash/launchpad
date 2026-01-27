
class HttpError(Exception):
    def __init__(self, message="Error", status_code=400, details=None):
        self.message = message
        self.status_code = status_code
        self.details = details
        super().__init__(message)