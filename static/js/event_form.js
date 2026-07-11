// Shared behaviour for the event form (templates/_partials/event_form.html),
// used by both the submit and the admin edit page. The page sets
// window.EVENT_FORM_CONFIG before including this script:
//   preselectedVenueId: venue id to preselect (edit page), or null
//   preselectedGenres:  genre tokens already on the event (edit page)
//   messages:           translated strings (see the partial)
// Field ids are the WTForms defaults from EventForm (id == field name).
document.addEventListener('DOMContentLoaded', function () {
    const cfg = window.EVENT_FORM_CONFIG || {};
    const msg = cfg.messages || {};
    const preselectedGenres = cfg.preselectedGenres || [];

    // Choices.js for Genre with DB-backed autocomplete + free-text
    const genreSelect = document.getElementById('genre');
    let genreChoices;
    fetch('/get_genres')
        .then(r => r.json())
        .then(data => {
            genreChoices = new Choices(genreSelect, {
                removeItemButton: true,
                shouldSort: false,
                searchResultLimit: 20,
                allowHTML: false,
                duplicateItemsAllowed: false,
                addItems: true,
                addItemText: (value) => `${msg.pressEnterToAdd} "<b>${value}</b>"`,
                placeholderValue: msg.genrePlaceholder,
            });
            genreChoices.setChoices(
                data.genres.map(g => ({
                    value: g,
                    label: g,
                    selected: preselectedGenres.includes(g)
                })),
                'value', 'label', true
            );
            preselectedGenres.forEach(g => {
                if (!data.genres.includes(g)) {
                    genreChoices.setChoices([{ value: g, label: g, selected: true }], 'value', 'label', false);
                }
            });
        });

    genreSelect.addEventListener('search', function (e) {
        const query = e.detail.value.trim();
        if (query && genreChoices) {
            const existing = genreChoices._currentState.choices.find(
                c => c.value.toLowerCase() === query.toLowerCase()
            );
            if (!existing) {
                genreChoices.setChoices([{ value: query, label: query }], 'value', 'label', false);
            }
        }
    });

    // Flatpickr for the date, end-date and doors-time fields
    flatpickr('#date', {
        altInput: true,
        altFormat: 'F j, Y',
        dateFormat: 'Y-m-d'
    });
    flatpickr('#end_date', {
        altInput: true,
        altFormat: 'F j, Y',
        dateFormat: 'Y-m-d'
    });
    flatpickr('#doors', {
        enableTime: true,
        noCalendar: true,
        dateFormat: 'H:i',
        time_24hr: true
    });

    // Festival toggle: update end-date label
    const festivalCheckbox = document.getElementById('is_festival');
    const endDateLabel = document.getElementById('end-date-label');
    festivalCheckbox.addEventListener('change', function () {
        if (festivalCheckbox.checked) {
            endDateLabel.innerHTML = msg.endDate + ' <span class="normal-case text-bleed">' + msg.requiredForFestivals + '</span>';
        } else {
            endDateLabel.innerHTML = msg.endDate + ' <span class="normal-case text-smudge">' + msg.optional + '</span>';
        }
    });

    // Flyer preview
    const flyerInput = document.getElementById('flyer');
    const flyerPreview = document.getElementById('flyer-preview');
    flyerInput.addEventListener('change', function (event) {
        flyerPreview.innerHTML = '';
        const file = event.target.files[0];
        if (file && (file.type === 'image/jpeg' || file.type === 'image/png')) {
            const reader = new FileReader();
            reader.onload = function (e) {
                const img = document.createElement('img');
                img.src = e.target.result;
                img.alt = msg.flyerPreviewAlt;
                img.classList.add('max-w-xs', 'mt-2', 'border-2', 'border-ink');
                flyerPreview.appendChild(img);
            };
            reader.readAsDataURL(file);
        } else {
            flyerPreview.innerHTML = '<p class="text-bleed text-xs font-mono">' + msg.uploadJpgPng + '</p>';
        }
    });

    // Venue dropdown: the nameless <select> drives the hidden venue_id input
    // (rendered by form.hidden_tag()), which is what the route reads.
    let venues = [];
    const venueIdInput = document.getElementById('venue_id');
    const venueSelect = document.getElementById('venue-select');
    const venueFieldIds = ['venue_name', 'venue_address', 'venue_city', 'venue_plz', 'venue_canton', 'venue_coords'];

    fetch('/get_venues')
        .then(r => r.json())
        .then(data => {
            venues = data.venues;
            venues.forEach(venue => {
                const option = document.createElement('option');
                option.value = venue.id;
                option.textContent = `${venue.name} - ${venue.city}`;
                venueSelect.appendChild(option);
            });
            if (cfg.preselectedVenueId) {
                venueSelect.value = String(cfg.preselectedVenueId);
                venueSelect.dispatchEvent(new Event('change'));
            }
        })
        .catch(err => console.error('Error fetching venues:', err));

    venueSelect.addEventListener('change', function () {
        const selectedValue = this.value;
        venueIdInput.value = selectedValue;
        if (selectedValue === 'new') {
            venueFieldIds.forEach(id => document.getElementById(id).value = '');
            venueFieldIds.forEach(id => document.getElementById(id).readOnly = false);
        } else {
            const selectedVenue = venues.find(v => v.id == selectedValue);
            if (selectedVenue) {
                document.getElementById('venue_name').value = selectedVenue.name || '';
                document.getElementById('venue_address').value = selectedVenue.address || '';
                document.getElementById('venue_city').value = selectedVenue.city || '';
                document.getElementById('venue_plz').value = selectedVenue.plz || '';
                document.getElementById('venue_canton').value = selectedVenue.canton || '';
                document.getElementById('venue_coords').value = selectedVenue.coords || '';
                venueFieldIds.forEach(id => document.getElementById(id).readOnly = true);
            }
        }
    });
});
