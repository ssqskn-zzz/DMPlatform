class AppError(Exception):
    """Base class for expected application errors mapped to HTTP responses."""

    status_code = 400


class BadRequestError(AppError):
    status_code = 400


class NotFoundError(AppError):
    status_code = 404


class ConflictError(AppError):
    status_code = 409
