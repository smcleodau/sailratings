"""Sunfast 3300 specific analysis."""

from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.engine import Engine


def get_all_sunfast_3300(engine: Engine) -> list[dict]:
    """Get all Sunfast 3300 boats with their latest TCC data."""
    query = """
        SELECT b.id, b.boat_name, b.sail_number, b.country, b.year_built,
               t.tcc, t.non_spi_tcc, t.dlr, t.crew,
               t.lh, t.beam, t.draft,
               t.headsails, t.flying_headsails, t.spinnakers,
               t.stix, t.avs, t.category
        FROM boats b
        JOIN LATERAL (
            SELECT * FROM tcc_snapshots
            WHERE boat_id = b.id
            ORDER BY snapshot_date DESC
            LIMIT 1
        ) t ON true
        WHERE b.design = 'Sunfast 3300'
        ORDER BY t.tcc ASC
    """
    with engine.connect() as conn:
        result = conn.execute(text(query))
        return [dict(row._mapping) for row in result]


def get_sunfast_certificates(engine: Engine) -> list[dict]:
    """Get all Sunfast 3300 boats with certificate measurement data + TCC."""
    query = """
        SELECT b.boat_name, b.sail_number, b.country,
               t.tcc, t.non_spi_tcc, t.dlr,
               t.headsails, t.spinnakers,
               c.displacement as weight, c.p, c.e, c.j, c.stl, c.hlp,
               c.muw, c.mtw, c.mhw,
               c.hlu, c.huw, c.htw, c.hhw,
               c.sym_slu, c.sym_sle, c.sym_sf, c.sym_shw,
               c.bo, c.so, c.water_ballast, c.spreaders,
               c.cert_number
        FROM boats b
        JOIN LATERAL (
            SELECT * FROM tcc_snapshots
            WHERE boat_id = b.id
            ORDER BY snapshot_date DESC
            LIMIT 1
        ) t ON true
        JOIN irc_certificates c ON c.boat_id = b.id
        WHERE b.design = 'Sunfast 3300'
        ORDER BY t.tcc ASC
    """
    with engine.connect() as conn:
        result = conn.execute(text(query))
        return [dict(row._mapping) for row in result]


def sail_config_analysis(engine: Engine) -> list[dict]:
    """Analyze how sail configuration affects TCC for Sunfast 3300s."""
    query = """
        SELECT
            t.headsails, t.spinnakers,
            COUNT(*) as count,
            MIN(t.tcc) as min_tcc,
            MAX(t.tcc) as max_tcc,
            AVG(t.tcc) as avg_tcc,
            ARRAY_AGG(b.boat_name ORDER BY t.tcc) as boats
        FROM boats b
        JOIN LATERAL (
            SELECT * FROM tcc_snapshots
            WHERE boat_id = b.id
            ORDER BY snapshot_date DESC
            LIMIT 1
        ) t ON true
        WHERE b.design = 'Sunfast 3300'
        GROUP BY t.headsails, t.spinnakers
        ORDER BY avg_tcc
    """
    with engine.connect() as conn:
        result = conn.execute(text(query))
        return [dict(row._mapping) for row in result]


def sensitivity_analysis(engine: Engine) -> dict:
    """Compute measurement correlations and per-unit TCC impact for SF3300s.

    Returns dict with:
      - correlations: {field: correlation_with_tcc}
      - ranges: {field: (min, max)}
      - tcc_range: (min_tcc, max_tcc)
    """
    boats = get_sunfast_certificates(engine)
    if len(boats) < 3:
        return {}

    import numpy as np

    tcc_values = np.array([float(b["tcc"]) for b in boats])
    fields = [
        "weight", "p", "e", "j", "stl", "hlp",
        "muw", "mtw", "mhw", "hlu",
        "sym_slu", "sym_sle",
        "bo", "so",
    ]

    correlations = {}
    ranges = {}
    for field in fields:
        vals = []
        tccs = []
        for i, b in enumerate(boats):
            v = b.get(field)
            if v is not None:
                vals.append(float(v))
                tccs.append(tcc_values[i])
        if len(vals) > 2:
            arr = np.array(vals)
            tarr = np.array(tccs)
            correlations[field] = float(np.corrcoef(arr, tarr)[0, 1])
            ranges[field] = (float(arr.min()), float(arr.max()))

    return {
        "correlations": correlations,
        "ranges": ranges,
        "tcc_range": (float(tcc_values.min()), float(tcc_values.max())),
        "n_boats": len(boats),
    }
