from flask_wtf import FlaskForm
from wtforms import (StringField, TextAreaField, DateField, TimeField,
                     FileField, SubmitField, SelectField, SelectMultipleField,
                     PasswordField)
from wtforms.fields.datetime import DateTimeField
from wtforms.fields.simple import HiddenField, BooleanField
from wtforms.validators import DataRequired, Optional, Email, EqualTo, Length

try:
    from flask_babel import lazy_gettext as _l  # type: ignore
except ImportError:  # pragma: no cover - defensive fallback
    def _l(s):
        return s


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
    name = StringField(_l('Event Name'), validators=[Optional()])
    date = DateTimeField(_l('Event Date'), format='%Y-%m-%d', validators=[DataRequired()])
    end_date = DateField(_l('End Date'), validators=[Optional()])
    is_festival = BooleanField(_l('Festival'))
    doors = TimeField(_l('Doors Open Time'), format='%H:%M', validators=[DataRequired()])
    genre = SelectMultipleField(_l('Genre'), choices=[], validate_choice=False, validators=[Optional()])
    acts = TextAreaField(_l('Acts'), validators=[Optional()])
    flyer = FileField(_l('Flyer'), validators=[Optional()])

    # Ticket Details
    ticket_price = StringField(_l('Ticket Price'), validators=[DataRequired()])
    ticket_link = StringField(_l('Ticket Link'), validators=[Optional()])

    # Venue Selection Field
    venue_selection = StringField(_l('Venue'), validators=[Optional()])
    venue_id = HiddenField('Venue ID')  # To store the selected venue's ID

    # Venue Details Fields (for new venues)
    venue_name = StringField(_l('Venue Name'), validators=[Optional()])
    venue_address = StringField(_l('Venue Address'), validators=[Optional()])
    venue_city = StringField(_l('City'), validators=[Optional()])
    venue_canton = StringField(_l('Canton'), validators=[Optional()])
    venue_plz = StringField(_l('ZIP Code'), validators=[Optional()])
    venue_coords = StringField(_l('Coordinates'), validators=[Optional()])

class EventEditForm(FlaskForm):
    # Existing event fields
    name = StringField(_l('Event Name'), validators=[Optional()])
    date = DateTimeField(_l('Event Date'), format='%Y-%m-%d', validators=[DataRequired()])
    end_date = DateField(_l('End Date'), validators=[Optional()])
    is_festival = BooleanField(_l('Festival'))
    doors = TimeField(_l('Doors Open Time'), format='%H:%M', validators=[DataRequired()])
    genre = SelectMultipleField(_l('Genre'), choices=[], validate_choice=False, validators=[Optional()])
    acts = TextAreaField(_l('Acts'), validators=[Optional()])
    flyer = FileField(_l('Flyer'), validators=[Optional()])

    # Ticket Details
    ticket_price = StringField(_l('Ticket Price'), validators=[DataRequired()])
    ticket_link = StringField(_l('Ticket Link'), validators=[Optional()])

    # Venue Selection Field
    venue_selection = StringField(_l('Venue'), validators=[Optional()])
    venue_id = HiddenField('Venue ID')  # To store the selected venue's ID

    # Venue Details Fields (for new venues)
    venue_name = StringField(_l('Venue Name'), validators=[Optional()])
    venue_address = StringField(_l('Venue Address'), validators=[Optional()])
    venue_city = StringField(_l('City'), validators=[Optional()])
    venue_canton = StringField(_l('Canton'), validators=[Optional()])
    venue_plz = StringField(_l('ZIP Code'), validators=[Optional()])
    venue_coords = StringField(_l('Coordinates'), validators=[Optional()])


class LoginForm(FlaskForm):
    email = StringField(_l('Email'), validators=[DataRequired(), Email()])
    password = PasswordField(_l('Password'), validators=[DataRequired()])


class DeleteEventForm(FlaskForm):
    pass


class SetPasswordForm(FlaskForm):
    password = PasswordField(_l('Password'), validators=[DataRequired(), Length(min=8)])
    confirm_password = PasswordField(_l('Confirm Password'), validators=[DataRequired(), EqualTo('password')])


class VenueForm(FlaskForm):
    name = StringField(_l('Venue Name'), validators=[DataRequired(), Length(max=200)])
    address = StringField(_l('Venue Address'), validators=[Optional(), Length(max=200)])
    city = StringField(_l('City'), validators=[DataRequired(), Length(max=100)])
    canton = SelectField(_l('Canton'), choices=[], validators=[Optional()])
    plz = StringField(_l('ZIP Code'), validators=[DataRequired(), Length(max=10)])
    coords = StringField(_l('Coordinates'), validators=[Optional(), Length(max=50)])


class DeleteVenueForm(FlaskForm):
    pass


class CollaboratorRequestForm(FlaskForm):
    name = StringField(_l('Name'), validators=[DataRequired(), Length(max=100)])
    email = StringField(_l('Email'), validators=[DataRequired(), Email(), Length(max=200)])
    message = TextAreaField(_l('Message'), validators=[DataRequired(), Length(min=10, max=2000)])
    # Honeypot — hidden via CSS, real users never fill it.
    website = StringField('Website', validators=[Optional(), Length(max=0)])
