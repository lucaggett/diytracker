import smtplib
import ssl
import uuid
from email.message import EmailMessage
from models import Submitter
from app import app, db


def regenerate_link_and_notify(email):
    """
    Regenerate the password and notify all users of the new password
    """
    # Get admin emails from the DB
    with app.app_context():
        target_submitter = Submitter.query.filter_by(email=email).first()
        if target_submitter is None:
            print("No submitter found for email", email)
        # Reset UUID
        target_submitter.submission_code = uuid.uuid4()
        db.session.commit()

    # Notify all users
    EMAIL_SERVER, USERNAME, PASSWORD = open("EMAIL_DATA").read().split(":")
    PASSWORD = PASSWORD.strip()
    link = f"https://diytracker.ch/submit/{target_submitter.submission_code}"

    print(f'Sending email to {email}')
    message = EmailMessage()
    message.set_content(f"Your submission link has been reset. You can now submit using this link:\n\n{link} ")
    message['Subject'] = 'diytracker submission link reset'
    message['From'] = "diytracker@aggett.ch"
    message['To'] = email

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(EMAIL_SERVER, 465, context=context) as server:
        server.login(USERNAME, PASSWORD)
        server.send_message(message)


def add_user(email):
    """
    Add a new user to the admin list
    :param email: Email of the user to add
    """
    with app.app_context():
        # Generate a co
        new_user = Submitter(email=email)
        db.session.add(new_user)
        db.session.commit()
        submission_code = new_user.submission_code

    # Then email them, e.g.:
    link = f"https://diytracker.ch/submit/{submission_code}"
    message = EmailMessage()

    # Welcome the user and notify them of the current password
    EMAIL_SERVER, USERNAME, PASSWORD = open("EMAIL_DATA").read().split(":")
    PASSWORD = PASSWORD.strip()

    message = EmailMessage()
    message.set_content(
        f"Welcome to diytracker.ch!\n\n"
        f"You can now submit events using your unique link:\n{link}\n\n"
        f"Keep this link safe or bookmark it.\n\n"
        f"This is an automated message from the diytracker application server\n\n"
        f"If you have any questions, please contact Luc at luc@aggett.com")
    message['Subject'] = 'Welcome new diytracker.ch submitter!'
    message['From'] = "info@diytracker.ch"
    message['To'] = email

    context = ssl.create_default_context()
    print(f"Attempting to log in as {USERNAME} on {EMAIL_SERVER}")
    with smtplib.SMTP_SSL(EMAIL_SERVER, 465, context=context) as server:
        server.login(USERNAME, PASSWORD)
        server.send_message(message)
    print(f'Welcome email sent to {email} successfully!')


def remove_user(email):
    """
    Remove a user from the admin list
    :param email: Email of the user to remove
    """
    with app.app_context():
        user = Submitter.query.filter_by(email=email).first()
        db.session.delete(user)
        db.session.commit()
    print(f'User {email} removed successfully!')


def list_users():
    """
    List all users in the admin list
    """
    with app.app_context():
        users = Submitter.query.all()
        for user in users:
            print(user.email)


if __name__ == '__main__':
    # small admin script to add or remove users to/from the admin list, regenerate the password, etc
    print("Admin Tools CLI")
    print("1. Regenerate password and notify all users")
    print("2. Add a new user")
    print("3. Remove a user")
    print("4. List all users")
    choice = input("Enter your choice: ")

    if choice == '1':
        regenerate_link_and_notify(
            input("Enter the email of the user to reset: ")
        )

    elif choice == '2':
        email = input("Enter the email of the new user: ")
        add_user(email)

    elif choice == '3':
        email = input("Enter the email of the user to remove: ")
        remove_user(email)

    elif choice == '4':
        list_users()
