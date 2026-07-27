"""连接级家庭短期 Dialogue 存储。"""

from threading import Lock
from typing import Callable, Dict, Generic, Optional, TypeVar

from .models import (
    IdentityStatus,
    TurnIdentityContext,
    build_memory_user_id,
)


DialogueT = TypeVar("DialogueT")


class FamilySessionDialogueStore(Generic[DialogueT]):
    """在单个连接内隔离个人与匿名短期 Dialogue。"""

    def __init__(self, dialogue_factory: Callable[[], DialogueT]) -> None:
        if not callable(dialogue_factory):
            raise TypeError("dialogue_factory 必须可调用")
        self._dialogue_factory = dialogue_factory
        self._personal_dialogues: Dict[str, DialogueT] = {}
        self._anonymous_dialogue: Optional[DialogueT] = None
        self._lock = Lock()

    def get_dialogue(
        self,
        turn_identity_context: Optional[TurnIdentityContext],
    ) -> DialogueT:
        """按本轮身份选择个人 Dialogue，否则选择连接级匿名 Dialogue。"""

        memory_user_id = self._validated_memory_user_id(
            turn_identity_context
        )
        with self._lock:
            if memory_user_id is None:
                if self._anonymous_dialogue is None:
                    self._anonymous_dialogue = self._create_dialogue()
                return self._anonymous_dialogue

            dialogue = self._personal_dialogues.get(memory_user_id)
            if dialogue is None:
                dialogue = self._create_dialogue()
                self._personal_dialogues[memory_user_id] = dialogue
            return dialogue

    def clear(self) -> None:
        """释放当前连接持有的全部个人与匿名 Dialogue 引用。"""

        with self._lock:
            self._personal_dialogues.clear()
            self._anonymous_dialogue = None

    def _create_dialogue(self) -> DialogueT:
        dialogue = self._dialogue_factory()
        if dialogue is None:
            raise TypeError("dialogue_factory 不得返回 None")
        return dialogue

    @staticmethod
    def _validated_memory_user_id(
        turn_identity_context: Optional[TurnIdentityContext],
    ) -> Optional[str]:
        if turn_identity_context is None:
            return None

        decision = getattr(
            turn_identity_context,
            "identity_decision",
            None,
        )
        if (
            decision is None
            or getattr(decision, "identity_status", None)
            is not IdentityStatus.RECOGNIZED
        ):
            return None

        memory_user_id = getattr(decision, "memory_user_id", None)
        family_id = getattr(decision, "family_id", None)
        person_id = getattr(decision, "person_id", None)
        if (
            not isinstance(memory_user_id, str)
            or not memory_user_id.strip()
        ):
            return None

        try:
            expected_memory_user_id = build_memory_user_id(
                family_id,
                person_id,
            )
        except (TypeError, ValueError):
            return None

        if memory_user_id != expected_memory_user_id:
            return None
        return memory_user_id
