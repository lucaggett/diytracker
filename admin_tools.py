import os
import random
import smtplib
import ssl
import string
import sys
from email.message import EmailMessage
from models import Submitter
from app import app, db


def generate_password():
    """
    a password generator
    :return: A secure password
    """
    password_characters = list(string.ascii_letters) + list(string.digits)
    return ''.join(random.choices(password_characters, k=32))

def regenerate_password_and_notify():
    """
    Regenerate the password and notify all users of the new password
    """
    # Get admin emails from the DB
    with app.app_context():
        emails = [submitter.email for submitter in Submitter.query.all()]

    new_password = generate_password()

    # Update the password in the environment
    with open("SUBMISSION_PASSWORD_CURRENT", "w") as f:
        f.write(new_password)

    # Notify all users
    EMAIL_SERVER, USERNAME, PASSWORD = open("EMAIL_DATA").read().split(":")
    PASSWORD = PASSWORD.strip()

    for email in emails:
        print(f'Sending email to {email}')
        message = EmailMessage()
        quips = ["Beep Boop", "Hiiii :3"]
        message.set_content(f'The DIY Tracker password has been reset to: {new_password}\n\n{random.choice(quips)},\ndiytracker.ch application server')
        message['Subject'] = 'Password has been reset'
        message['From'] = "diytracker@aggett.ch"
        message['To'] = email

        context = ssl.create_default_context()
        print(f"Attempting to log in with credentials {USERNAME} and {PASSWORD} on {EMAIL_SERVER}")
        with smtplib.SMTP_SSL(EMAIL_SERVER, 465, context=context) as server:
            server.login(USERNAME, PASSWORD)
            server.send_message(message)

    print('Password reset and notifications sent successfully!')


def add_user(email):
    """
    Add a new user to the admin list
    :param email: Email of the user to add
    """
    with app.app_context():
        new_user = Submitter(email=email)
        db.session.add(new_user)
        db.session.commit()
    print(f'User {email} added successfully!')

    # Welcome the user and notify them of the current password
    diytracker_password = open("SUBMISSION_PASSWORD_CURRENT").read().strip()
    EMAIL_SERVER, USERNAME, PASSWORD = open("EMAIL_DATA").read().split(":")
    PASSWORD = PASSWORD.strip()

    message = EmailMessage()
    message.set_content(f"Welcome to diytracker.ch!\n\n"
                        f"You may now submit events at https://diytracker.ch/submit using the password {diytracker_password}.\n\n"
                        f"The password will be reset periodically, and you will be notified of the new password via email.\n\n"
                        f"Happy Submitting\n"
                        f"diytracker.ch application server\n\n"
                        f"If you have any questions, please contact Luc at luc@aggett.com")
    message['Subject'] = 'Welcome new diytracker.ch submitter'
    message['From'] = "diytracker@aggett.ch"
    message['To'] = email

    context = ssl.create_default_context()
    print(f"Attempting to log in with credentials {USERNAME} and {PASSWORD} on {EMAIL_SERVER}")
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
    if len(sys.argv) == 1:
        print("Admin Tools CLI")
        print("1. Regenerate password and notify all users")
        print("2. Add a new user")
        print("3. Remove a user")
        print("4. List all users")
        choice = input("Enter your choice: ")

        if choice == '1':
            regenerate_password_and_notify()

        elif choice == '2':
            email = input("Enter the email of the new user: ")
            add_user(email)

        elif choice == '3':
            email = input("Enter the email of the user to remove: ")
            remove_user(email)

        elif choice == '4':
            list_users()

    elif len(sys.argv) == 2:
        if sys.argv[1] == 'regenerate':
            regenerate_password_and_notify()
        elif sys.argv[1] == 'list':
            list_users()
        else:
            print("Invalid argument")
    else:
        print("Too many arguments")

