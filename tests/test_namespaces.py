from src.db.entities.role import *

def test_base():
    document_namespace = NamespaceDefinition(
        name="document",
        relations={
            "owner": NamespaceRelationDefinition(
                name="owner",
                subjects_computation_expression=DirectExpression()
            ),
            "collaborator": NamespaceRelationDefinition(
                name="collaborator",
                subjects_computation_expression=DirectExpression()
            ),
            "guest": NamespaceRelationDefinition(
                name="guest",
                subjects_computation_expression=DirectExpression()
            ),

            # computed using inheritance
            "editor": NamespaceRelationDefinition(
                name="editor",
                subjects_computation_expression=ComputedSubjectsExpression(
                    computed_relation="owner"  # owners are automatically editors
                )
            ),
            "viewer": NamespaceRelationDefinition(
                name="viewer",
                subjects_computation_expression=ComputedSubjectsExpression(
                    computed_relation="editor"  # editors are automatically viewers
                )
            ),

            # boolean combinations
            
            # public viewer = collaborator OR guest
            "public_viewer": NamespaceRelationDefinition(
                name="public_viewser",
                subjects_computation_expression=UnionExpression(
                    children=[
                        ComputedSubjectsExpression(computed_relation="collaborator"),
                        ComputedSubjectsExpression(computed_relation="guest")
                    ]
                )
            ),
            # external viewer = viewer AND NOT collaborator
            "external_viewer": NamespaceRelationDefinition(
                name="public_viewer",
                subjects_computation_expression=ExclusionExpression(
                    left=ComputedSubjectsExpression(computed_relation="viewer"),
                    right=ComputedSubjectsExpression(computed_relation="collaborator")
                )
            )
        }
    )

    # each expression is calculated like this: FinalSubjects(R) = DIRECT(R) ∪ Eval(Expression(R))
    # -> An editor Computed("owner") = DIRECT("editor") OR COMPUTED("owner")  -> editor can be assigned by itself
    # DIRECT expressions only contribute direct tuples in top level. When nested in UNION | INTERSECTION | EXCLUSION 
    # they result in {}, hence ComputedSubjectsExpressions are used there

