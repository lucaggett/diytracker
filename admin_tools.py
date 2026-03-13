import os
import smtplib
import ssl
import uuid
from email.message import EmailMessage

from dotenv import load_dotenv

load_dotenv()

from models import Submitter
from app import app, db


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
    print(f"Attempting to log in as {username} on {email_server}")
    with smtplib.SMTP_SSL(email_server, 465, context=context) as server:
        server.login(username, password)
        server.send_message(message)
    print(f'Email sent to {to_address}')


def set_password(email, password):
    """Set a password for an existing submitter."""
    with app.app_context():
        user = Submitter.query.filter_by(email=email).first()
        if user is None:
            print(f"No submitter found for email {email}")
            return
        user.set_password(password)
        db.session.commit()
    print(f'Password set for {email}')


def set_admin(email, is_admin):
    """Grant or revoke admin status for a submitter."""
    with app.app_context():
        user = Submitter.query.filter_by(email=email).first()
        if user is None:
            print(f"No submitter found for email {email}")
            return
        user.is_admin = is_admin
        db.session.commit()
    status = 'granted' if is_admin else 'revoked'
    print(f'Admin status {status} for {email}')


def add_user(email, password):
    """Add a new submitter and send a welcome email with the login URL."""
    with app.app_context():
        new_user = Submitter(email=email)
        new_user.set_password(password)
        db.session.add(new_user)
        db.session.commit()

    login_link = "https://diytracker.ch/login"
    _send_email(
        email,
        'Welcome new diytracker.ch submitter!',
        f"Welcome to diytracker.ch!\n\n"
        f"You can now submit and review events by logging in at:\n{login_link}\n\n"
        f"Your login email: {email}\n\n"
        f"If you have any questions, please contact Luc at luc@aggett.com"
    )
    print(f'Welcome email sent to {email} successfully!')


def remove_user(email):
    """Remove a submitter."""
    with app.app_context():
        user = Submitter.query.filter_by(email=email).first()
        if user is None:
            print(f'No user found for email {email}')
            return
        db.session.delete(user)
        db.session.commit()
    print(f'User {email} removed successfully!')


def list_users():
    """List all submitters."""
    with app.app_context():
        users = Submitter.query.all()
        for user in users:
            admin_flag = ' [ADMIN]' if user.is_admin else ''
            has_pw = ' [has password]' if user.password_hash else ' [no password]'
            print(f"{user.email}{admin_flag}{has_pw}")


if __name__ == '__main__':
    print("Admin Tools CLI")
    print("1. Set password for a user")
    print("2. Add a new user")
    print("3. Remove a user")
    print("4. List all users")
    print("5. Grant/revoke admin status")
    choice = input("Enter your choice: ")

    if choice == '1':
        email = input("Enter the email of the user: ")
        password = input("Enter the new password: ")
        set_password(email, password)

    elif choice == '2':
        email = input("Enter the email of the new user: ")
        password = input("Enter the password for the new user: ")
        add_user(email, password)

    elif choice == '3':
        email = input("Enter the email of the user to remove: ")
        remove_user(email)

    elif choice == '4':
        list_users()

    elif choice == '5':
        email = input("Enter the email of the user: ")
        action = input("Grant or revoke admin? (grant/revoke): ").strip().lower()
        set_admin(email, action == 'grant')
