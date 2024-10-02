from flask_wtf import FlaskForm
from wtforms import (StringField, TextAreaField, DateField, TimeField,
                     FileField, SubmitField, SelectField, SelectMultipleField)
from wtforms.fields.datetime import DateTimeField
from wtforms.fields.simple import HiddenField, BooleanField
from wtforms.validators import DataRequired, Optional


def get_genre_choices():
    genres = [
        'Hardcore', 'Punk', 'Metal', 'Post-punk', 'EBM', 'Industrial',
        'Synthpop', 'Darkwave', 'Goth', 'New Wave', 'Alternative',
        'Indie', 'Rock', 'Pop', 'Hip Hop', 'Reggae', 'Dub', 'Dancehall',
        'Drum & Bass', 'Dubstep', 'Techno', 'House', 'Trance', 'Electro',
        'Ambient', 'Experimental', 'Noise', 'Wave', 'NDW', 'Folk', 'Neofolk',
        'Jazz', 'Blues', 'Ska', 'Garage', 'Hyperpop', 'Emo', 'Metalcore', 'Synth',
        'Ska'
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
    doors = TimeField('Doors Open Time', format='%H:%M', validators=[DataRequired()])
    genre = SelectMultipleField('Genre', choices=get_genre_choices(), validators=[Optional()])
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

    # Password
    password = StringField('Submission Password', validators=[DataRequired()])

