from abc import ABC, abstractmethod
from enum import Enum
from typing import Type

class SubjectsComputationOp(Enum):
    """Possible operations a subject can use"""
    OP_UNION = "UNION"
    OP_INTERSECTION = "INTERSECTION"
    OP_EXCLUSION = "EXCLUSION"
    OP_COMPUTED_SUBJECTS = "COMPUTED_SUBJECTS"  # for same-object inheritance
    OP_DIRECT = "DIRECT"  # No operation but direct assignment


class ValidationError(Exception):
    ...


class SubjectsComputationExpressionABC(ABC):
    """Abstract Base Class defining how Subjects are computed"""
    # @abstractmethod
    # def is_subjects_computation_expression():
    #     ...

    @abstractmethod
    def operation(self) -> SubjectsComputationOp:
        ...

    # @abstractmethod
    # def validate():
    #     """
    #     Raises
    #     -------
    #     ValidationError: if validation fails
    #     """
    #     ...

    # @abstractmethod
    # def __str__(self) -> str:
    #     ...



class DirectExpression(SubjectsComputationExpressionABC):
    def operation(self) -> SubjectsComputationOp:
        return SubjectsComputationOp.OP_DIRECT


class ComputedSubjectsExpression(SubjectsComputationExpressionABC):
    """Computed relations: include subjects from another relation (same-object inheritance)"""
    computed_relation: str  # "owner" (on the same object)
    
    def operation(self) -> SubjectsComputationOp:
        return SubjectsComputationOp.OP_COMPUTED_SUBJECTS
    

class UnionExpression(SubjectsComputationExpressionABC):
    """Boolean OR logic (either set A or set B -> A ∩ B )"""
    def operation(self) -> SubjectsComputationOp:
        return SubjectsComputationOp.OP_UNION


class IntersectionExpression(SubjectsComputationExpressionABC):
    """Boolean AND logic (in both sets A and B -> A ∪ B)"""
    def operation(self) -> SubjectsComputationOp:
        return SubjectsComputationOp.OP_INTERSECTION


class ExclusionExpression(SubjectsComputationExpressionABC):
    """Set substraction A - B (same as A\B)"""
    def operation(self) -> SubjectsComputationOp:
        return SubjectsComputationOp.OP_EXCLUSION

