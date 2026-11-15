"""Host-only API for Phase 9 learned dispatch: status, train (in the simulator), switch on/off."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .agents.logistics import LEARNED_DISPATCH_KEY
from .models import AppSetting, utcnow


class TrainIn(BaseModel):
    episodes: int = Field(default=300, ge=20, le=1000)


class SwitchIn(BaseModel):
    enabled: bool
    host_id: str | None = Field(default=None, max_length=32)


def build_router(*, get_db, require_admin, policy_path: Path | None) -> APIRouter:
    from simulation.learning import load_policy, train_and_save  # local: keeps app start-up light

    r = APIRouter(prefix="/internal/learned-dispatch")

    def status(db: Session) -> dict:
        setting = db.get(AppSetting, LEARNED_DISPATCH_KEY)
        policy = load_policy(policy_path)
        return {
            "enabled": bool(setting and setting.value == "on" and policy),
            "switched_on": bool(setting and setting.value == "on"),
            "trained": policy is not None,
            # .get: a policy with valid weights but no training details (e.g. hand-made) must not break this page
            "policy": None if policy is None else {
                k: policy.get(k) for k in ("trained_at", "episodes", "weights", "hand_set_weights", "explanation",
                                           "training_curve", "test", "reward")},
        }

    @r.get("")
    def get_status(_: str = Depends(require_admin), db: Session = Depends(get_db)):
        return status(db)

    @r.post("/train")
    def train(body: TrainIn, _: str = Depends(require_admin), db: Session = Depends(get_db)):
        train_and_save(episodes=body.episodes, **({"path": policy_path} if policy_path else {}))
        return status(db)

    @r.put("")
    def switch(body: SwitchIn, _: str = Depends(require_admin), db: Session = Depends(get_db)):
        if body.enabled and load_policy(policy_path) is None:
            raise HTTPException(status_code=409, detail="train the learned dispatcher before switching it on")
        setting = db.get(AppSetting, LEARNED_DISPATCH_KEY)
        if setting is None:
            setting = AppSetting(key=LEARNED_DISPATCH_KEY, value="off")
            db.add(setting)
        setting.value, setting.updated_by, setting.updated_at = ("on" if body.enabled else "off"), body.host_id, utcnow()
        db.commit()
        return status(db)

    return r
