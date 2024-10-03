import calendar
import os
from collections import defaultdict
from datetime import datetime
from dateutil.relativedelta import relativedelta

from flask import Flask, render_template, redirect, url_for, flash, request, jsonify
from werkzeug.utils import secure_filename

from forms import EventForm
from models import db, Event, Venue

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

# Check allowed file extensions
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']

@app.route('/submit', methods=['GET', 'POST'])
def submit_event():
    form = EventForm()
    if form.validate_on_submit():
        # Extract form data
        name = form.name.data
        date = form.date.data
        doors = form.doors.data
        genres = form.genre.data
        acts = form.acts.data
        ticket_link = form.ticket_link.data
        ticket_price = form.ticket_price.data
        password = form.password.data

        # Get the venue_id from the form
        venue_id = form.venue_id.data

        # Password validation
        if password != open("SUBMISSION_PASSWORD_CURRENT").read().strip():
            flash('Invalid password')
            return redirect(url_for('submit_event'))

        # Handle flyer upload (unchanged)
        flyer = None
        if form.flyer.data:
            file = form.flyer.data
            if file and allowed_file(file.filename):
                filename = secure_filename(file.filename)
                flyer_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                file.save(flyer_path)
                flyer = flyer_path

        # Handle venue selection
        if venue_id == 'new' or not venue_id:
            # Extract venue data from form fields
            venue_name = form.venue_name.data
            venue_address = form.venue_address.data
            venue_city = form.venue_city.data
            venue_canton = form.venue_canton.data
            venue_plz = form.venue_plz.data
            venue_coords = form.venue_coords.data

            # Validate that essential venue fields are provided
            if not venue_name or not venue_city or not venue_plz:
                flash('Please provide all required venue details for a new venue.')
                return redirect(url_for('submit_event'))

            # Check if the venue already exists
            venue = Venue.query.filter_by(
                name=venue_name,
                city=venue_city,
                plz=venue_plz
            ).first()

            if not venue:
                # Create a new venue
                venue = Venue(
                    name=venue_name,
                    address=venue_address,
                    city=venue_city,
                    canton=venue_canton,
                    plz=venue_plz,
                    coords=venue_coords
                )
                db.session.add(venue)
                db.session.commit()  # Commit to assign an ID to the venue
            else:
                # Venue already exists; you may want to inform the user
                flash('Venue already exists. Using existing venue.')
        else:
            # Use existing venue by ID
            venue = Venue.query.get(venue_id)
            if not venue:
                flash('Selected venue does not exist.')
                return redirect(url_for('submit_event'))

        # Create and save the new event
        new_event = Event(
            name=name,
            date=date,
            doors=doors,
            genre=', '.join(genres) if genres else '',
            acts=acts,
            flyer=flyer,
            ticket_link=ticket_link,
            ticket_price=ticket_price,
            venue_id=venue.id,  # Associate the event with the venue
            event_hash=hash(f"{name}{date}{doors}{genres}{acts}{ticket_link}{ticket_price}{venue.id}")
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
            if password != open('ADMIN_PASSWORD').read().strip():
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
    if password != open('ADMIN_PASSWORD').read().strip():
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

@app.route('/get_venues')
def get_venues():
    venues = Venue.query.all()
    venue_list = []
    for venue in venues:
        venue_list.append({
            'id': venue.id,
            'name': venue.name,
            'address': venue.address,
            'city': venue.city,
            'plz': venue.plz,
            'canton': venue.canton,
            'coords': venue.coords if venue.coords else 'N/A'
        })
    return jsonify({'venues': venue_list})
