from flask_wtf import FlaskForm
from wtforms import (StringField, TextAreaField, DateField, TimeField,
                     FileField, SubmitField, SelectField, SelectMultipleField,
                     PasswordField)
from wtforms.fields.datetime import DateTimeField
from wtforms.fields.simple import HiddenField, BooleanField
from wtforms.validators import DataRequired, Optional, Email, EqualTo, Length


def get_genre_choices():
    genres = [
        'Hardcore', 'Punk', 'Metal', 'Post-punk', 'EBM', 'Industrial',
        'Synthpop', 'Darkwave', 'Goth', 'New Wave', 'Alternative',
        'Indie', 'Rock', 'Pop', 'Hip Hop', 'Reggae', 'Dub', 'Dancehall',
        'Drum & Bass', 'Dubstep', 'Techno', 'House', 'Trance', 'Electro',
        'Ambient', 'Experimental', 'Noise', 'Wave', 'NDW', 'Folk', 'Neofolk',
        'Jazz', 'Blues', 'Ska', 'Garage', 'Hyperpop', 'Emo', 'Metalcore', 'Synth',
        'Ska', 'Beatdown', 'Doom', 'Sludge', 'Stoner', 'Crustpunk'
    ]
    return [(genre, genre) for genre in genres]


def get_canton_choices():
    """
    Get list of Swiss cantons for SelectField
    :return: a list of tuples with canton abbreviations and names
    """
    return [
        ('AG', 'Aargau'),
        ('AI', 'Appenzell Innerrhoden'),
        ('AR', 'Appenzell Ausserrhoden'),
        ('BE', 'Bern'),
        ('BL', 'Basel Land'),
        ('BS', 'Basel Stadt'),
        ('FR', 'Fribourg'),
        ('GE', 'Genève'),
        ('GL', 'Glarus'),
        ('GR', 'Graubünden'),
        ('JU', 'Jura'),
        ('LU', 'Luzern'),
        ('NE', 'Neuchâtel'),
        ('NW', 'Nidwalden'),
        ('OW', 'Obwalden'),
        ('SG', 'Sankt Gallen'),
        ('SH', 'Schaffhausen'),
        ('SO', 'Solothurn'),
        ('SZ', 'Schwyz'),
        ('TG', 'Thurgau'),
        ('TI', 'Ticino'),
        ('UR', 'Uri'),
        ('VD', 'Vaud'),
        ('VS', 'Wallis'),
        ('ZG', 'Zug'),
        ('ZH', 'Zürich')
    ]



class EventForm(FlaskForm):
    # Existing event fields
    name = StringField('Event Name', validators=[Optional()])
    date = DateTimeField('Event Date', format='%Y-%m-%d', validators=[DataRequired()])
    end_date = DateField('End Date', validators=[Optional()])
    is_festival = BooleanField('Festival')
    doors = TimeField('Doors Open Time', format='%H:%M', validators=[DataRequired()])
    genre = SelectMultipleField('Genre', choices=[], validate_choice=False, validators=[Optional()])
    acts = TextAreaField('Acts', validators=[Optional()])
    flyer = FileField('Flyer', validators=[Optional()])

    # Ticket Details
    ticket_price = StringField('Ticket Price', validators=[DataRequired()])
    ticket_link = StringField('Ticket Link', validators=[Optional()])

    # Venue Selection Field
    venue_selection = StringField('Venue', validators=[Optional()])
    venue_id = HiddenField('Venue ID')  # To store the selected venue's ID

    # Venue Details Fields (for new venues)
    venue_name = StringField('Venue Name', validators=[Optional()])
    venue_address = StringField('Venue Address', validators=[Optional()])
    venue_city = StringField('City', validators=[Optional()])
    venue_canton = StringField('Canton', validators=[Optional()])
    venue_plz = StringField('ZIP Code', validators=[Optional()])
    venue_coords = StringField('Coordinates', validators=[Optional()])

class EventEditForm(FlaskForm):
    # Existing event fields
    name = StringField('Event Name', validators=[Optional()])
    date = DateTimeField('Event Date', format='%Y-%m-%d', validators=[DataRequired()])
    end_date = DateField('End Date', validators=[Optional()])
    is_festival = BooleanField('Festival')
    doors = TimeField('Doors Open Time', format='%H:%M', validators=[DataRequired()])
    genre = SelectMultipleField('Genre', choices=[], validate_choice=False, validators=[Optional()])
    acts = TextAreaField('Acts', validators=[Optional()])
    flyer = FileField('Flyer', validators=[Optional()])

    # Ticket Details
    ticket_price = StringField('Ticket Price', validators=[DataRequired()])
    ticket_link = StringField('Ticket Link', validators=[Optional()])

    # Venue Selection Field
    venue_selection = StringField('Venue', validators=[Optional()])
    venue_id = HiddenField('Venue ID')  # To store the selected venue's ID

    # Venue Details Fields (for new venues)
    venue_name = StringField('Venue Name', validators=[Optional()])
    venue_address = StringField('Venue Address', validators=[Optional()])
    venue_city = StringField('City', validators=[Optional()])
    venue_canton = StringField('Canton', validators=[Optional()])
    venue_plz = StringField('ZIP Code', validators=[Optional()])
    venue_coords = StringField('Coordinates', validators=[Optional()])


class LoginForm(FlaskForm):
    email = StringField('Email', validators=[DataRequired(), Email()])
    password = PasswordField('Password', validators=[DataRequired()])


class DeleteEventForm(FlaskForm):
    pass


class SetPasswordForm(FlaskForm):
    password = PasswordField('Password', validators=[DataRequired(), Length(min=8)])
    confirm_password = PasswordField('Confirm Password', validators=[DataRequired(), EqualTo('password')])


class CollaboratorRequestForm(FlaskForm):
    name = StringField('Name', validators=[DataRequired(), Length(max=100)])
    email = StringField('E-Mail', validators=[DataRequired(), Email(), Length(max=200)])
    message = TextAreaField('Nachricht', validators=[DataRequired(), Length(min=10, max=2000)])
    # Honeypot — hidden via CSS, real users never fill it.
    website = StringField('Website', validators=[Optional(), Length(max=0)])

