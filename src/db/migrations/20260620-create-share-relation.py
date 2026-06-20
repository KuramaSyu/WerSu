from pathlib import Path

from authzed.api.v1 import WriteSchemaRequest

from src.db.entities.user.user import UserEntity
from src.db.migrations.base import MigrationABC
from src.db.migrations.context import MigrationContext


class Migration(MigrationABC):
    """Create relation_shared table for the ability to create share links for a note. 
    With it, alter the users table, to make email, discord_id and avatar_url nullable since they are 
    not required for a temporary user. A temporary user will be created for each share which are link-only shares.
    Since a share can be limited in time, we also add a user_action table to schedule the disabling and deletion of
    these temporary users.
    """

    async def up(self, ctx: MigrationContext) -> None:
        """Create the attachment metadata table used by the facade."""
        await ctx.db.execute(
            """
            CREATE TABLE IF NOT EXISTS shared (
                id TEXT PRIMARY KEY DEFAULT uuidv7()::text,
                description TEXT NULL,
                note_id TEXT NOT NULL REFERENCES note.content(id) ON DELETE CASCADE ON UPDATE CASCADE,

                created_at TIMESTAMP NOT NULL,
                created_by TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE ON UPDATE CASCADE,
                online_since TIMESTAMP NULL,
                online_until TIMESTAMP NULL,

                -- this user will be used to access a note. It does not depend on the frontend login user,
                -- but is just for the backend. When creating a share, a user is created with it, with
                -- the right permissions.
                access_as TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE ON UPDATE CASCADE  
            );

            -- add new type for users
            CREATE TYPE user_kind AS ENUM ('human', 'temporary', 'system');
            -- Make existing columns nullable for temp user
            ALTER TABLE users
                ALTER COLUMN email DROP NOT NULL,
                ALTER COLUMN discord_id DROP NOT NULL,
                ALTER COLUMN avatar_url DROP NOT NULL;
                ADD COLUMN type user_kind NOT NULL DEFAULT 'human';
            
            -- user action types
            CREATE TYPE user_action_type AS ENUM ('disable', 'delete', 'enable');

            -- scheduled user actions to disable and delete temp users for shares
            CREATE TABLE IF NOT EXISTS user_action (
                id TEXT PRIMARY KEY DEFAULT uuidv7()::text,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE ON UPDATE CASCADE,
                action user_action_type NOT NULL,
                execute_at TIMESTAMP NOT NULL,
                executed_at TIMESTAMP NULL;
            );

            -- system will often lookup unexecuted user actions
            CREATE INDEX IF NOT EXISTS idx_user_actions_pending
                ON user_action (execute_at)
                WHERE executed_at IS NULL;
            """
        )

        # create public user
        user = UserEntity(
            username="public_user",
            user_kind="system"
        )

        await ctx.db.user.create(user)
