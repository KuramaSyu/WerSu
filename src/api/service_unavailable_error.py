class ServiceUnavailableError(Exception):
    """Exception raised when a service is unavailable."""

    def __init__(self, name: str, address: str):
        self.name = name
        self.address = address
        super().__init__(f"{name} is unavailable at {address}")