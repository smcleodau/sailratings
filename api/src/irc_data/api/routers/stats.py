from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.engine import Engine

from irc_data.api.deps import get_db

router = APIRouter(prefix="/stats", tags=["stats"])

@router.get("/")
def get_stats(db: Engine = Depends(get_db)):
    """Return live database statistics."""
    with db.connect() as conn:
        stats = {}
        
        # Boats
        row = conn.execute(text("SELECT COUNT(*) FROM boats")).fetchone()
        stats["boats"] = row[0] if row else 0
        
        # TCC Snapshots
        row = conn.execute(text("SELECT COUNT(*) FROM tcc_snapshots")).fetchone()
        stats["tcc_snapshots"] = row[0] if row else 0
        
        # Certificates (PDFs)
        row = conn.execute(text("SELECT COUNT(*) FROM irc_certificates")).fetchone()
        stats["irc_certificates"] = row[0] if row else 0
        
        # ORC Certificates
        row = conn.execute(text("SELECT COUNT(*) FROM orc_certificates")).fetchone()
        stats["orc_certificates"] = row[0] if row else 0
        
        # Race Results
        row = conn.execute(text("SELECT COUNT(*) FROM race_results")).fetchone()
        stats["race_results"] = row[0] if row else 0
        
        # Countries
        row = conn.execute(text("SELECT COUNT(DISTINCT country) FROM boats WHERE country IS NOT NULL")).fetchone()
        stats["countries"] = row[0] if row else 0
        
        # Designs
        row = conn.execute(text("SELECT COUNT(DISTINCT design) FROM boats WHERE design IS NOT NULL")).fetchone()
        stats["designs"] = row[0] if row else 0
        
        return stats
