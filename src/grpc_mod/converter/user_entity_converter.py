from src.grpc_mod import User
from src.db.entities.user.user import UserEntity

def to_grpc_user(user_entity: UserEntity) -> User:
    """Converts a UserEntity to a gRPC User message."""
    assert user_entity.id is not None
    assert user_entity.discord_id is not None
    assert user_entity.avatar is not None
    assert user_entity.username is not None
    assert user_entity.email is not None

    return User(
        id=user_entity.id,
        discord_id=user_entity.discord_id,
        avatar=user_entity.avatar,
        username=user_entity.username,
        discriminator=user_entity.discriminator or "",
        email=user_entity.email
    )




