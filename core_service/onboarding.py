"""Onboarding, verification and availability rules (spec Modules 2, 3 and 5)."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .accounts import record_event
from .models import Account, AccountStatus, Document, NgoProfile, RestaurantProfile, Review, new_id, utcnow

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
IMAGE_TYPES = ("image/jpeg", "image/png", "image/webp")
EVIDENCE_TYPES = IMAGE_TYPES + ("application/pdf",)


@dataclass(frozen=True)
class DocRule:
    label: str
    allowed: tuple[str, ...]
    min_count: int
    max_count: int


# Agreed Phase 2 defaults. Photo verification happens at first registration only; the host compares photos manually.
DOC_RULES: dict[str, dict[str, DocRule]] = {
    "restaurant": {
        "restaurant_photo": DocRule("Restaurant photo", IMAGE_TYPES, 1, 6),
        "food_licence": DocRule("Food licence", EVIDENCE_TYPES, 1, 2),
        "identity_proof": DocRule("Identity / business proof", EVIDENCE_TYPES, 1, 2),
    },
    "ngo": {
        "representative_photo": DocRule("Representative photo", IMAGE_TYPES, 1, 1),
        "ngo_registration": DocRule("NGO registration certificate", EVIDENCE_TYPES, 1, 2),
        "representative_id": DocRule("Representative ID", EVIDENCE_TYPES, 1, 2),
    },
}

PROFILE_MODELS = {"restaurant": RestaurantProfile, "ngo": NgoProfile}

DECISIONS = {"verify": AccountStatus.VERIFIED, "hold": AccountStatus.ON_HOLD, "decline": AccountStatus.DECLINED}


class RuleError(Exception):
    """A business-rule violation. ``status`` is the HTTP status the API should use."""

    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.message = message
        self.status = status


# ---------------------------------------------------------------- files

def sniff_content_type(data: bytes) -> str | None:
    """Identify the file from its first bytes; the client-supplied content type is never trusted."""
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith(b"%PDF-"):
        return "application/pdf"
    return None


def _safe_filename(name: str) -> str:
    name = Path(name or "upload").name.replace("\x00", "")
    return "".join(c for c in name if c.isprintable())[:200] or "upload"


# ---------------------------------------------------------------- views

def badges(account: Account, ngo_profile: NgoProfile | None = None) -> dict:
    """Status marks from Module 5. Verification and availability are independent signals."""
    verified = account.status == AccountStatus.VERIFIED
    if account.role == "restaurant":
        label = "Verified" if verified else ("On hold" if account.status == AccountStatus.ON_HOLD else "Unverified")
        return {"verification": {"color": "blue" if verified else "orange", "label": label}}
    if account.role == "ngo":
        available = bool(ngo_profile and ngo_profile.available)
        return {
            "verification": {"color": "green" if verified else "grey", "label": "Verified NGO" if verified else "Not verified"},
            "availability": {"color": "green" if available else "red", "label": "Available" if available else "Unavailable"},
        }
    return {}


def get_profile(db: Session, account: Account):
    model = PROFILE_MODELS.get(account.role)
    return db.get(model, account.id) if model else None


def documents_for(db: Session, account_id: str) -> list[Document]:
    return list(db.scalars(select(Document).where(Document.account_id == account_id).order_by(Document.created_at)))


def missing_items(db: Session, account: Account) -> list[str]:
    missing = []
    if get_profile(db, account) is None:
        missing.append("Profile details")
    counts = dict(db.execute(select(Document.kind, func.count()).where(Document.account_id == account.id)
                             .group_by(Document.kind)).all())
    for kind, rule in DOC_RULES.get(account.role, {}).items():
        if counts.get(kind, 0) < rule.min_count:
            missing.append(rule.label)
    return missing


def profile_dict(profile) -> dict | None:
    if profile is None:
        return None
    return {c.name: getattr(profile, c.name) for c in profile.__table__.columns if c.name != "account_id"}


def onboarding_view(db: Session, account: Account) -> dict:
    profile = get_profile(db, account)
    return {
        "account_id": account.id,
        "role": account.role,
        "status": account.status,
        "editable": account.status in AccountStatus.EDITABLE,
        "profile": profile_dict(profile),
        "documents": [document_dict(d) for d in documents_for(db, account.id)],
        "requirements": {k: {"label": r.label, "allowed": list(r.allowed), "min": r.min_count, "max": r.max_count}
                         for k, r in DOC_RULES.get(account.role, {}).items()},
        "missing": missing_items(db, account),
        "reviews": [review_dict(r) for r in db.scalars(select(Review).where(Review.account_id == account.id)
                                                       .order_by(Review.id.desc()))],
        "badges": badges(account, profile if account.role == "ngo" else None),
    }


def document_dict(d: Document) -> dict:
    return {"id": d.id, "kind": d.kind, "filename": d.filename, "content_type": d.content_type,
            "size_bytes": d.size_bytes, "created_at": d.created_at}


def review_dict(r: Review) -> dict:
    return {"id": r.id, "decision": r.decision, "from_status": r.from_status, "to_status": r.to_status,
            "note": r.note, "reviewer_id": r.reviewer_id, "created_at": r.created_at}


# ---------------------------------------------------------------- applicant actions

def _require_editable(account: Account) -> None:
    if account.status not in AccountStatus.EDITABLE:
        raise RuleError(f"the application cannot be changed while it is '{account.status}'")


def save_profile(db: Session, account: Account, data: dict, *, portal: str):
    _require_editable(account)
    model = PROFILE_MODELS[account.role]
    profile = db.get(model, account.id)
    if profile is None:
        profile = model(account_id=account.id, **data)
        db.add(profile)
    else:
        for k, v in data.items():
            setattr(profile, k, v)
    account.display_name = data["restaurant_name"] if account.role == "restaurant" else data["ngo_name"]
    record_event(db, "profile.updated", portal=portal, actor_id=account.id, subject_id=account.id)
    return profile


def add_document(db: Session, account: Account, *, kind: str, filename: str, data: bytes, upload_dir: Path,
                 portal: str) -> Document:
    _require_editable(account)
    rule = DOC_RULES.get(account.role, {}).get(kind)
    if rule is None:
        raise RuleError(f"unknown document type '{kind}'", 422)
    if not data:
        raise RuleError("the file is empty", 422)
    if len(data) > MAX_UPLOAD_BYTES:
        raise RuleError("the file is larger than 5 MB", 413)
    ctype = sniff_content_type(data)
    if ctype not in rule.allowed:
        allowed = ", ".join(t.split("/")[1].upper() for t in rule.allowed)
        raise RuleError(f"{rule.label} must be one of: {allowed}", 415)
    existing = list(db.scalars(select(Document).where(Document.account_id == account.id, Document.kind == kind)
                               .order_by(Document.created_at)))
    if len(existing) >= rule.max_count:
        if rule.max_count == 1:  # single-slot documents (e.g. representative photo) are replaced
            delete_document(db, account, existing[0], upload_dir=upload_dir, portal=portal, check_editable=False)
        else:
            raise RuleError(f"at most {rule.max_count} files allowed for {rule.label}; remove one first")
    storage_key = new_id()
    upload_dir.mkdir(parents=True, exist_ok=True)
    (upload_dir / storage_key).write_bytes(data)
    doc = Document(account_id=account.id, kind=kind, filename=_safe_filename(filename), content_type=ctype,
                   size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest(), storage_key=storage_key)
    db.add(doc)
    db.flush()
    record_event(db, "document.uploaded", portal=portal, actor_id=account.id, subject_id=account.id, kind=kind,
                 document_id=doc.id)
    return doc


def delete_document(db: Session, account: Account, doc: Document, *, upload_dir: Path, portal: str,
                    check_editable: bool = True) -> None:
    if check_editable:
        _require_editable(account)
    path = upload_dir / doc.storage_key
    db.delete(doc)
    db.flush()
    path.unlink(missing_ok=True)
    record_event(db, "document.deleted", portal=portal, actor_id=account.id, subject_id=account.id, kind=doc.kind)


def submit(db: Session, account: Account, *, portal: str) -> None:
    _require_editable(account)
    missing = missing_items(db, account)
    if missing:
        raise RuleError("application incomplete: " + ", ".join(missing), 422)
    previous = account.status
    account.status = AccountStatus.UNDER_REVIEW
    record_event(db, "application.submitted", portal=portal, actor_id=account.id, subject_id=account.id,
                 resubmission=previous == AccountStatus.ON_HOLD)


def set_availability(db: Session, account: Account, available: bool, *, portal: str) -> NgoProfile:
    profile = db.get(NgoProfile, account.id)
    if profile is None:
        raise RuleError("complete the NGO profile before setting availability")
    if profile.available != available:
        profile.available = available
        profile.availability_changed_at = utcnow()
        record_event(db, "availability.changed", portal=portal, actor_id=account.id, subject_id=account.id,
                     available=available)
    return profile


# ---------------------------------------------------------------- host actions

def review(db: Session, account: Account, *, decision: str, note: str | None, reviewer_id: str | None) -> Review:
    if account.role not in PROFILE_MODELS:
        raise RuleError("only restaurant and NGO applications can be reviewed", 422)
    if decision not in DECISIONS:
        raise RuleError("decision must be verify, hold or decline", 422)
    if account.status == AccountStatus.APPLICANT:
        raise RuleError("the application has not been submitted yet")
    target = DECISIONS[decision]
    if target == account.status:
        raise RuleError(f"the account is already '{target}'")
    note = (note or "").strip() or None
    if decision in ("hold", "decline") and not note:
        raise RuleError("a note for the applicant is required when holding or declining", 422)
    if decision == "verify" and missing_items(db, account):
        raise RuleError("cannot verify an incomplete application: " + ", ".join(missing_items(db, account)))
    rec = Review(account_id=account.id, reviewer_id=reviewer_id, decision=decision, from_status=account.status,
                 to_status=target, note=note)
    account.status = target
    db.add(rec)
    db.flush()
    record_event(db, f"review.{decision}", portal="admin", actor_id=reviewer_id, subject_id=account.id,
                 from_status=rec.from_status, to_status=target)
    return rec
