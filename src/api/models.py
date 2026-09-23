"""
pHantasma REST API Models
Pydantic models for request/response validation.
"""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class CommandType(str, Enum):
    VOICE = "voice"
    TEXT = "text"
    DEVICE_CONTROL = "device_control"
    MEMORY = "memory"


class CommandRequest(BaseModel):
    """Request to execute a command."""

    text: str = Field(..., min_length=1, max_length=5000)
    type: CommandType = CommandType.TEXT
    language: Optional[str] = None  # "pt", "en", None=auto
    device_id: Optional[str] = None  # For device control


class CommandResponse(BaseModel):
    """Response from command execution."""

    success: bool
    text: Optional[str] = None
    audio_base64: Optional[str] = None  # Base64 encoded WAV/MP3
    audio_format: Optional[str] = None  # "wav", "mp3"
    device_states: Optional[Dict[str, Any]] = None
    memory_updated: Optional[bool] = None
    error: Optional[str] = None
    processing_time_ms: float = 0.0


class DeviceInfo(BaseModel):
    """Device information from config."""

    name: str
    type: str  # tuya_switch, tuya_light, tuya_sensor,
    # xiaomi_vacuum, xiaomi_light
    room: Optional[str] = None
    state: Optional[Dict[str, Any]] = None
    online: bool = True


class DeviceControlRequest(BaseModel):
    """Request to control a device."""

    device_name: str
    action: str  # on, off, toggle, set_brightness,
    # set_color, start_clean, etc.
    parameters: Optional[Dict[str, Any]] = None


class DeviceControlResponse(BaseModel):
    success: bool
    device_name: str
    new_state: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


class MemoryEntry(BaseModel):
    """Long-term memory entry."""

    id: Optional[int] = None
    key: str
    value: str
    created_at: datetime
    updated_at: Optional[datetime] = None


class MemoryRequest(BaseModel):
    key: str = Field(..., min_length=1, max_length=200)
    value: str = Field(..., min_length=1, max_length=5000)


class MemoryResponse(BaseModel):
    success: bool
    entry: Optional[MemoryEntry] = None
    entries: Optional[List[MemoryEntry]] = None
    error: Optional[str] = None


class HealthStatus(BaseModel):
    """Health check response."""

    status: str  # "healthy", "degraded", "unhealthy"
    version: str
    uptime_seconds: float
    components: Dict[str, str]  # component -> status
    timestamp: datetime


class ServerInfo(BaseModel):
    """Server information for discovery."""

    name: str = "pHantasma"
    version: str
    api_version: str = "v1"
    endpoints: List[str]
    capabilities: List[str]  # ["voice", "devices", "memory", "tts", "stt"]


class STTRequest(BaseModel):
    """Speech-to-text request."""

    audio_base64: str
    language: Optional[str] = None
    format: str = "wav"  # "wav", "mp3", "ogg"


class STTResponse(BaseModel):
    success: bool
    text: Optional[str] = None
    language: Optional[str] = None
    duration_ms: float = 0.0
    error: Optional[str] = None


class TTSRequest(BaseModel):
    """Text-to-speech request."""

    text: str = Field(..., min_length=1, max_length=5000)
    language: Optional[str] = "pt"
    format: str = "wav"  # "wav", "mp3"


class TTSResponse(BaseModel):
    success: bool
    audio_base64: Optional[str] = None
    format: str = "wav"
    duration_ms: float = 0.0
    error: Optional[str] = None
