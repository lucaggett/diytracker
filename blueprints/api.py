from flask import Blueprint, jsonify

from forms import get_genre_choices
from models import Event, Venue
from services.cache import cache

bp = Blueprint('api', __name__)


@bp.route('/get_genres')
@cache.cached()
def get_genres():
    seed = [g for g, _ in get_genre_choices()]
    db_genres = []
    for event in Event.query.with_entities(Event.genre).filter(Event.genre != None).all():
        for g in (event.genre or '').split(','):
            g = g.strip()
            if g:
                db_genres.append(g)
    combined = sorted(set(seed + db_genres), key=str.lower)
    return jsonify({'genres': combined})


@bp.route('/get_venues')
@cache.cached()
def get_venues():
    venues = Venue.query.order_by(Venue.name.asc()).all()
    venue_list = []
    for venue in venues:
        venue_list.append({
            'id': venue.id,
            'name': venue.name,
            'address': venue.address,
            'city': venue.city,
            'plz': venue.plz,
            'canton': venue.canton,
            'coords': venue.coords if venue.coords else 'N/A',
        })
    return jsonify({'venues': venue_list})
