"""
Category presets. Groups below target small/local businesses that are
plausible buyers of CRM, a website, AI services, or automation - the
signal we actually score for (no website / Facebook-page-only / phone but
no site) doubles as an ideal-customer-profile flag for this ICP, not just
a general lead-quality score.
"""

CATEGORY_GROUPS = {
    "professional_services": {
        "label": "Professional services (real estate, law, accounting, insurance)",
        "categories": [
            "Real estate agency", "Law firm", "Accounting firm",
            "Insurance agency", "Financial advisor", "Tax consultant",
        ],
    },
    "healthcare_wellness": {
        "label": "Healthcare & wellness (clinics, dental, salons, gyms)",
        "categories": [
            "Dental clinic", "Medical clinic", "Physiotherapy clinic",
            "Hair salon", "Spa", "Gym", "Yoga studio", "Chiropractor",
        ],
    },
    "retail_hospitality": {
        "label": "Retail & hospitality (shops, restaurants, cafes)",
        "categories": [
            "Clothing store", "Restaurant", "Cafe", "Bakery",
            "Grocery store", "Furniture store", "Jewelry store",
        ],
    },
    "trades_local_services": {
        "label": "Trades & local services (contractors, repair, home services)",
        "categories": [
            "Electrician", "Plumber", "AC repair", "Painter", "Carpenter",
            "Pest control", "Home cleaning service", "Roofing contractor",
        ],
    },
    "education_coaching": {
        "label": "Education & coaching (tutoring, coaching institutes, driving schools)",
        "categories": [
            "Tutoring center", "Coaching institute", "Driving school",
            "Language institute", "Computer training institute",
        ],
    },
    "automotive": {
        "label": "Automotive (repair shops, car dealers, tire shops)",
        "categories": [
            "Auto repair shop", "Car dealer", "Tire shop", "Car wash", "Auto parts store",
        ],
    },
    "events_hospitality": {
        "label": "Events & hospitality (wedding planners, banquet halls, decorators)",
        "categories": [
            "Wedding planner", "Banquet hall", "Event decorator",
            "Catering service", "Party supply store",
        ],
    },
    "it_creative_agencies": {
        "label": "IT & creative agencies (web/graphic design, marketing, photography)",
        "categories": [
            "Web design agency", "Graphic design studio", "Marketing agency",
            "Photography studio", "Video production company",
        ],
    },
}

# Kept for the original ProFixer/AMC lead-gen use case (a different, more
# specific business line than the digital-services ICP above).
LEGACY_PRESETS = {
    "profixer": [
        "Electrician", "Plumber", "AC repair", "Painter", "Carpenter",
        "Home cleaning service", "Pest control", "Roofing contractor",
        "Water purifier repair", "Electrical contractor",
        "Bathroom renovation", "Kitchen renovation", "Handyman", "TV repair",
    ],
    "amc": [
        "AC repair AMC", "Refrigerator repair", "Washing machine repair",
        "TV repair", "Water purifier service", "Home appliance repair",
        "Annual maintenance contract", "Microwave oven repair",
        "Geyser repair", "RO service", "Chimney repair",
        "Electrical appliance repair",
    ],
}


def categories_for_groups(group_keys: list) -> list:
    """Flatten selected group keys into a deduplicated category list."""
    seen = []
    for key in group_keys:
        group = CATEGORY_GROUPS.get(key)
        if not group:
            continue
        for c in group["categories"]:
            if c not in seen:
                seen.append(c)
    return seen
