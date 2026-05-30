from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.infrastructure.db.database import Base


class NoteRow(Base):
    __tablename__ = "notes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    device_id: Mapped[str] = mapped_column(String(128), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    tags: Mapped[str] = mapped_column(Text, default="[]")       # JSON array
    entities: Mapped[str] = mapped_column(Text, default="{}")   # JSON object
    summary: Mapped[str] = mapped_column(Text, default="")
    audio_path: Mapped[str] = mapped_column(Text, default="")
    duration_s: Mapped[float] = mapped_column(Float, default=0.0)
    capture_mode: Mapped[str] = mapped_column(String(32), default="wake_word")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
