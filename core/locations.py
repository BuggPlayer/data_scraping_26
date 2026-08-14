"""
Major-city lists for country-wide scraping. Google Places Text Search does
NOT return complete national coverage from a single "in India" query — it
caps out around 60 results per query regardless of how big the area is. To
get real country-wide coverage you have to run one search per city per
category, so this module is the list of cities we loop over.

Each entry expands to a "City, Region, Country" string used in the Places
text query, and the country/region are stored on every lead so you can
filter/segment by jurisdiction later (calling/emailing compliance differs
by country - see README).

Lists are the top ~20-30 cities by population/business density per
country - not exhaustive. Add more if you need deeper tier-2/3 coverage;
each added city multiplies your query count (and API cost) by
len(categories).
"""

INDIA_CITIES = [
    ("Mumbai", "Maharashtra"), ("Delhi", "Delhi"), ("Bangalore", "Karnataka"),
    ("Hyderabad", "Telangana"), ("Chennai", "Tamil Nadu"), ("Kolkata", "West Bengal"),
    ("Pune", "Maharashtra"), ("Ahmedabad", "Gujarat"), ("Surat", "Gujarat"),
    ("Jaipur", "Rajasthan"), ("Lucknow", "Uttar Pradesh"), ("Kanpur", "Uttar Pradesh"),
    ("Nagpur", "Maharashtra"), ("Indore", "Madhya Pradesh"), ("Thane", "Maharashtra"),
    ("Bhopal", "Madhya Pradesh"), ("Visakhapatnam", "Andhra Pradesh"),
    ("Patna", "Bihar"), ("Vadodara", "Gujarat"), ("Ghaziabad", "Uttar Pradesh"),
    ("Ludhiana", "Punjab"), ("Agra", "Uttar Pradesh"), ("Nashik", "Maharashtra"),
    ("Faridabad", "Haryana"), ("Coimbatore", "Tamil Nadu"),
]

USA_CITIES = [
    ("New York", "New York"), ("Los Angeles", "California"), ("Chicago", "Illinois"),
    ("Houston", "Texas"), ("Phoenix", "Arizona"), ("Philadelphia", "Pennsylvania"),
    ("San Antonio", "Texas"), ("San Diego", "California"), ("Dallas", "Texas"),
    ("Austin", "Texas"), ("Jacksonville", "Florida"), ("San Jose", "California"),
    ("Fort Worth", "Texas"), ("Columbus", "Ohio"), ("Charlotte", "North Carolina"),
    ("San Francisco", "California"), ("Indianapolis", "Indiana"), ("Seattle", "Washington"),
    ("Denver", "Colorado"), ("Washington", "District of Columbia"), ("Boston", "Massachusetts"),
    ("Nashville", "Tennessee"), ("Atlanta", "Georgia"), ("Miami", "Florida"),
    ("Las Vegas", "Nevada"),
]

AUSTRALIA_CITIES = [
    ("Sydney", "New South Wales"), ("Melbourne", "Victoria"), ("Brisbane", "Queensland"),
    ("Perth", "Western Australia"), ("Adelaide", "South Australia"),
    ("Gold Coast", "Queensland"), ("Newcastle", "New South Wales"),
    ("Canberra", "Australian Capital Territory"), ("Sunshine Coast", "Queensland"),
    ("Wollongong", "New South Wales"), ("Hobart", "Tasmania"), ("Geelong", "Victoria"),
    ("Townsville", "Queensland"), ("Cairns", "Queensland"), ("Darwin", "Northern Territory"),
    ("Toowoomba", "Queensland"), ("Ballarat", "Victoria"), ("Bendigo", "Victoria"),
    ("Albury", "New South Wales"), ("Launceston", "Tasmania"),
]

COUNTRY_CITY_LISTS = {
    "India": INDIA_CITIES,
    "USA": USA_CITIES,
    "Australia": AUSTRALIA_CITIES,
}


def get_locations(countries: list) -> list:
    """Return every city for each of the requested countries (any of
    "India", "USA", "Australia") - used when a whole country is selected."""
    locations = []
    for country in countries:
        for city, region in COUNTRY_CITY_LISTS.get(country, []):
            locations.append(_location_dict(city, region, country))
    return locations


def get_locations_by_selection(selected: list) -> list:
    """Return location dicts for specific {"country", "city"} selections.
    Region is looked up server-side from COUNTRY_CITY_LISTS (not trusted
    from the client) so a tampered request can't inject a bogus region.
    Unknown country/city pairs are silently skipped."""
    locations = []
    for sel in selected:
        country = sel.get("country", "")
        city = sel.get("city", "")
        for known_city, region in COUNTRY_CITY_LISTS.get(country, []):
            if known_city == city:
                locations.append(_location_dict(city, region, country))
                break
    return locations


def _location_dict(city: str, region: str, country: str) -> dict:
    return {
        "city": city,
        "region": region,
        "country": country,
        "query_string": f"{city}, {region}, {country}",
    }
