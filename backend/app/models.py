from datetime import datetime, date, time
from typing import Optional
from sqlalchemy import String, Integer, Float, Text, Date, Time, DateTime, ForeignKey, JSON, Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship
import enum

from app.database import Base


class EventStatus(str, enum.Enum):
    on_sale = "on_sale"
    sold_out = "sold_out"
    cancelled = "cancelled"
    free = "free"


class ScrapeStatus(str, enum.Enum):
    running = "running"
    success = "success"
    failed = "failed"


class Venue(Base):
    __tablename__ = "venues"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    city: Mapped[str] = mapped_column(String(50))
    capacity: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    size_category: Mapped[str] = mapped_column(String(20))  # small, medium, large
    website: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    ticketmaster_venue_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    scraper_type: Mapped[str] = mapped_column(String(50))
    scraper_config: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    color: Mapped[str] = mapped_column(String(7), default="#6366f1")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    events: Mapped[list["Event"]] = relationship(back_populates="venue", cascade="all, delete-orphan")
    scrape_logs: Mapped[list["ScrapeLog"]] = relationship(back_populates="venue", cascade="all, delete-orphan")


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(primary_key=True)
    external_id: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    venue_id: Mapped[int] = mapped_column(ForeignKey("venues.id"), index=True)
    name: Mapped[str] = mapped_column(String(500))
    artist: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    support_artists: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    doors_time: Mapped[Optional[time]] = mapped_column(Time, nullable=True)
    show_time: Mapped[Optional[time]] = mapped_column(Time, nullable=True)
    ticket_url: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    price_min: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    price_max: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    image_url: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    genre: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    subgenre: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default=EventStatus.on_sale.value)
    age_restriction: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(50))
    source_url: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    venue: Mapped["Venue"] = relationship(back_populates="events")


class ScrapeLog(Base):
    __tablename__ = "scrape_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    venue_id: Mapped[int] = mapped_column(ForeignKey("venues.id"), index=True)
    scraper_type: Mapped[str] = mapped_column(String(50))
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default=ScrapeStatus.running.value)
    events_found: Mapped[int] = mapped_column(Integer, default=0)
    events_created: Mapped[int] = mapped_column(Integer, default=0)
    events_updated: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    duration_seconds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    venue: Mapped["Venue"] = relationship(back_populates="scrape_logs")
