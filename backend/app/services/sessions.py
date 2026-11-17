class ConversationService:
    def __init__(self, repository, auth, models):
        self.repository, self.auth, self.models = repository, auth, models

    def create(self, value, token):
        if value.profile_id != "mock":
            self.models.profile(value.profile_id)
        return self.repository.create(value, lambda: self.auth.require(token))

    def send(self, session_id, value, token):
        return self.repository.send(session_id, value, lambda: self.auth.require(token))

    def cancel(self, session_id, run_id, token):
        return self.repository.cancel(session_id, run_id, lambda: self.auth.require(token))

    def retry(self, session_id, run_id, token):
        return self.repository.retry(session_id, run_id, lambda: self.auth.require(token))

    def decide(self, session_id, call_id, value, token):
        return self.repository.decide(session_id, call_id, value, lambda: self.auth.require(token))

    def get(self, session_id):
        return self.repository.get(session_id)

    def list(self, limit, before):
        return self.repository.list(limit, before)

    def messages(self, session_id, limit, after, before):
        return self.repository.messages(session_id, limit, after, before)

    def get_run(self, session_id, run_id):
        return self.repository.get_run(session_id, run_id)
