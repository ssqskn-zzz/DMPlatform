class AppError(Exception):
    """Base class for expected application errors mapped to HTTP responses."""

    status_code = 400


class BadRequestError(AppError):
    status_code = 400


class AuthenticationError(AppError):
    status_code = 401


class PermissionDeniedError(AppError):
    status_code = 403


class NotFoundError(AppError):
    status_code = 404


class ConflictError(AppError):
    status_code = 409
