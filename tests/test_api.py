"""JSON API endpoints."""


class TestGetGenres:
    def test_combines_seed_and_db_genres(self, client, make_event):
        # An event introduces a genre token; the seed list provides the rest.
        make_event(genre='SomeUniqueGenre')
        data = client.get('/get_genres').get_json()
        genres = data['genres']
        assert 'SomeUniqueGenre' in genres   # from DB
        assert 'Punk' in genres              # from seed
        # Sorted case-insensitively and de-duplicated.
        assert genres == sorted(set(genres), key=str.lower)

    def test_empty_db_returns_seed_only(self, client):
        genres = client.get('/get_genres').get_json()['genres']
        assert 'Metal' in genres


class TestGetVenues:
    def test_returns_venues_sorted_by_name(self, client, make_venue):
        make_venue(name='Zeta')
        make_venue(name='Alpha', plz='9999')
        venues = client.get('/get_venues').get_json()['venues']
        names = [v['name'] for v in venues]
        assert names == ['Alpha', 'Zeta']

    def test_venue_payload_shape(self, client, make_venue):
        make_venue(name='Hall', city='Bern', plz='3000', canton='BE')
        venue = client.get('/get_venues').get_json()['venues'][0]
        assert set(venue) == {'id', 'name', 'address', 'city', 'plz', 'canton', 'coords'}
        assert venue['coords'] == 'N/A'  # empty coords surfaced as N/A
