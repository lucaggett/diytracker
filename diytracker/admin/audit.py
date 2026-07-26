"""Read access to the ActionLog audit trail for the admin TUI.

Plain functions returning dataclasses (never ORM objects), shared-layer
style like the other admin modules. Writing happens in services/audit.py.
"""

from dataclasses import dataclass

from sqlalchemy import or_

from diytracker.models import ActionLog
from diytracker.services.search import like_patterns, normalise_query


@dataclass
class ActionRow:
    id: int
    when: str  # UTC, YYYY-MM-DD HH:MM
    actor: str
    action: str
    target: str  # "event #12"
    detail: str


def _row(rec):
    return ActionRow(
        id=rec.id,
        when=f"{rec.created_at:%Y-%m-%d %H:%M}" if rec.created_at else "",
        actor=rec.actor,
        action=rec.action,
        target=f"{rec.target_type} #{rec.target_id}",
        detail=rec.detail or "",
    )


def list_actions(limit=200, action=None, search=None):
    """Newest-first audit rows, optionally filtered to one action name
    (or an action prefix like "label." when it ends with a dot).

    *search* ANDs its terms across actor, target and detail, and is combined
    with the action filter rather than replacing it.
    """
    query = ActionLog.query
    if action:
        if action.endswith("."):
            query = query.filter(ActionLog.action.startswith(action))
        else:
            query = query.filter(ActionLog.action == action)
    for pattern in like_patterns(normalise_query(search)):
        query = query.filter(
            or_(
                ActionLog.actor.ilike(pattern, escape="\\"),
                ActionLog.action.ilike(pattern, escape="\\"),
                ActionLog.target_type.ilike(pattern, escape="\\"),
                ActionLog.detail.ilike(pattern, escape="\\"),
            )
        )
    rows = query.order_by(ActionLog.created_at.desc(), ActionLog.id.desc()).limit(limit)
    return [_row(rec) for rec in rows]
