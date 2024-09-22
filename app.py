import calendar
import os
from collections import defaultdict
from datetime import datetime
from dateutil.relativedelta import relativedelta

from flask import Flask, render_template, redirect, url_for, flash, request
from werkzeug.utils import secure_filename

from forms import EventForm
from models import db, Event

PASSWORD = os.getenv('SUBMISSION_PASSWORD', 'diytrackerischziemlicool')

# Check that required directories exist
if not os.path.exists('logs'):
    os.makedirs('logs')

if not os.path.exists('static/uploads'):
    os.makedirs('static/uploads')

app = Flask(__name__)

app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///events.db'  # SQLite for simplicity
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = 'your_secret_key'

db.init_app(app)

with app.app_context():
    db.create_all()

app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['ALLOWED_EXTENSIONS'] = {'png', 'jpg', 'jpeg', 'gif'}
print("http://127.0.0.1:5000/submit")

# Check allowed file extensions
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']

@app.route('/submit', methods=['GET', 'POST'])
def submit_event():
    form = EventForm()
    if form.validate_on_submit():
        # Get form data
        name = form.name.data
        date = form.date.data
        venue_name = form.venue_name.data
        venue_address = form.venue_address.data
        venue_city = form.venue_city.data
        venue_canton = form.venue_canton.data
        venue_plz = form.venue_plz.data
        venue_coords = form.venue_coords.data
        doors = form.doors.data
        genres = form.genre.data
        acts = form.acts.data
        ticket_link = form.ticket_link.data
        ticket_price = form.ticket_price.data
        password = form.password.data

        if password != PASSWORD:
            flash('Invalid password')
            return redirect(url_for('submit_event'))

        # Handle flyer upload
        flyer = None
        if form.flyer.data:
            file = form.flyer.data
            if file and allowed_file(file.filename):
                filename = secure_filename(file.filename)
                flyer = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                file.save(flyer)

        # Create and save new event
        new_event = Event(
            name=name,
            date=date,
            venue_name=venue_name,
            venue_address=venue_address,
            venue_city=venue_city,
            venue_canton=venue_canton,
            venue_plz=venue_plz,
            venue_coords=venue_coords,
            doors=doors,
            genre=', '.join(genres),
            acts=acts,
            flyer=flyer,
            ticket_link=ticket_link,
            ticket_price=ticket_price
        )
        db.session.add(new_event)
        db.session.commit()

        flash('Event submitted successfully!')
        return redirect(url_for('calendar_view'))

    return render_template('submit_event.html', form=form)

@app.route('/admin', methods=['GET'])
def admin():
    events = Event.query.order_by(Event.date.asc()).all()
    return render_template('admin.html', events=events)

@app.route('/edit_event/<int:event_id>', methods=['GET', 'POST'])
def edit_event(event_id):
    event = Event.query.get_or_404(event_id)
    form = EventForm(obj=event)
    if request.method == 'POST':
        if form.validate_on_submit():
            password = form.password.data
            if password != PASSWORD:
                flash('Invalid password')
                return redirect(url_for('edit_event', event_id=event_id))

            # Update event details
            form.populate_obj(event)

            # Handle flyer upload
            if form.flyer.data:
                file = form.flyer.data
                if file and allowed_file(file.filename):
                    filename = secure_filename(file.filename)
                    flyer = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                    file.save(flyer)
                    event.flyer = flyer

            db.session.commit()
            flash('Event updated successfully!')
            return redirect(url_for('admin'))

    return render_template('edit_event.html', form=form, event=event)

@app.route('/delete_event/<int:event_id>', methods=['POST'])
def delete_event(event_id):
    event = Event.query.get_or_404(event_id)
    password = request.form.get('password')
    if password != PASSWORD:
        flash('Invalid password')
        return redirect(url_for('admin'))

    db.session.delete(event)
    db.session.commit()
    flash('Event deleted successfully!')
    return redirect(url_for('admin'))

@app.route('/')
def calendar_view():
    now = datetime.now()

    # Calculate the current month and the next two months
    months = []
    for i in range(3):
        month_date = now + relativedelta(months=i)
        months.append((month_date.year, month_date.month))

    # Define the start and end dates for querying events
    start_date = datetime(months[0][0], months[0][1], 1)
    end_month = months[-1]
    last_day = calendar.monthrange(end_month[0], end_month[1])[1]
    end_date = datetime(end_month[0], end_month[1], last_day, 23, 59, 59)

    # Query events within the date range
    events = Event.query.filter(Event.date >= start_date, Event.date <= end_date).order_by(Event.date.asc()).all()

    # Group events by their date
    grouped_events = defaultdict(list)
    for event in events:
        event_date = event.date.date()
        grouped_events[event_date].append(event)

    # Prepare month data for the template
    months_data = []
    for year, month in months:
        last_day_of_month = calendar.monthrange(year, month)[1]
        months_data.append({
            'year': year,
            'month': month,
            'last_day_of_month': last_day_of_month
        })

    return render_template('calendar.html', grouped_events=grouped_events, months_data=months_data, datetime=datetime)
