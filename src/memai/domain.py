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
    def __init__(self, start_id: datetime, speaker: User | Assistant, content: str)  -> None:
        # micro-second timestamp acting like unique id 
        self.start_id = start_id
        self.speaker = speaker
        self.content = content


class ConversationalMemory():
    """
    Holds memory during a conversation between user and assistant. 
    It is designed to be a short-term memory (STM) and to help:
        - feeding the LLM's context window. 
        - consolidating what will be stored in long-term memory (LTM).
    
    More recent elements are preserved as turns (prioritized), whereas 
    older elements decay over time (kept as summary or eventually just subjects).
    """
    max_elapse = timedelta(hours=12)
    max_size = 10

    def __init__(self, first_turn: Turn) -> None:
        self.start_id = first_turn.start_id
        self.turns: list[Turn] = [first_turn]
        self.subjects: set[str] = set()

    def add_turn(self, turn: Turn) -> bool:
        if turn.start_id - self.turns[-1].start_id < self.max_elapse:
            self.turns.append(turn)
            return True
        else:
            return False
    
    def list_subjects(self, subjects_extractor: Callable) -> set[str]:
        all_content = " ".join([turn.content for turn in self.turns])
        self.subjects = subjects_extractor(all_content)
        return self.subjects

    @property
    def end(self) -> datetime:
        return self.turns[-1].start_id

    def duration(self) -> timedelta:
        return self.end - self.start_id



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
class Event():
    pass

class MemoryConsolidation(Event):
    # Event triggered during an ongoing dialog when ConversationalMemory
    #  is near max_size, allowing the service layer to react:
    #   - persist older convertation turns in DB (to be preserved)
    #   - replace older convertation turns by summary (decayed)
    #   
    pass

class MemoryFlushing(Event):
    # Event triggered when a new conversation (session) is activated (by the user or the assistant) 
    # and the current ConversationalMemory having previous content must be flushed
    pass




# Domain Services
# stuff that doesn't fit in entities or value objects but is still part of the domain logic.



# Service Layer (Application Services)
# stuff that orchestrates the use of domain entities and services to achieve a specific use case.
# used by the outside world (e.g. API layer) to interact with the domain.


## outside world --> domain

def feed_conversational_memory():
    # to be called by pipecat when a new turn is added to the conversation:
    # it will feed the ConversationalMemory
    pass


## domain --> outside world

def consolidate_memory():
    # upon the MemoryConsolidation event 
    # it will and allow the service layer to store to LTM (MemoriesDB).
    pass


## Pipecat will log all conversation in file, and a batch process can persist these logs as archive


# lots of consilidation stuff should happen during downtime so not to impact the user experience, 
# and also to allow the system to learn from the past conversations and improve over time.