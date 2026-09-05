from typing import Literal
from pydantic import BaseModel, Field, HttpUrl


class SourceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    start_url: HttpUrl
    auto_scan: bool = False
    interval_minutes: int = Field(default=60, ge=5, le=10080)
    max_pages: int = Field(default=5000, ge=1, le=250000)
    max_depth: int = Field(default=12, ge=0, le=100)
    concurrency: int = Field(default=6, ge=1, le=32)
    delay_ms: int = Field(default=350, ge=0, le=60000)
    render_mode: Literal["auto", "http", "browser"] = "auto"
    respect_robots: bool = True


class SourceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    auto_scan: bool | None = None
    interval_minutes: int | None = Field(default=None, ge=5, le=10080)
    max_pages: int | None = Field(default=None, ge=1, le=250000)
    max_depth: int | None = Field(default=None, ge=0, le=100)
    concurrency: int | None = Field(default=None, ge=1, le=32)
    delay_ms: int | None = Field(default=None, ge=0, le=60000)
    render_mode: Literal["auto", "http", "browser"] | None = None
    respect_robots: bool | None = None


class ScanOptions(BaseModel):
    force_full: bool = False
