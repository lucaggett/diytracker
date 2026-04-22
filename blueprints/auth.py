from datetime import datetime

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from forms import LoginForm, SetPasswordForm
from models import db, Submitter
from services.i18n import gettext as _

bp = Blueprint('auth', __name__)


@bp.route('/login', methods=['GET', 'POST'])
def login():
    form = LoginForm()
    if form.validate_on_submit():
        user = Submitter.query.filter_by(email=form.email.data).first()
        if user and user.check_password(form.password.data):
            session['user_id'] = user.id
            next_url = request.args.get('next', '')
            if next_url.startswith('/'):
                return redirect(next_url)
            return redirect(url_for('public.calendar_view'))
        flash(_('Invalid email or password.'))
    return render_template('login.html', form=form)


@bp.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('public.calendar_view'))


@bp.route('/set-password/<token>', methods=['GET', 'POST'])
def set_password(token):
    user = Submitter.query.filter_by(invite_token=token).first()
    if not user or not user.invite_token_expiry or user.invite_token_expiry < datetime.utcnow():
        flash(_('This invite link is invalid or has expired.'))
        return redirect(url_for('auth.login'))
    form = SetPasswordForm()
    if form.validate_on_submit():
        user.set_password(form.password.data)
        user.clear_invite_token()
        db.session.commit()
        session['user_id'] = user.id
        return redirect(url_for('submissions.submit_event_link'))
    return render_template('set_password.html', form=form)
