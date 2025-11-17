import os
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any

from fastapi import FastAPI, HTTPException, Depends, Header, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, EmailStr
from bson import ObjectId

from database import db, create_document, get_documents

app = FastAPI(title="Service Marketplace API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------
# Utility helpers
# ---------------------------

def to_object_id(id_str: str) -> ObjectId:
    try:
        return ObjectId(id_str)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid ID format")


def serialize(doc: Dict[str, Any]) -> Dict[str, Any]:
    if not doc:
        return doc
    d = dict(doc)
    if "_id" in d:
        d["id"] = str(d.pop("_id"))
    # Convert datetime to isoformat
    for k, v in list(d.items()):
        if isinstance(v, datetime):
            d[k] = v.astimezone(timezone.utc).isoformat()
    return d


# Very light password hashing (demo purposes only)
import hashlib
import secrets

def hash_password(password: str, salt: Optional[str] = None) -> Dict[str, str]:
    salt = salt or secrets.token_hex(8)
    hashed = hashlib.sha256((salt + password).encode()).hexdigest()
    return {"salt": salt, "hash": hashed}


def verify_password(password: str, salt: str, hash_val: str) -> bool:
    return hashlib.sha256((salt + password).encode()).hexdigest() == hash_val


def new_token() -> str:
    return secrets.token_hex(24)


# ---------------------------
# Models (requests/responses)
# ---------------------------
class SignupRequest(BaseModel):
    name: str
    email: EmailStr
    password: str
    country: Optional[str] = None
    province: Optional[str] = None


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class ProviderModeRequest(BaseModel):
    enabled: bool


class Question(BaseModel):
    id: str
    text: str
    type: str = Field("text", description="text|textarea|select|checkbox|number|file")
    required: bool = True
    options: Optional[List[str]] = None


class AvailabilitySlot(BaseModel):
    start: str
    end: str


class ServiceCreateRequest(BaseModel):
    name: str
    description: str
    price: float = Field(ge=0)
    category: str
    country: Optional[str] = None
    province: Optional[str] = None
    photos: List[str] = []
    videos: List[str] = []
    questions: List[Question] = []
    availability: List[AvailabilitySlot] = []


class ServiceUpdateRequest(ServiceCreateRequest):
    is_active: Optional[bool] = None


class BookingAnswer(BaseModel):
    question_id: str
    answer: Any


class BookingCreateRequest(BaseModel):
    service_id: str
    scheduled_start: Optional[str] = None
    scheduled_end: Optional[str] = None
    message: Optional[str] = None
    answers: List[BookingAnswer] = []


class BookingStatusUpdate(BaseModel):
    status: str = Field(..., pattern="^(accepted|declined|canceled)$")


# ---------------------------
# Auth dependency
# ---------------------------

def get_current_user(authorization: Optional[str] = Header(None)) -> Dict[str, Any]:
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Invalid auth scheme")
    token = authorization.split(" ", 1)[1].strip()
    user = db["user"].find_one({"tokens": token})
    if not user:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return serialize(user)


# ---------------------------
# Health & Utility
# ---------------------------
@app.get("/")
def read_root():
    return {"message": "Service Marketplace API running"}


@app.get("/test")
def test_database():
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": None,
        "database_name": None,
        "connection_status": "Not Connected",
        "collections": [],
    }
    try:
        if db is not None:
            response["database"] = "✅ Available"
            response["database_url"] = "✅ Set" if os.getenv("DATABASE_URL") else "❌ Not Set"
            response["database_name"] = db.name
            response["connection_status"] = "Connected"
            try:
                collections = db.list_collection_names()
                response["collections"] = collections[:10]
                response["database"] = "✅ Connected & Working"
            except Exception as e:
                response["database"] = f"⚠️ Connected but Error: {str(e)[:50]}"
        else:
            response["database"] = "⚠️ Available but not initialized"
    except Exception as e:
        response["database"] = f"❌ Error: {str(e)[:50]}"
    return response


# ---------------------------
# Authentication
# ---------------------------
@app.post("/auth/signup")
def signup(payload: SignupRequest):
    existing = db["user"].find_one({"email": payload.email.lower()})
    if existing:
        raise HTTPException(status_code=409, detail="Email already registered")
    pwd = hash_password(payload.password)
    token = new_token()
    user_doc = {
        "name": payload.name,
        "email": payload.email.lower(),
        "country": payload.country,
        "province": payload.province,
        "provider_mode": False,
        "avatar_url": None,
        "password": {"salt": pwd["salt"], "hash": pwd["hash"]},
        "tokens": [token],
    }
    result_id = db["user"].insert_one(user_doc).inserted_id
    return {"user": {"id": str(result_id), "name": payload.name, "email": payload.email, "provider_mode": False}, "token": token}


@app.post("/auth/login")
def login(payload: LoginRequest):
    user = db["user"].find_one({"email": payload.email.lower()})
    if not user or "password" not in user:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if not verify_password(payload.password, user["password"]["salt"], user["password"]["hash"]):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = new_token()
    db["user"].update_one({"_id": user["_id"]}, {"$push": {"tokens": token}})
    return {"user": serialize(user), "token": token}


@app.get("/me")
def me(current=Depends(get_current_user)):
    return current


@app.post("/me/provider-mode")
def set_provider_mode(req: ProviderModeRequest, current=Depends(get_current_user)):
    db["user"].update_one({"_id": to_object_id(current["id"])}, {"$set": {"provider_mode": req.enabled}})
    return {"provider_mode": req.enabled}


# ---------------------------
# Services
# ---------------------------
@app.post("/services")
def create_service(data: ServiceCreateRequest, current=Depends(get_current_user)):
    if not current.get("provider_mode"):
        raise HTTPException(status_code=403, detail="Enable provider mode to create services")
    doc = data.model_dump()
    doc["provider_id"] = current["id"]
    new_id = create_document("service", doc)
    inserted = db["service"].find_one({"_id": to_object_id(new_id)})
    return serialize(inserted)


@app.get("/services")
def list_services(q: Optional[str] = None, country: Optional[str] = None, province: Optional[str] = None,
                  category: Optional[str] = None, provider_id: Optional[str] = None, limit: int = 50):
    filt: Dict[str, Any] = {"is_active": {"$ne": False}}
    if country:
        filt["country"] = country
    if province:
        filt["province"] = province
    if category:
        filt["category"] = category
    if provider_id:
        filt["provider_id"] = provider_id
    if q:
        filt["$or"] = [
            {"name": {"$regex": q, "$options": "i"}},
            {"description": {"$regex": q, "$options": "i"}}
        ]
    cursor = db["service"].find(filt).limit(min(limit, 100))
    return [serialize(x) for x in cursor]


@app.get("/services/{service_id}")
def get_service(service_id: str):
    svc = db["service"].find_one({"_id": to_object_id(service_id)})
    if not svc:
        raise HTTPException(status_code=404, detail="Service not found")
    return serialize(svc)


@app.put("/services/{service_id}")
def update_service(service_id: str, payload: ServiceUpdateRequest, current=Depends(get_current_user)):
    svc = db["service"].find_one({"_id": to_object_id(service_id)})
    if not svc:
        raise HTTPException(status_code=404, detail="Service not found")
    if svc.get("provider_id") != current["id"]:
        raise HTTPException(status_code=403, detail="Not your service")
    update = {k: v for k, v in payload.model_dump().items() if v is not None}
    update["updated_at"] = datetime.now(timezone.utc)
    db["service"].update_one({"_id": svc["_id"]}, {"$set": update})
    new_doc = db["service"].find_one({"_id": svc["_id"]})
    return serialize(new_doc)


@app.delete("/services/{service_id}")
def delete_service(service_id: str, current=Depends(get_current_user)):
    svc = db["service"].find_one({"_id": to_object_id(service_id)})
    if not svc:
        raise HTTPException(status_code=404, detail="Service not found")
    if svc.get("provider_id") != current["id"]:
        raise HTTPException(status_code=403, detail="Not your service")
    db["service"].delete_one({"_id": svc["_id"]})
    return {"deleted": True}


# ---------------------------
# Bookings
# ---------------------------
@app.post("/bookings")
def create_booking(data: BookingCreateRequest, current=Depends(get_current_user)):
    svc = db["service"].find_one({"_id": to_object_id(data.service_id)})
    if not svc:
        raise HTTPException(status_code=404, detail="Service not found")
    doc = {
        "service_id": data.service_id,
        "provider_id": svc["provider_id"],
        "customer_id": current["id"],
        "scheduled_start": data.scheduled_start,
        "scheduled_end": data.scheduled_end,
        "message": data.message,
        "answers": [a.model_dump() for a in data.answers],
        "status": "pending",
        "total_price": svc.get("price"),
    }
    new_id = create_document("booking", doc)
    created = db["booking"].find_one({"_id": to_object_id(new_id)})
    return serialize(created)


@app.get("/bookings")
def list_bookings(role: str = Query("customer", pattern="^(customer|provider)$"), current=Depends(get_current_user)):
    filt = {"customer_id": current["id"]} if role == "customer" else {"provider_id": current["id"]}
    cursor = db["booking"].find(filt).sort("created_at", -1)
    return [serialize(x) for x in cursor]


@app.patch("/bookings/{booking_id}/status")
def update_booking_status(booking_id: str, req: BookingStatusUpdate, current=Depends(get_current_user)):
    bk = db["booking"].find_one({"_id": to_object_id(booking_id)})
    if not bk:
        raise HTTPException(status_code=404, detail="Booking not found")
    if bk.get("provider_id") != current["id"]:
        raise HTTPException(status_code=403, detail="Only provider can change status")
    db["booking"].update_one({"_id": bk["_id"]}, {"$set": {"status": req.status, "updated_at": datetime.now(timezone.utc)}})
    new_doc = db["booking"].find_one({"_id": bk["_id"]})
    return serialize(new_doc)


# Optional: expose schemas for tooling
@app.get("/schema")
def get_schema_models():
    from schemas import User, Service, Booking  # type: ignore
    return {
        "models": [
            {"name": "User", "collection": "user", "fields": list(User.model_fields.keys())},
            {"name": "Service", "collection": "service", "fields": list(Service.model_fields.keys())},
            {"name": "Booking", "collection": "booking", "fields": list(Booking.model_fields.keys())},
        ]
    }


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
