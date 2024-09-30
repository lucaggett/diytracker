from flask_wtf import FlaskForm
from wtforms import (StringField, TextAreaField, DateField, TimeField,
                     FileField, SubmitField, SelectField, SelectMultipleField)
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
    name = StringField('Event Name', validators=[Optional()])
    date = DateField('Event Date', format='%Y-%m-%d', validators=[DataRequired()])
    venue_name = StringField('Venue', validators=[DataRequired()])
    venue_address = StringField('Address', validators=[Optional()])
    venue_city = StringField('City', validators=[DataRequired()])
    venue_canton = SelectField('Canton', choices=get_canton_choices(), validators=[DataRequired()])
    venue_plz = StringField('ZIP Code', validators=[DataRequired()])
    venue_coords = StringField('Coordinates', validators=[Optional()])
    ticket_price = StringField('Ticket Price', validators=[DataRequired()])
    ticket_link = StringField('Ticket Link', validators=[Optional()])
    doors = TimeField('Doors Open (24-hour time)', format='%H:%M', validators=[DataRequired()])
    genre = SelectMultipleField('Genre', choices=get_genre_choices(), validators=[DataRequired()])
    acts = TextAreaField('Acts', validators=[Optional()])
    flyer = FileField('Flyer', validators=[Optional()])
    password = StringField('Password', validators=[DataRequired()])
    submit = SubmitField('Submit Event')

    def validate(self, *args, **kwargs):
        if not super().validate():
            return False
        if not self.name.data and not self.acts.data:
            print('Name or acts required')
            return False
        if not self.password.data == open("SUBMISSION_PASSWORD_CURRENT").read().strip():
            print('Invalid password')
            return False
        return True
