from src.db import UserEntity
from src.db import NamespaceDefinition

class PermissionService:
    def __init__(self, ruleset: dict[str, NamespaceDefinition]):
        self.ruleset = ruleset

    
    def check(self, user: UserEntity, resource: str) -> bool:
        """
        1. Parse inputs:
        - user = "user:alice"
        - resource = "document:1#restricted_viewer"

        2. Look up relation definition:
        - Find schema for "document" namespace
        - Get definition for "restricted_viewer" relation
        - If not found → DENY

        3. Evaluate relation using uniform semantics:
        FinalSubjects(R) = DIRECT(R) ∪ Eval(Expression(R))

        a) Check direct grants: (user:alice, document:1#restricted_viewer)
            If found → ALLOW

        b) Evaluate expression based on type:

        IF expression is DirectExpression:
            - No additional subjects (expression evaluates to ∅)

        IF expression is ComputedSubjectsExpression:
            - Recursively check: Check(user:alice, document:1#{ComputedRelation})

        IF expression is UnionExpression:
            - Evaluate each child expression
            - Return TRUE if ANY child returns TRUE

        IF expression is IntersectionExpression:
            - Evaluate each child expression
            - Return TRUE if ALL children return TRUE

        IF expression is ExclusionExpression:
            - Evaluate Left and Right expressions
            - Return TRUE if Left=TRUE AND Right=FALSE
        """
        ...

    def _check(self, user: UserEntity, resource: NamespaceDefinition, depth: int, max_depth: int) -> bool:
        ...