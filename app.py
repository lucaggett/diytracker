import calendar
import os

from flask import Flask, render_template, redirect, url_for, flash
from werkzeug.utils import secure_filename

from forms import EventForm
from models import db, Event

app = Flask(__name__)

app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///events.db'  # SQLite for simplicity
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = 'your_secret_key'

db.init_app(app)

with app.app_context():
    db.create_all()

app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['ALLOWED_EXTENSIONS'] = {'png', 'jpg', 'jpeg', 'gif'}

# Check allowed file extensions
@app.route('/submit', methods=['GET', 'POST'])
def submit_event():
    form = EventForm()
    if form.validate_on_submit():
        # Get form data
        name = form.name.data
        date = form.date.data
        venue = form.venue.data
        doors = form.doors.data
        genre = form.genre.data
        acts = form.acts.data

        # Handle flyer upload
        flyer = None
        if form.flyer.data:
            file = form.flyer.data
            if file and allowed_file(file.filename):
                filename = secure_filename(file.filename)
                flyer = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                file.save(flyer)

        # Create and save new event
        new_event = Event(name=name, date=date, venue=venue, doors=doors, genre=genre, acts=acts, flyer=flyer)
        db.session.add(new_event)
        db.session.commit()

        flash('Event submitted successfully!')
        return redirect(url_for('calendar_view'))

    return render_template('submit_event.html', form=form)

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']


from collections import defaultdict
from datetime import datetime

@app.route('/')
@app.route('/<int:year>/<int:month>')
def calendar_view(year=None, month=None):
    now = datetime.now()
    year = year if year else now.year
    month = month if month else now.month

    first_day_of_month = datetime(year, month, 1)
    last_day_of_month = datetime(year, month, calendar.monthrange(year, month)[1])

    # Query events for the selected month
    events = Event.query.order_by(Event.date.asc()).all()
    print(events)
    # Group events by their exact date (as datetime objects)
    grouped_events = defaultdict(list)
    for event in events:
        event_date = event.date.strftime('%Y-%m-%d')
        grouped_events[datetime.strptime(event_date, '%Y-%m-%d')].append(event)

    return render_template('calendar.html', grouped_events=grouped_events, current_year=year, current_month_number=month)
