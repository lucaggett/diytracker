from datetime import datetime

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from sqlalchemy.orm import joinedload

from diytracker.forms import (
    ClaimEventForm,
    DeleteLabelForm,
    LabelForm,
    NotifyToggleForm,
    UnclaimEventForm,
)
from diytracker.models import Event, Label, Venue, db
from diytracker.services.audit import record
from diytracker.services.auth import current_user, promoter_required
from diytracker.services.cache import bust_cache
from diytracker.services.events import upcoming_filter
from diytracker.services.i18n import gettext as _
from diytracker.services.labels import (
    label_stats,
    likely_queue_matches,
    owned_labels,
    suggest_label_events,
    unique_slug,
)
from diytracker.services.search import like_patterns, normalise_query
from diytracker.services.seo import canonical_url
from diytracker.services.uploads import UPLOAD_FOLDER, save_flyer_file

bp = Blueprint("promoter", __name__, url_prefix="/promoter")


def _owned_label_or_403(label_id):
    label = Label.query.get_or_404(label_id)
    user = current_user()
    if label.promoter_id != user.id:
        abort(403)
    return label


@bp.route("/")
@promoter_required
def dashboard():
    user = current_user()
    labels = Label.query.filter_by(promoter_id=user.id).order_by(Label.name.asc()).all()
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
        queue_matches=likely_queue_matches(labels),
        notify_enabled=user.notify_label_events,
        delete_form=DeleteLabelForm(),
        unclaim_form=UnclaimEventForm(),
        notify_form=NotifyToggleForm(),
        now=datetime.now(),
    )


@bp.route("/notifications", methods=["POST"])
@promoter_required
def toggle_notifications():
    form = NotifyToggleForm()
    if not form.validate_on_submit():
        abort(400)
    user = current_user()
    user.notify_label_events = not user.notify_label_events
    db.session.commit()
    flash(
        _("Email notifications enabled.")
        if user.notify_label_events
        else _("Email notifications disabled.")
    )
    return redirect(url_for("promoter.dashboard"))


def _claim_form(user):
    form = ClaimEventForm()
    form.label_id.choices = [
        (str(label.id), label.name) for label in owned_labels(user)
    ]
    return form


CLAIM_PAGE_SIZE = 50


@bp.route("/claim")
@promoter_required
def claim_events():
    user = current_user()
    form = _claim_form(user)
    # Midnight, not now: a promoter claiming tonight's show at 21:00 must still
    # see it. upcoming_filter() then keeps a running festival in the list too.
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    unclaimed = (
        Event.query.options(joinedload(Event.venue))
        .filter(Event.label_id.is_(None), upcoming_filter(today))
        .order_by(Event.date.asc())
    )
    query = unclaimed
    raw_q = normalise_query(request.args.get("q", ""))
    if raw_q:
        query = query.outerjoin(Venue, Event.venue_id == Venue.id)
        for pattern in like_patterns(raw_q):
            query = query.filter(
                db.or_(
                    Event.name.ilike(pattern, escape="\\"),
                    Event.acts.ilike(pattern, escape="\\"),
                    Venue.name.ilike(pattern, escape="\\"),
                    Venue.city.ilike(pattern, escape="\\"),
                )
            )
    page = request.args.get("page", 1, type=int)
    pagination = query.paginate(page=page, per_page=CLAIM_PAGE_SIZE, error_out=False)

    # "Likely yours": fuzzy-match the user's label names against unfiltered
    # upcoming unlabelled events, so a suggestion can't be hidden by q/page.
    suggestions = suggest_label_events(owned_labels(user), unclaimed)
    return render_template(
        "promoter_claim.html",
        events=pagination.items,
        pagination=pagination,
        q=raw_q,
        suggestions=suggestions,
        form=form,
    )


@bp.route("/events/<int:event_id>/claim", methods=["POST"])
@promoter_required
def claim_event(event_id):
    user = current_user()
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
    record(
        "label.claim",
        "event",
        event.id,
        actor=user,
        detail=f"label_id={label.id} label={label.name!r} event={event.name!r}",
    )
    db.session.commit()
    bust_cache()
    flash(_("Event claimed for %(label)s!", label=label.name))
    return redirect(url_for("promoter.dashboard"))


@bp.route("/events/<int:event_id>/unclaim", methods=["POST"])
@promoter_required
def unclaim_event(event_id):
    form = UnclaimEventForm()
    if not form.validate_on_submit():
        abort(400)
    user = current_user()
    event = Event.query.get_or_404(event_id)
    # Strictly per-account, like claiming: admins get no special treatment.
    if event.label is None or event.label.promoter_id != user.id:
        abort(403)
    record(
        "label.unclaim",
        "event",
        event.id,
        actor=user,
        detail=(
            f"label_id={event.label_id} label={event.label.name!r} event={event.name!r}"
        ),
    )
    event.label_id = None
    db.session.commit()
    bust_cache()
    flash(_("Event released from the label."))
    return redirect(url_for("promoter.dashboard"))


def _apply_profile_fields(label, form):
    label.website = (form.website.data or "").strip() or None
    label.link_social_1 = (form.link_social_1.data or "").strip() or None
    label.link_social_2 = (form.link_social_2.data or "").strip() or None
    label.contact_email = (form.contact_email.data or "").strip() or None


def _duplicate_name(name, exclude_id=None):
    query = Label.query.filter(db.func.lower(Label.name) == name.lower())
    if exclude_id is not None:
        query = query.filter(Label.id != exclude_id)
    return query.first() is not None


@bp.route("/labels/new", methods=["GET", "POST"])
@promoter_required
def new_label():
    form = LabelForm()
    user = current_user()
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
            description=(form.description.data or "").strip() or None,
            promoter_id=user.id,
        )
        _apply_profile_fields(label, form)
        db.session.add(label)
        db.session.flush()
        record(
            "label.create",
            "label",
            label.id,
            actor=user,
            detail=f"name={label.name!r}",
        )
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
        elif form.remove_logo.data:
            # Clears the reference only; the file stays on disk, matching the
            # replace path, which never deletes old files either.
            label.logo = None
        label.description = (form.description.data or "").strip() or None
        _apply_profile_fields(label, form)
        record(
            "label.edit",
            "label",
            label.id,
            actor=current_user(),
            detail=f"name={label.name!r}",
        )
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
    # Detach events first — FK enforcement would otherwise reject the delete.
    detached = Event.query.filter_by(label_id=label.id).update({"label_id": None})
    record(
        "label.delete",
        "label",
        label.id,
        actor=current_user(),
        detail=f"name={label.name!r} detached_events={detached}",
    )
    db.session.delete(label)
    db.session.commit()
    bust_cache()
    flash(_("Label deleted."))
    return redirect(url_for("promoter.dashboard"))
