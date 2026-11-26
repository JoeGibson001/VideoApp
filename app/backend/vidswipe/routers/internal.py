"""Internal endpoints called by the Importer (DESIGN.md 10, 11.5). Auth: X-Ingest-Key."""
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Source, Video
from ..security import require_ingest_key
from ..services.ingest import IngestPayload, ingest

router = APIRouter(prefix="/api/internal", tags=["internal"], dependencies=[Depends(require_ingest_key)])


@router.post("/ingest")
def ingest_video(payload: IngestPayload, db: Session = Depends(get_db)):
    video, created = ingest(db, payload)
    return {"video_id": video.id, "created": created}


@router.get("/exists")
def exists(source_domain: str, source_video_id: str, db: Session = Depends(get_db)):
    domain = source_domain.lower().removeprefix("www.")
    vid = db.scalar(select(Video.id).join(Source, Source.id == Video.source_id).where(
        Source.domain == domain, Video.source_video_id == source_video_id))
    return {"exists": vid is not None, "video_id": vid}
