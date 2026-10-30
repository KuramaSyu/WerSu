class FriendlyLoginError(ValueError):
    """Raised when a login identifier (email or username) is already taken.

    The error carries ``field`` so callers can render a targeted
    message ("email already in use" vs "username already taken")
    instead of a generic conflict.
    """

    def __init__(self, field: str, value: str, message: str | None = None):
        """Store ``field`` and ``value`` and format a default message.

        Args:
            field: the column the duplicate landed on, e.g.
                ``"email"`` or ``"username"``.
            value: the offending value the caller submitted.
            message: optional message override; when ``None`` the
                default ``"<field> is already taken: <value>"`` is
                used.
        """
        self.field = field
        self.value = value
        super().__init__(message or f"{field} is already taken: {value}")
