from dataclasses import dataclass
from typing import *

from . import SubjectsComputationExpressionABC

@dataclass
class NamespaceRelationDefinition:
    """Defines a rule for a namespace, like auto-computing B when A is given"""
    name: str
    subjects_computation_expression: SubjectsComputationExpressionABC

@dataclass
class NamespaceDefinition:
    """
    A namespace like documents or directories with its relations which compute other namespaces
    """
    name: str
    relations: dict[str, NamespaceRelationDefinition]
