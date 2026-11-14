from app.api.schemas import EventPage
from app.repositories.workspace import event_out


class EventService:
    def __init__(self, db, repository):
        self.db, self.repository = db, repository

    def list(self, **filters):
        with self.db.session() as session:
            limit = filters.get("limit", 50)
            rows = self.repository.list(session, **filters)
            return EventPage(
                items=[event_out(row) for row in rows],
                next_cursor=rows[-1].sequence if len(rows) == limit else None,
            )

    def after(self, sequence, limit=50):
        return self.list(after=sequence, limit=limit).items
