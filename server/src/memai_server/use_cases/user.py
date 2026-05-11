from datetime import datetime

from ..domain.events import PrimaryLanguageChanged
from ..domain.model import Language, User
from .ports import UserRepository


class UpdatePrimaryLanguage:
    def __init__(self, user_repo: UserRepository) -> None:
        self._user_repo = user_repo

    def execute(self, user: User, new_language: Language) -> PrimaryLanguageChanged:
        old_language = user.primary_language
        user.update_primary_language(new_language)
        self._user_repo.save(user)
        return PrimaryLanguageChanged(
            user_id=user.id,
            old_language=old_language,
            new_language=new_language,
        )
