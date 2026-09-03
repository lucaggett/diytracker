"""Errors shared by the service layer and the routes above it."""


class AdminError(Exception):
    """A user-facing failure (unknown record, schema drift, missing file).

    Raised by service functions instead of aborting or flashing, so the caller
    decides how it surfaces: the queue routes turn it into a flash message.
    Named for the admin tool it was introduced for — that tool now lives in
    the diytracker-admin repo, and this stayed behind with queue_review.
    """
