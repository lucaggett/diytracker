from flask_wtf import FlaskForm
from wtforms import (StringField, TextAreaField, DateField, TimeField,
                     FileField, SubmitField, SelectField, SelectMultipleField)
from wtforms.validators import DataRequired, Optional


def get_genre_choices():
    return [
        ('rock', 'Rock'),
        ('pop', 'Pop'),
        ('hip-hop', 'Hip-Hop'),
        ('jazz', 'Jazz'),
        ('blues', 'Blues'),
        ('country', 'Country'),
        ('metal', 'Metal'),
        ('electronic', 'Electronic'),
        ('classical', 'Classical'),
        ('folk', 'Folk'),
        ('reggae', 'Reggae'),
        ('latin', 'Latin'),
        ('other', 'Other')
    ]


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
    name = StringField('Event Name (Optional if Acts are provided)', validators=[Optional()])
    date = DateField('Event Date', format='%Y-%m-%d', validators=[DataRequired()])
    venue_name = StringField('Venue', validators=[DataRequired()])
    venue_address = StringField('Address (Optional)', validators=[Optional()])
    venue_city = StringField('City', validators=[DataRequired()])
    venue_canton = SelectField('Canton', choices=get_canton_choices(), validators=[DataRequired()])
    venue_plz = StringField('ZIP Code', validators=[DataRequired()])
    venue_coords = StringField('Coordinates (Optional)', validators=[Optional()])
    ticket_price = StringField('Ticket Price', validators=[DataRequired()])
    ticket_link = StringField('Ticket Link (Optional)', validators=[Optional()])
    doors = TimeField('Doors Open (24-hour time)', format='%H:%M', validators=[DataRequired()])
    genre = SelectMultipleField('Genre', choices=get_genre_choices(), validators=[DataRequired()])
    acts = TextAreaField('Acts (Optional if Event Name is provided)', validators=[Optional()])
    flyer = FileField('Flyer (Optional, JPG/PNG)', validators=[Optional()])
    password = StringField('Password', validators=[DataRequired()])
    submit = SubmitField('Submit Event')

    def validate(self, *args, **kwargs):
        if not super().validate():
            return False
        if not self.name.data and not self.acts.data:
            print('Name or acts required')
            return False
        return True
