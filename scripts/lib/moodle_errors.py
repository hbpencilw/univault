"""Shared error taxonomy for the UniVault Moodle connector.

Every failure carries a stable category string used in reports, exit codes and
the sync journal. Categories follow docs/PLAN_MOODLE.md section 16.
"""


class MoodleError(Exception):
    category = "UNKNOWN"

    def __init__(self, message, *, detail=None):
        super().__init__(message)
        self.message = message
        self.detail = detail

    def to_dict(self):
        return {"category": self.category, "message": self.message, "detail": self.detail}


class BlockedUrl(MoodleError):
    category = "BLOCKED_URL"


class AuthExpired(MoodleError):
    category = "AUTH_EXPIRED"


class PermissionDenied(MoodleError):
    category = "PERMISSION_DENIED"


class AjaxUnsupported(MoodleError):
    category = "AJAX_UNSUPPORTED"


class ParseFailed(MoodleError):
    category = "PARSE_FAILED"


class NetworkFailed(MoodleError):
    category = "NETWORK_FAILED"


class RateLimited(MoodleError):
    category = "RATE_LIMITED"


class ServerError(MoodleError):
    category = "SERVER_ERROR"


class ContentMismatch(MoodleError):
    category = "CONTENT_MISMATCH"


class DiskFull(MoodleError):
    category = "DISK_FULL"


class FileWriteFailed(MoodleError):
    category = "FILE_WRITE_FAILED"


class ResourceUnavailable(MoodleError):
    category = "RESOURCE_UNAVAILABLE"
