from models import db, Venue


def get_or_create_venue(name, address, city, canton, plz, coords=''):
    """Find venue by (name, city, plz) or create it. Returns (venue, created).
    Does not commit — caller is responsible for the transaction."""
    venue = Venue.query.filter_by(name=name, city=city, plz=plz).first()
    if venue:
        return venue, False
    venue = Venue(
        name=name,
        address=address,
        city=city,
        canton=canton,
        plz=plz,
        coords=coords or '',
    )
    db.session.add(venue)
    db.session.flush()
    return venue, True
