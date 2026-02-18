from abc import ABC, abstractmethod
from enum import Enum
class ValidationError(Exception):
    ...

class SubjectsComputationExpression(ABC):
    """Abstract Base Class defining how Subjects are computed"""
    @abstractmethod
    def is_subjects_computation_expression():
        ...

    @abstractmethod
    def operation():
        ...

    @abstractmethod
    def validate():
        """
        Raises
        -------
        ValidationError: if validation fails
        """
        ...

    @abstractmethod
    def __str__(self) -> str:
        ...

class SubjectsComputationOp(Enum):
    """Possible operations a subject can use"""
    OP_UNION = "UNION"
    OP_INTERSECTION = "INTERSECTION"
    OP_EXCLUSION = "EXCLUSION"
    OP_COMPUTED_SUBJECTS = "COMPUTED_SUBJECTS"  # for same-object inheritance
    OP_DIRECT = "DIRECT"  # No operation but direct assignment