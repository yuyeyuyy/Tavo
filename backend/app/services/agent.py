from uuid import uuid4

from sqlalchemy import func, select

from app.api.schemas import AnalysisInput, RunOut, RunPage
from app.core.config import utcnow
from app.core.errors import AppError, Code
from app.models.entities import AgentRun, BusinessEvent, LegacyRecord


class AgentService:
    def __init__(self, db, now=utcnow):
        self.db, self.now = db, now

    def input_in(self, session, event_id):
        event = session.scalar(select(BusinessEvent).where(BusinessEvent.event_id == event_id))
        if event:
            current = event.payload.get("current")
            if event.event_type != "data.changed" or not current:
                raise AppError(Code.INVALID_INPUT)
            return event.account_id, AnalysisInput(
                id=event_id, balance=current["balance"], currency=current["currency"]
            )
        legacy = session.scalar(
            select(LegacyRecord).where(
                LegacyRecord.table_name == "events", LegacyRecord.record_id == event_id
            )
        )
        if legacy and legacy.connection_id:
            return legacy.connection_id, AnalysisInput(
                id=event_id, balance=legacy.data["balance"], currency=legacy.data["currency"]
            )
        raise AppError(Code.NOT_FOUND)

    def enqueue(self, event_id):
        with self.db.session(write=True) as session:
            account_id, _input = self.input_in(session, event_id)
            if session.scalar(
                select(AgentRun).where(
                    AgentRun.event_id == event_id, AgentRun.status.in_(("pending", "running"))
                )
            ):
                raise AppError(Code.BUSY)
            row = AgentRun(
                id=str(uuid4()),
                event_id=event_id,
                account_id=account_id,
                status="pending",
                progress=0,
                created_at=self.now(),
                started_at=None,
                finished_at=None,
                error_code=None,
                result=None,
                worker_owner=None,
                worker_fence=None,
            )
            session.add(row)
            session.flush()
            return RunOut.model_validate(row)

    def get(self, run_id):
        with self.db.session() as session:
            row = session.get(AgentRun, run_id)
            if not row:
                raise AppError(Code.NOT_FOUND)
            return RunOut.model_validate(row)

    def list(self, limit=50, offset=0):
        with self.db.session() as session:
            rows = session.scalars(
                select(AgentRun)
                .order_by(AgentRun.created_at.desc(), AgentRun.id)
                .limit(limit)
                .offset(offset)
            )
            return RunPage(
                items=[RunOut.model_validate(row) for row in rows],
                total=session.scalar(select(func.count()).select_from(AgentRun)),
                limit=limit,
                offset=offset,
            )
