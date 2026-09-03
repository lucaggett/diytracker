"""Session helpers: who is logged in, and the three access decorators."""

import functools

from flask import abort, g, redirect, request, session, url_for

from diytracker.models import Submitter, db


def safe_redirect_target(target):
    r"""Return *target* if it is a local path, else None.

    Rejects protocol-relative URLs (`//evil.com`) and backslash variants
    (`/\\evil.com`) that browsers normalise into cross-origin redirects.
    """
    if (
        target
        and target.startswith("/")
        and not target.startswith("//")
        and "\\" not in target
    ):
        return target
    return None


def current_user():
    """The logged-in Submitter, or None — cached on `g` for the request.

    A single admin POST used to run this lookup three or four times: once in
    the decorator, once or twice in the view to name an audit actor, and once
    more in the header's context processor. They all want the same row.

    The cache is filled on first read, so a view that changes `session
    ["user_id"]` (login, set-password) must not call this before doing so;
    both redirect immediately instead.
    """
    if "_current_user" not in g:
        user_id = session.get("user_id")
        g._current_user = db.session.get(Submitter, user_id) if user_id else None  # noqa: SLF001 - our own attribute on flask.g
    return g._current_user  # noqa: SLF001


def _require(predicate):
    """Build a decorator that sends anonymous visitors to the login page and
    403s a logged-in user who fails *predicate*. A session naming a user that
    no longer exists is cleared rather than 403'd — the account was deleted
    out from under a live cookie, and asking them to log in again is the
    honest answer.
    """

    def decorator(f):
        @functools.wraps(f)
        def decorated(*args, **kwargs):
            if "user_id" not in session:
                return redirect(url_for("auth.login", next=request.path))
            user = current_user()
            if user is None:
                session.clear()
                g.pop("_current_user", None)
                return redirect(url_for("auth.login", next=request.path))
            if not predicate(user):
                abort(403)
            return f(*args, **kwargs)

        return decorated

    return decorator


login_required = _require(lambda user: True)  # noqa: ARG005 - predicate protocol takes the user
admin_required = _require(lambda user: user.is_admin)
# Admins pass too, so they can inspect any promoter flow.
promoter_required = _require(lambda user: user.is_promoter or user.is_admin)
