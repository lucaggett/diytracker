"""User management logic. Runs inside a caller-provided app context."""

from dataclasses import dataclass

from diytracker.admin.core import AdminError
from diytracker.models import Submitter, db


@dataclass
class UserRow:
    email: str
    is_admin: bool
    is_promoter: bool
    has_password: bool


def _row(user):
    return UserRow(
        email=user.email,
        is_admin=user.is_admin,
        is_promoter=user.is_promoter,
        has_password=bool(user.password_hash),
    )


def _get_user(email):
    user = Submitter.query.filter_by(email=email).first()
    if not user:
        raise AdminError(f"No user found with email {email!r}.")
    return user


def list_users():
    return [_row(u) for u in Submitter.query.order_by(Submitter.email).all()]


def add_user(email):
    """Create the user and return their invite token (email not sent here)."""
    if Submitter.query.filter_by(email=email).first():
        raise AdminError(f"A user with email {email!r} already exists.")
    user = Submitter(email=email)
    token = user.generate_invite_token()
    db.session.add(user)
    db.session.commit()
    return token


def set_password(email, password):
    user = _get_user(email)
    if not password:
        raise AdminError("No password entered; aborting.")
    user.set_password(password)
    db.session.commit()


def set_flag(email, flag, value):
    """Toggle an access flag; flag is 'is_admin' or 'is_promoter'."""
    if flag not in ("is_admin", "is_promoter"):
        raise AdminError(f"Unknown user flag {flag!r}.")
    user = _get_user(email)
    setattr(user, flag, value)
    db.session.commit()
    return _row(user)


def reissue_invite(email):
    """Generate a fresh invite token and return it (email not sent here)."""
    user = _get_user(email)
    token = user.generate_invite_token()
    db.session.commit()
    return token


def delete_user(email):
    user = _get_user(email)
    # label.promoter_id is NOT NULL, so a label owner can't be deleted
    # without deciding what happens to their labels first.
    if user.labels:
        names = ", ".join(label.name for label in user.labels)
        raise AdminError(f"User owns label(s) {names}; reassign or delete them first.")
    db.session.delete(user)
    db.session.commit()
