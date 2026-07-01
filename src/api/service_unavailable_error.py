"""Exception raised when a downstream service (e.g. SpiceDB) cannot be reached."""


class ServiceUnavailableError(Exception):
    """Raised when a downstream service is unreachable.

    The exception carries the name of the service and the network
    address that was attempted so callers can build a useful error
    response without re-parsing the message.
    """

    def __init__(self, name: str, address: str):
        """Store ``name`` and ``address`` and format a default message.

        Args:
            name: human-readable service name (e.g. ``"SpiceDB"``).
            address: the address that was attempted (best-effort).
        """
        self.name = name
        self.address = address
        super().__init__(f"{name} is unavailable at {address}")