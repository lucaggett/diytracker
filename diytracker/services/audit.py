"""Writer for the ActionLog audit trail.

record() only adds the row to the session — the caller's commit is the
transaction boundary, so the log entry and the change it describes land
(or roll back) together. No Flask request context is required, which lets
diytracker/admin/ use it from the TUI with actor="tui".
"""

from diytracker.models import ActionLog, db


def record(action, target_type, target_id, actor, detail=None):
    """Stage an audit row. *actor* is either a Submitter instance (web) or
    a plain string like "tui"/"cli"."""
    actor_id = None
    actor_name = actor
    if not isinstance(actor, str):
        actor_id = actor.id
        actor_name = actor.email
    db.session.add(
        ActionLog(
            actor_id=actor_id,
            actor=actor_name,
            action=action,
            target_type=target_type,
            target_id=target_id,
            detail=detail,
        )
    )
