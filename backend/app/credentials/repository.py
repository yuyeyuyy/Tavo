from app.core.config import utcnow
from app.core.errors import AppError, Code
from app.models.entities import CredentialState


class CredentialVersions:
    def revision(self, session, reference):
        row = session.get(CredentialState, reference)
        return row.revision if row else 0

    def advance(self, session, reference, present, expected=None):
        row = session.get(CredentialState, reference)
        revision = row.revision if row else 0
        if expected is not None and expected != revision:
            raise AppError(Code.SECRET_REVISION_CONFLICT)
        if row:
            row.revision += 1
            row.present, row.updated_at = present, utcnow()
        else:
            row = CredentialState(
                reference=reference, revision=1, present=present, updated_at=utcnow()
            )
            session.add(row)
        return row.revision
