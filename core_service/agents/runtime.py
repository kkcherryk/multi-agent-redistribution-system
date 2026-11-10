"""Agent runtime: a small in-process multi-agent system.

* ``Agent`` subclasses react to messages (``handle``) and act periodically (``tick``).
* ``MessageBus`` delivers messages between agents synchronously and records every message in ``agent_messages``,
  so the host can see what each agent observed, asked and decided.
* ``AgentRuntime`` owns the agents and the bus; a background thread calls ``tick`` every few seconds in the
  running app, while tests call it directly.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime

from sqlalchemy.orm import Session, sessionmaker

from ..models import AgentMessage, utcnow

log = logging.getLogger("frs.agents")


class Agent:
    name = "agent"
    description = ""

    def __init__(self) -> None:
        self.bus: MessageBus | None = None
        self.last_tick: datetime | None = None
        self.last_error: str | None = None

    def handle(self, db: Session, msg: AgentMessage) -> dict | None:  # pragma: no cover - default no-op
        return None

    def tick(self, db: Session) -> None:  # pragma: no cover - default no-op
        return None

    def send(self, db: Session, recipient: str, topic: str, *, listing_id: str | None = None, **payload) -> dict | None:
        return self.bus.send(db, self.name, recipient, topic, listing_id=listing_id, **payload)


class MessageBus:
    def __init__(self) -> None:
        self.agents: dict[str, Agent] = {}

    def register(self, agent: Agent) -> None:
        agent.bus = self
        self.agents[agent.name] = agent

    def send(self, db: Session, sender: str, recipient: str, topic: str, *, listing_id: str | None = None,
             **payload) -> dict | None:
        """Record the message; if the recipient is an agent, deliver it now and return its reply."""
        msg = AgentMessage(sender=sender, recipient=recipient, topic=topic, listing_id=listing_id, payload=payload)
        db.add(msg)
        db.flush()
        target = self.agents.get(recipient)
        return target.handle(db, msg) if target else None


class AgentRuntime:
    def __init__(self, agents: list[Agent]) -> None:
        self.bus = MessageBus()
        for a in agents:
            self.bus.register(a)
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def get(self, name: str) -> Agent:
        return self.bus.agents[name]

    def tick(self, db: Session) -> None:
        """One round: every agent observes the current state and acts. Serialised so rounds never overlap."""
        with self._lock:
            for agent in self.bus.agents.values():
                try:
                    agent.tick(db)
                    db.commit()
                    agent.last_error = None
                except Exception as exc:  # one failing agent must not stop the others
                    db.rollback()
                    agent.last_error = f"{type(exc).__name__}: {exc}"
                    log.exception("agent %s failed", agent.name)
                agent.last_tick = utcnow()

    def status(self) -> list[dict]:
        return [{"name": a.name, "description": a.description, "last_tick": a.last_tick, "last_error": a.last_error}
                for a in self.bus.agents.values()]

    # ---- background loop for the running app
    def start(self, session_factory: sessionmaker, interval_seconds: float) -> None:
        if self._thread is not None:
            return

        def loop() -> None:
            while not self._stop.wait(interval_seconds):
                with session_factory() as db:
                    self.tick(db)

        self._thread = threading.Thread(target=loop, name="frs-agents", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
