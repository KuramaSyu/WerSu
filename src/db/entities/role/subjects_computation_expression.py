from abc import ABC, abstractmethod

class ValidationError(Exception):
    ...

class SubjectsComputationExpression(ABC):
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