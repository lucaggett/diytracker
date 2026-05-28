from flask_wtf import FlaskForm
from wtforms import (StringField, TextAreaField, DateField, TimeField,
                     FileField, SubmitField, SelectField, SelectMultipleField,
                     PasswordField, IntegerField)
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
        'Beatdown', 'Doom', 'Sludge', 'Stoner', 'Crustpunk',
        'Screamo', 'Powerviolence', 'Mathcore', 'Shoegaze', 'Gabber'
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

EventEditForm = EventForm


class LoginForm(FlaskForm):
    email = StringField(_l('Email'), validators=[DataRequired(), Email()])
    password = PasswordField(_l('Password'), validators=[DataRequired()])


class DeleteEventForm(FlaskForm):
    pass


class DeleteScrapedEventForm(FlaskForm):
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


_YPN = [('', '—'), ('yes', 'Yes'), ('partial', 'Partial'), ('no', 'No')]
_YN  = [('', '—'), ('yes', 'Yes'), ('no', 'No')]
_YNU = [('', '—'), ('yes', 'Yes'), ('no', 'No'), ('unknown', 'Unknown')]
_NSR = [('', '—'), ('never', 'Never'), ('sometimes', 'Sometimes'), ('regularly', 'Regularly')]
_ASN = [('', '—'), ('always', 'Always'), ('sometimes', 'Sometimes'), ('never', 'Never')]
_SIGN = [('', '—'), ('sometimes', 'Sometimes'), ('rarely', 'Rarely'), ('never', 'Never')]
_SOUND   = [('', '—'), ('very_loud', 'Very loud (120 dB+)'), ('loud', 'Loud'), ('moderate', 'Moderate'), ('varies', 'Varies')]
_SURFACE = [('', '—'), ('flat', 'Flat / smooth'), ('slight_slope', 'Slight slope'), ('uneven', 'Uneven'), ('cobblestones', 'Cobblestones / gravel')]
_PARKING = [('', '—'), ('yes', 'Yes, on-site'), ('nearby', 'Nearby'), ('no', 'No')]
_FRIDGE  = [('', '—'), ('yes', 'Yes'), ('no', 'No'), ('ask_staff', 'Ask staff')]
_YSN     = [('', '—'), ('yes', 'Yes'), ('sometimes', 'Sometimes'), ('no', 'No')]


class AccessibilityForm(FlaskForm):
    # Mobility & Wheelchair
    step_free_entrance       = SelectField(_l('Step-free entrance'), choices=_YPN, validators=[Optional()])
    step_free_entrance_notes = TextAreaField(_l('Entrance notes'), validators=[Optional(), Length(max=500)])
    step_free_interior       = SelectField(_l('Step-free throughout interior'), choices=_YPN, validators=[Optional()])
    accessible_toilet        = SelectField(_l('Accessible toilet'), choices=_YPN, validators=[Optional()])
    accessible_toilet_notes  = TextAreaField(_l('Toilet notes'), validators=[Optional(), Length(max=500)])
    wheelchair_spaces        = SelectField(_l('Dedicated wheelchair spaces'), choices=_YN, validators=[Optional()])
    wheelchair_spaces_count  = IntegerField(_l('Number of wheelchair spaces'), validators=[Optional()])
    floor_surface            = SelectField(_l('Floor surface'), choices=_SURFACE, validators=[Optional()])

    # Sensory, Epilepsy & Autism
    strobe_lights            = SelectField(_l('Strobe / flashing lights'), choices=_NSR, validators=[Optional()])
    strobe_warning           = SelectField(_l('Warning given before strobes'), choices=_ASN, validators=[Optional()])
    smoke_machines           = SelectField(_l('Smoke / haze machines'), choices=_NSR, validators=[Optional()])
    sound_level              = SelectField(_l('Typical sound level'), choices=_SOUND, validators=[Optional()])
    quiet_space              = SelectField(_l('Quiet / low-stimulation room available'), choices=_YN, validators=[Optional()])
    earplugs_available       = SelectField(_l('Free earplugs provided'), choices=_YN, validators=[Optional()])
    sensory_friendly_events  = SelectField(_l('Sensory-friendly events / nights'), choices=_YSN, validators=[Optional()])

    # Hearing
    hearing_loop             = SelectField(_l('Hearing loop (induction loop)'), choices=_YNU, validators=[Optional()])
    sign_language            = SelectField(_l('Sign language interpretation at events'), choices=_SIGN, validators=[Optional()])

    # Medical
    medication_fridge        = SelectField(_l('Refrigerator for medication'), choices=_FRIDGE, validators=[Optional()])
    first_aid_kit            = SelectField(_l('First aid kit on site'), choices=_YN, validators=[Optional()])
    aed_on_site              = SelectField(_l('AED (defibrillator) on site'), choices=_YNU, validators=[Optional()])

    # General / Social
    accessible_parking       = SelectField(_l('Accessible parking'), choices=_PARKING, validators=[Optional()])
    public_transport_notes   = TextAreaField(_l('Public transport access'), validators=[Optional(), Length(max=500)])
    gender_neutral_toilets   = SelectField(_l('Gender-neutral toilets'), choices=_YN, validators=[Optional()])
    seating_areas            = SelectField(_l('Rest / seating areas inside'), choices=_YN, validators=[Optional()])
    guide_dogs_welcome       = SelectField(_l('Guide dogs & assistance animals welcome'), choices=_YN, validators=[Optional()])
    quiet_entrance           = SelectField(_l('Quiet / alternative entrance option'), choices=_YN, validators=[Optional()])
    additional_notes         = TextAreaField(_l('Additional accessibility notes'), validators=[Optional(), Length(max=2000)])


class CollaboratorRequestForm(FlaskForm):
    name = StringField(_l('Name'), validators=[DataRequired(), Length(max=100)])
    email = StringField(_l('Email'), validators=[DataRequired(), Email(), Length(max=200)])
    message = TextAreaField(_l('Message'), validators=[DataRequired(), Length(min=10, max=2000)])
    # Honeypot — hidden via CSS, real users never fill it.
    website = StringField('Website', validators=[Optional(), Length(max=0)])
