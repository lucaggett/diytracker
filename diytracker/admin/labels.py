"""Label management logic. Runs inside a caller-provided app context.

The promoter web UI only lets an owner manage their own labels; this is
the admin-side view across all of them — including reassigning a label to
a different promoter, which the web UI deliberately has no path for.
"""

from dataclasses import dataclass

from sqlalchemy import func

from diytracker.admin.core import AdminError
from diytracker.services.audit import record
from diytracker.models import Event, Label, Submitter, db
from diytracker.services.cache import bust_cache
from diytracker.services.labels import unique_slug


@dataclass
class LabelRow:
    id: int
    name: str
    slug: str
    promoter: str
    n_events: int
    has_logo: bool
    description: str


def _row(label, n_events):
    return LabelRow(
        id=label.id,
        name=label.name,
        slug=label.slug,
        promoter=label.promoter.email,
        n_events=n_events,
        has_logo=bool(label.logo),
        description=label.description or "",
    )


def _get_label(label_id):
    label = db.session.get(Label, label_id)
    if not label:
        raise AdminError(f"No label with id {label_id}.")
    return label


def list_labels():
    counts = dict(
        db.session.query(Event.label_id, func.count(Event.id))
        .filter(Event.label_id.isnot(None))
        .group_by(Event.label_id)
        .all()
    )
    return [
        _row(label, counts.get(label.id, 0))
        for label in Label.query.order_by(Label.name).all()
    ]


def _get_promoter(email):
    user = Submitter.query.filter_by(email=email).first()
    if not user:
        raise AdminError(f"No user found with email {email!r}.")
    if not user.is_promoter:
        raise AdminError(f"{email} is not a promoter; grant the flag in Users first.")
    return user


def _check_name(name, exclude_id=None):
    name = (name or "").strip()
    if not name:
        raise AdminError("Label name must not be empty.")
    query = Label.query.filter(func.lower(Label.name) == name.lower())
    if exclude_id is not None:
        query = query.filter(Label.id != exclude_id)
    if query.first():
        raise AdminError(f"A label named {name!r} already exists.")
    return name


def create_label(name, promoter_email):
    name = _check_name(name)
    user = _get_promoter(promoter_email)
    label = Label(name=name, slug=unique_slug(name), promoter_id=user.id)
    db.session.add(label)
    db.session.flush()
    record("label.create", "label", label.id, actor="tui", detail=f"name={name!r}")
    db.session.commit()
    bust_cache()
    return _row(label, 0)


def rename_label(label_id, new_name):
    """Rename and regenerate the slug (the old /label/<slug>/ URL dies)."""
    label = _get_label(label_id)
    name = _check_name(new_name, exclude_id=label.id)
    label.name = name
    label.slug = unique_slug(name, exclude_id=label.id)
    record("label.edit", "label", label.id, actor="tui", detail=f"renamed to {name!r}")
    db.session.commit()
    bust_cache()
    return _row(label, _count_events(label.id))


def set_description(label_id, description):
    label = _get_label(label_id)
    label.description = (description or "").strip() or None
    record("label.edit", "label", label.id, actor="tui", detail="description changed")
    db.session.commit()
    bust_cache()
    return _row(label, _count_events(label.id))


def reassign_label(label_id, promoter_email):
    label = _get_label(label_id)
    user = _get_promoter(promoter_email)
    label.promoter_id = user.id
    record(
        "label.edit",
        "label",
        label.id,
        actor="tui",
        detail=f"reassigned to {promoter_email!r}",
    )
    db.session.commit()
    return _row(label, _count_events(label.id))


def delete_label(label_id):
    """Delete the label, detaching its events (they stay on the calendar).
    Returns (name, n_events_detached)."""
    label = _get_label(label_id)
    name = label.name
    # Detach events first — FK enforcement would otherwise reject the delete.
    n_events = Event.query.filter_by(label_id=label.id).update({"label_id": None})
    record(
        "label.delete",
        "label",
        label.id,
        actor="tui",
        detail=f"name={name!r} detached_events={n_events}",
    )
    db.session.delete(label)
    db.session.commit()
    bust_cache()
    return name, n_events


def _count_events(label_id):
    return Event.query.filter_by(label_id=label_id).count()
