import functools

from flask import session, request, url_for, redirect, abort

from diytracker.models import db, Submitter


def safe_redirect_target(target):
    """Return *target* if it is a local path, else None.

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


def login_required(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("auth.login", next=request.path))
        if db.session.get(Submitter, session["user_id"]) is None:
            session.clear()
            return redirect(url_for("auth.login", next=request.path))
        return f(*args, **kwargs)

    return decorated


def admin_required(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("auth.login", next=request.path))
        user = db.session.get(Submitter, session["user_id"])
        if not user or not user.is_admin:
            abort(403)
        return f(*args, **kwargs)

    return decorated


def promoter_required(f):
    # Admins pass too, so they can inspect any promoter flow.
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("auth.login", next=request.path))
        user = db.session.get(Submitter, session["user_id"])
        if not user or not (user.is_promoter or user.is_admin):
            abort(403)
        return f(*args, **kwargs)

    return decorated
