import os
import smtplib
import ssl
from email.message import EmailMessage

from dotenv import load_dotenv

load_dotenv()

from models import Submitter
from app import app, db


# ── helpers ───────────────────────────────────────────────────────────────────

def _send_email(to_address, subject, body):
    email_server = os.environ['EMAIL_SERVER']
    username = os.environ['EMAIL_USERNAME']
    password = os.environ['EMAIL_PASSWORD']

    message = EmailMessage()
    message.set_content(body)
    message['Subject'] = subject
    message['From'] = "info@diytracker.ch"
    message['To'] = to_address

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(email_server, 465, context=context) as server:
        server.login(username, password)
        server.send_message(message)
    print(f'  Email sent to {to_address}')


def _get_all_users():
    with app.app_context():
        return Submitter.query.order_by(Submitter.email).all()


def _print_user_row(i, user):
    admin_flag = ' [ADMIN]' if user.is_admin else '       '
    pw_flag    = ' [pw]' if user.password_hash else ' [NO PW]'
    print(f"  {i:>2}.  {admin_flag}{pw_flag}  {user.email}")


def _print_user_list(users):
    print()
    print(f"  {'':>4}  {'':7}  {'':5}  email")
    print(f"  {'-'*50}")
    for i, user in enumerate(users, 1):
        _print_user_row(i, user)
    print()


def _pick_user(prompt="Select a user by number: "):
    """Show the user list and return a (user_id, email) tuple for the chosen row."""
    users = _get_all_users()
    if not users:
        print("  No users found.")
        return None
    _print_user_list(users)
    raw = input(prompt).strip()
    try:
        idx = int(raw) - 1
        if not (0 <= idx < len(users)):
            raise ValueError
    except ValueError:
        print("  Invalid selection.")
        return None
    chosen = users[idx]
    return chosen.id, chosen.email


# ── actions ───────────────────────────────────────────────────────────────────

def set_password(user_id, password):
    with app.app_context():
        user = db.session.get(Submitter, user_id)
        user.set_password(password)
        db.session.commit()
        print(f'  Password updated for {user.email}')


def set_admin(user_id, is_admin):
    with app.app_context():
        user = db.session.get(Submitter, user_id)
        user.is_admin = is_admin
        db.session.commit()
        status = 'granted' if is_admin else 'revoked'
        print(f'  Admin status {status} for {user.email}')


def remove_user(user_id, email):
    confirm = input(f"  Delete {email}? This cannot be undone. (yes/no): ").strip().lower()
    if confirm != 'yes':
        print("  Cancelled.")
        return
    with app.app_context():
        user = db.session.get(Submitter, user_id)
        db.session.delete(user)
        db.session.commit()
    print(f'  {email} removed.')


def _send_invite_email(email, token):
    _send_email(
        email,
        'Set your diytracker.ch password',
        f"You've been invited to diytracker.ch!\n\n"
        f"Set your password using this link (valid for 7 days):\n"
        f"https://diytracker.ch/set-password/{token}\n\n"
        f"If you have any questions, contact Luc at luc@aggett.com"
    )


def add_user():
    email = input("  Email: ").strip()
    if not email:
        print("  Cancelled.")
        return

    with app.app_context():
        if Submitter.query.filter_by(email=email).first():
            print(f"  A user with that email already exists.")
            return
        new_user = Submitter(email=email)
        token = new_user.generate_invite_token()
        db.session.add(new_user)
        db.session.commit()
        print(f'  User {email} created.')

    _send_invite_email(email, token)


def edit_user():
    """Select a user, then choose what to change."""
    result = _pick_user("Select user to edit: ")
    if result is None:
        return
    user_id, email = result

    print(f"\n  Editing: {email}")
    print("    a.  Set password")
    print("    b.  Toggle admin status")
    print("    c.  Delete user")
    print("    d.  Resend invite")
    print("    q.  Cancel")
    action = input("  Action: ").strip().lower()

    if action == 'a':
        password = input("  New password: ").strip()
        if password:
            set_password(user_id, password)
        else:
            print("  Cancelled.")
    elif action == 'd':
        with app.app_context():
            user = db.session.get(Submitter, user_id)
            token = user.generate_invite_token()
            db.session.commit()
        _send_invite_email(email, token)
        print(f'  Invite resent to {email}.')
    elif action == 'b':
        with app.app_context():
            user = db.session.get(Submitter, user_id)
            current = user.is_admin
        new_state = not current
        label = "grant" if new_state else "revoke"
        confirm = input(f"  {label.capitalize()} admin for {email}? (y/n): ").strip().lower()
        if confirm == 'y':
            set_admin(user_id, new_state)
    elif action == 'c':
        remove_user(user_id, email)
    else:
        print("  Cancelled.")


# ── main loop ─────────────────────────────────────────────────────────────────

def main():
    while True:
        print("\n── diytracker admin ──────────────────")
        print("  1.  List users")
        print("  2.  Add user")
        print("  3.  Edit / delete user")
        print("  q.  Quit")
        choice = input("Choice: ").strip().lower()

        if choice == '1':
            users = _get_all_users()
            if users:
                _print_user_list(users)
            else:
                print("  No users.")
        elif choice == '2':
            add_user()
        elif choice == '3':
            edit_user()
        elif choice == 'q':
            break
        else:
            print("  Unknown option.")


if __name__ == '__main__':
    main()
