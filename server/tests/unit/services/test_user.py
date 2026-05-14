from uuid import uuid4

from memai_server.domain.model import Language, User
from memai_server.services.user import UpdatePrimaryLanguage

from tests.fakes.fakes import FakeUserRepository


def _user() -> User:
    return User(id=uuid4(), primary_language=Language("en"))


class TestUpdatePrimaryLanguage:
    def test_updates_user_primary_language(self):
        user = _user()
        repo = FakeUserRepository(user=user)
        event = UpdatePrimaryLanguage(repo).execute(user, Language("fr"))
        assert user.primary_language == Language("fr")

    def test_persists_updated_user(self):
        user = _user()
        repo = FakeUserRepository(user=user)
        UpdatePrimaryLanguage(repo).execute(user, Language("fr"))
        assert repo.get().primary_language == Language("fr")

    def test_returns_event_with_correct_languages(self):
        user = _user()
        event = UpdatePrimaryLanguage(FakeUserRepository(user=user)).execute(user, Language("fr"))
        assert event.old_language == Language("en")
        assert event.new_language == Language("fr")
        assert event.user_id == user.id

    def test_no_change_when_same_language(self):
        user = _user()
        event = UpdatePrimaryLanguage(FakeUserRepository(user=user)).execute(user, Language("en"))
        assert event.old_language == event.new_language
