from datetime import datetime, timedelta


from dataclasses import dataclass
from typing import Callable


# Domain Entities  
class Assistant():
    def __init__(self, assistant_id, name, llm):
        self.assistant_id = assistant_id
        self.name = name
        self.llm = llm

class User():
    def __init__(self, pseudonym):
        self.pseudonym = pseudonym


class Turn():
    def __init__(self, speaker: User | Assistant, dialog: str, start: datetime)  -> None:
        self.speaker = speaker
        self.dialog = dialog
        self.start = start


class Conversation():
    """
    Conversation as Aggregation of turns within a certain time frame.
    Aggregation is a cluster of finer domain objects processed as a whole (ref. DDD).
    """
    max_elapse = timedelta(minutes=5)

    def __init__(self, conversation_id, turn: Turn) -> None:
        self.conversation_id = conversation_id
        self.turns: list[Turn] = [turn]
        self.subjects: set[str] = set()

    def add_turn(self, turn: Turn) -> bool:
        if turn.start - self.turns[-1].start < self.max_elapse:
            self.turns.append(turn)
            return True
        else:
            return False
    
    def list_subjects(self, subjects_extractor: Callable) -> set[str]:
        all_content = " ".join([turn.dialog for turn in self.turns])
        self.subjects = subjects_extractor(all_content)
        return self.subjects

    @property
    def start(self) -> datetime:
        return self.turns[0].start
    @property
    def end(self) -> datetime:
        return self.turns[-1].start

    def duration(self) -> timedelta:
        return self.end - self.start



# Domain Value Objects

@dataclass
class Chunk():
    """
    Chunk is value object (immutable) but different contents that 
    are semantically close enough are considered identical.
    This optimizes persistence storage and similar chunks are not stored multiple times. 
    """
    content: str
    # to change to numpy array in the future
    embedding: list[float]

    def compare_chunk(self, other_chunk, chunk_compare: Callable) -> bool:
        # compare two chunks and return True if they are similar enough,
        # this is a placeholder implementation
        return chunk_compare(self.embedding, other_chunk.embedding)

# Domain Events



# Domain Services

def ltm_consolidation(conversation: Conversation, consolidation_func: Callable) -> str:
    """
    Consolidate a conversation into a single string that can be stored in LTM.
    This is a domain service as it operates on multiple domain entities (Conversation and Chunk).
    """
    all_content = " ".join([turn.dialog for turn in conversation.turns])
    consolidated_content = consolidation_func(all_content)
    return consolidated_content


def stm_chunking(conversation: Conversation, chunking_func: Callable) -> list[Chunk]:
    """
    Chunk a conversation into smaller pieces that can be stored in STM.
    This is a domain service as it operates on multiple domain entities (Conversation and Chunk).
    """
    all_content = " ".join([turn.dialog for turn in conversation.turns])
    chunks = chunking_func(all_content)
    return chunks

