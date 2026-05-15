"""Configuration constants for IRC data collection."""

from pathlib import Path

# Project paths
PROJECT_ROOT = Path(__file__).parent.parent.parent
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
TCC_LISTINGS_DIR = RAW_DIR / "tcc_listings"
CERTIFICATES_DIR = RAW_DIR / "certificates"
RACE_RESULTS_DIR = RAW_DIR / "race_results"
IMPORTS_DIR = RAW_DIR / "imports"

# Database
DATABASE_URL = "postgresql+psycopg://irc:irc@localhost:5433/irc_data"

# IRC Rating URLs
IRC_CERTIFICATE_SEARCH_URL = "https://ircrating.org/boat-data-for-valid-irc-certificates/"
IRC_TCC_LISTING_URL = "https://ircrating.org"
IRC_PDF_BASE_URL = "https://ircrating.org/pdfdirectory"

# Historical certificates
HISTORICAL_CERTS_DIR = RAW_DIR / "certificates" / "historical"

# ORC data
ORC_API_BASE = "https://data.orc.org/public/WPub.dll"
ORC_RAW_DIR = RAW_DIR / "orc"

# ORC member countries (code -> name)
ORC_COUNTRIES = {
    "ARG": "Argentina",
    "AUS": "Australia",
    "AUT": "Austria",
    "BRA": "Brazil",
    "CAN": "Canada",
    "CRO": "Croatia",
    "ECU": "Ecuador",
    "FIN": "Finland",
    "FRA": "France",
    "GER": "Germany",
    "GBR": "Great Britain",
    "GRE": "Greece",
    "HKG": "Hong Kong",
    "HUN": "Hungary",
    "IRL": "Ireland",
    "ISR": "Israel",
    "ITA": "Italy",
    "JPN": "Japan",
    "KOR": "Korea",
    "MU_": "Multihulls Rating Office",
    "NED": "Netherlands",
    "NOR": "Norway",
    "PER": "Peru",
    "POL": "Poland",
    "POR": "Portugal",
    "ROU": "Romania",
    "SLO": "Slovenia",
    "RSA": "South Africa",
    "ESP": "Spain",
    "SUI": "Switzerland",
    "TUR": "Turkey",
    "USA": "United States",
}

# Wayback Machine
WAYBACK_CDX_URL = "https://web.archive.org/cdx/search/cdx"
WAYBACK_BASE_URL = "https://web.archive.org/web"

# Race result sources
CYCA_RESULTS_URL = "https://www.cyca.com.au/results"
SAILRACEHQ_URL = "https://www.sailracehq.com/results"
SAILWAVE_URL = "https://www.sailwave.com/results"
TOPYACHT_URL = "https://www.topyacht.net.au/results"
SAILSYS_URL = "https://app.sailsys.com.au"

# Scraping
DEFAULT_DELAY_SECONDS = 2.0
MAX_RETRIES = 3

# Known Sunfast 3300 dimensions for design detection
SUNFAST_3300_LH = 9.99
SUNFAST_3300_BEAM = 3.40
DESIGN_MATCH_TOLERANCE = 0.10  # metres — SF3300 LH varies 9.95-10.04, Beam 3.35-3.41

# Sail number country prefixes
COUNTRY_PREFIXES = {
    "AUS": "AUS",
    "GBR": "GBR",
    "IRL": "IRL",
    "FRA": "FRA",
    "NZL": "NZL",
    "FIN": "FIN",
    "NED": "NED",
    "D": "GER",
    "JPN": "JPN",
    "USA": "USA",
    "ESP": "ESP",
    "ITA": "ITA",
    "BEL": "BEL",
    "DEN": "DEN",
    "SWE": "SWE",
    "NOR": "NOR",
    "HKG": "HKG",
    "RSA": "RSA",
    "CAN": "CAN",
    "POR": "POR",
    "GER": "GER",
    "SUI": "SUI",
    "POL": "POL",
    "CRO": "CRO",
    "GRE": "GRE",
    "TUR": "TUR",
    "RUS": "RUS",
    "CHN": "CHN",
    "SIN": "SIN",
    "MAS": "MAS",
    "THA": "THA",
}
