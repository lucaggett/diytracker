from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from diytracker.forms import LoginForm, SetPasswordForm
from diytracker.models import db, Submitter, utcnow
from diytracker.services.auth import safe_redirect_target
from diytracker.services.i18n import gettext as _
from diytracker.services.limits import limiter

bp = Blueprint("auth", __name__)


@bp.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute", methods=["POST"])
def login():
    form = LoginForm()
    if form.validate_on_submit():
        user = Submitter.query.filter_by(email=form.email.data).first()
        if user and user.check_password(form.password.data):
            # Drop whatever was in the pre-login session before elevating it,
            # so a session cookie an attacker managed to plant can't be
            # ridden into the authenticated session. The language choice is
            # a visitor preference, not a credential, so it survives.
            lang = session.get("lang")
            session.clear()
            if lang:
                session["lang"] = lang
            session["user_id"] = user.id
            session.permanent = True
            next_url = safe_redirect_target(request.args.get("next", ""))
            if next_url:
                return redirect(next_url)
            return redirect(url_for("public.calendar_view"))
        current_app.logger.warning(
            "Failed login attempt for %r from %s", form.email.data, request.remote_addr
        )
        flash(_("Invalid email or password."))
    return render_template("login.html", form=form)


# POST-only so a cross-site <img>/link can't log the user out; the CSRF
# token on the little logout forms is validated globally by CSRFProtect.
@bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("public.calendar_view"))


@bp.route("/set-password/<token>", methods=["GET", "POST"])
def set_password(token):
    user = Submitter.query.filter_by(invite_token=token).first()
    if not user or not user.invite_token_expiry or user.invite_token_expiry < utcnow():
        flash(_("This invite link is invalid or has expired."))
        return redirect(url_for("auth.login"))
    form = SetPasswordForm()
    if form.validate_on_submit():
        user.set_password(form.password.data)
        user.clear_invite_token()
        db.session.commit()
        lang = session.get("lang")
        session.clear()
        if lang:
            session["lang"] = lang
        session["user_id"] = user.id
        session.permanent = True
        return redirect(url_for("submissions.submit_event_link"))
    return render_template("set_password.html", form=form)
