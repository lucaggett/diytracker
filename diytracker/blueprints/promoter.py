from datetime import datetime

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    session,
    url_for,
)

from sqlalchemy.orm import joinedload

from diytracker.forms import ClaimEventForm, DeleteLabelForm, LabelForm
from diytracker.models import db, Event, Label, Submitter
from diytracker.services.auth import promoter_required
from diytracker.services.cache import bust_cache
from diytracker.services.i18n import gettext as _
from diytracker.services.labels import label_stats, unique_slug
from diytracker.services.seo import canonical_url
from diytracker.services.uploads import UPLOAD_FOLDER, save_flyer_file

bp = Blueprint("promoter", __name__, url_prefix="/promoter")


def _current_user():
    return db.session.get(Submitter, session["user_id"])


def _owned_label_or_403(label_id):
    label = Label.query.get_or_404(label_id)
    user = _current_user()
    if label.promoter_id != user.id and not user.is_admin:
        abort(403)
    return label


@bp.route("/")
@promoter_required
def dashboard():
    user = _current_user()
    labels_query = Label.query
    if not user.is_admin:
        labels_query = labels_query.filter_by(promoter_id=user.id)
    labels = labels_query.order_by(Label.name.asc()).all()
    stats = label_stats(user)
    share_urls = {
        row["event"].id: canonical_url(
            url_for("public.event_page", event_id=row["event"].id)
        )
        for row in stats
    }
    return render_template(
        "promoter_dashboard.html",
        labels=labels,
        stats=stats,
        share_urls=share_urls,
        delete_form=DeleteLabelForm(),
        now=datetime.now(),
    )


def _own_labels(user):
    """The labels *user* may claim events for (admins: all labels)."""
    query = Label.query
    if not user.is_admin:
        query = query.filter_by(promoter_id=user.id)
    return query.order_by(Label.name.asc()).all()


def _claim_form(user):
    form = ClaimEventForm()
    form.label_id.choices = [(str(label.id), label.name) for label in _own_labels(user)]
    return form


@bp.route("/claim")
@promoter_required
def claim_events():
    user = _current_user()
    form = _claim_form(user)
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    events = (
        Event.query.options(joinedload(Event.venue))
        .filter(Event.label_id.is_(None), Event.date >= today)
        .order_by(Event.date.asc())
        .all()
    )
    return render_template("promoter_claim.html", events=events, form=form)


@bp.route("/events/<int:event_id>/claim", methods=["POST"])
@promoter_required
def claim_event(event_id):
    user = _current_user()
    event = Event.query.get_or_404(event_id)
    form = _claim_form(user)
    if not form.validate_on_submit():
        abort(400)
    # Choices are restricted to the user's own labels, so a passing
    # validation already implies ownership; fetch for the flash message.
    label = db.session.get(Label, int(form.label_id.data))
    if event.label_id is not None:
        flash(_("This event already belongs to a label."))
        return redirect(url_for("promoter.claim_events"))
    event.label_id = label.id
    db.session.commit()
    bust_cache()
    flash(_("Event claimed for %(label)s!", label=label.name))
    return redirect(url_for("promoter.dashboard"))


def _duplicate_name(name, exclude_id=None):
    query = Label.query.filter(db.func.lower(Label.name) == name.lower())
    if exclude_id is not None:
        query = query.filter(Label.id != exclude_id)
    return query.first() is not None


@bp.route("/labels/new", methods=["GET", "POST"])
@promoter_required
def new_label():
    form = LabelForm()
    if form.validate_on_submit():
        name = form.name.data.strip()
        if _duplicate_name(name):
            flash(_("A label with this name already exists."))
            return render_template("label_form.html", form=form, label=None)
        logo = save_flyer_file(
            form.logo.data, current_app.config.get("UPLOAD_FOLDER", UPLOAD_FOLDER)
        )
        label = Label(
            name=name,
            slug=unique_slug(name),
            logo=logo,
            promoter_id=session["user_id"],
        )
        db.session.add(label)
        db.session.commit()
        bust_cache()
        flash(_("Label created!"))
        return redirect(url_for("promoter.dashboard"))
    return render_template("label_form.html", form=form, label=None)


@bp.route("/labels/<int:label_id>/edit", methods=["GET", "POST"])
@promoter_required
def edit_label(label_id):
    label = _owned_label_or_403(label_id)
    form = LabelForm(obj=label)
    if form.validate_on_submit():
        name = form.name.data.strip()
        if _duplicate_name(name, exclude_id=label.id):
            flash(_("A label with this name already exists."))
            return render_template("label_form.html", form=form, label=label)
        if name != label.name:
            label.name = name
            label.slug = unique_slug(name, exclude_id=label.id)
        logo = save_flyer_file(
            form.logo.data, current_app.config.get("UPLOAD_FOLDER", UPLOAD_FOLDER)
        )
        if logo:
            label.logo = logo
        db.session.commit()
        bust_cache()
        flash(_("Label updated!"))
        return redirect(url_for("promoter.dashboard"))
    return render_template("label_form.html", form=form, label=label)


@bp.route("/labels/<int:label_id>/delete", methods=["POST"])
@promoter_required
def delete_label(label_id):
    form = DeleteLabelForm()
    if not form.validate_on_submit():
        abort(400)
    label = _owned_label_or_403(label_id)
    # SQLite FK enforcement is off in this app, so detach events explicitly.
    Event.query.filter_by(label_id=label.id).update({"label_id": None})
    db.session.delete(label)
    db.session.commit()
    bust_cache()
    flash(_("Label deleted."))
    return redirect(url_for("promoter.dashboard"))
