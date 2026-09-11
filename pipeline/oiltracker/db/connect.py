"""Opening connections to the PostGIS store."""

import psycopg
from psycopg.rows import TupleRow

from oiltracker.settings import Settings


def connect(settings: Settings) -> psycopg.Connection[TupleRow]:
    """Open a connection whose session timezone is UTC.

    Every timestamp in this pipeline is an absolute instant; pinning the session
    timezone keeps partition bounds and date truncation independent of the host.
    """
    return psycopg.connect(settings.database_url, options="-c timezone=UTC")
