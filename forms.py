from flask_wtf import FlaskForm
from wtforms import StringField, TextAreaField, DateField, TimeField, FileField, SubmitField
from wtforms.validators import DataRequired, Optional
from wtforms import ValidationError

class EventForm(FlaskForm):
    name = StringField('Event Name (Optional if Acts are provided)', validators=[Optional()])
    date = DateField('Event Date', format='%Y-%m-%d', validators=[DataRequired()])
    venue = StringField('Venue/Address', validators=[DataRequired()])
    doors = TimeField('Doors Open (24-hour time)', format='%H:%M', validators=[DataRequired()])
    genre = StringField('Genre (Optional)', validators=[Optional()])
    acts = TextAreaField('Acts (Optional if Event Name is provided)', validators=[Optional()])
    flyer = FileField('Flyer (Optional, JPG/PNG)', validators=[Optional()])
    submit = SubmitField('Submit Event')

    def validate(self, *args, **kwargs):
        if not super().validate():
            return False
        if not self.name.data and not self.acts.data:
            self.name.errors.append("Please provide either an event name or a list of acts.")
            return False
        return True
