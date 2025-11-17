"""
Database Schemas for the Service Marketplace

Each Pydantic model represents a MongoDB collection. The collection name is the
lowercase of the class name (e.g., User -> "user").
"""
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field, EmailStr
from datetime import datetime


class User(BaseModel):
    """
    Users of the platform. Users can also become providers by enabling provider_mode.
    Collection: "user"
    """
    name: str = Field(..., description="Full name")
    email: EmailStr = Field(..., description="Unique email address")
    country: Optional[str] = Field(None, description="Country of residence")
    province: Optional[str] = Field(None, description="Province/State of residence")
    provider_mode: bool = Field(False, description="Is the user a service provider")
    avatar_url: Optional[str] = Field(None, description="Profile image URL")


class Question(BaseModel):
    """
    A pre-service question that customers must answer when booking a service.
    """
    id: str = Field(..., description="Client-side stable id for the question")
    text: str = Field(..., description="The prompt shown to the customer")
    type: str = Field("text", description="text | textarea | select | checkbox | number | file")
    required: bool = Field(True)
    options: Optional[List[str]] = Field(None, description="For select/checkbox types")


class AvailabilitySlot(BaseModel):
    """Simple availability slot represented as ISO 8601 datetime string."""
    start: str = Field(..., description="ISO datetime of the slot start")
    end: str = Field(..., description="ISO datetime of the slot end")


class Service(BaseModel):
    """
    Service listings created by providers.
    Collection: "service"
    """
    provider_id: str = Field(..., description="Owner user id (ObjectId as string)")
    name: str
    description: str
    price: float = Field(..., ge=0)
    category: str
    country: Optional[str] = None
    province: Optional[str] = None
    photos: List[str] = Field(default_factory=list)
    videos: List[str] = Field(default_factory=list)
    questions: List[Question] = Field(default_factory=list)
    availability: List[AvailabilitySlot] = Field(default_factory=list)
    is_active: bool = Field(True)


class BookingAnswer(BaseModel):
    question_id: str
    answer: Any


class Booking(BaseModel):
    """
    Booking requests from customers to providers for a specific service.
    Collection: "booking"
    """
    service_id: str
    provider_id: str
    customer_id: str
    scheduled_start: Optional[str] = Field(None, description="Requested start time ISO string")
    scheduled_end: Optional[str] = Field(None, description="Requested end time ISO string")
    message: Optional[str] = None
    answers: List[BookingAnswer] = Field(default_factory=list)
    status: str = Field("pending", description="pending | accepted | declined | canceled")
    total_price: Optional[float] = None


# Note: The Flames database viewer may use GET /schema to discover these models.
