from click import Option

from src.api.permission_repo import PermissionRepoABC
from src.api.relationship import NoteRelationEnum, ObjectRef, Relationship, SubjectRef
from src.api.sharing import ShareAccessServiceABC, SharingRepoABC
from src.api.types import LoggingProvider
from src.api.undefined import unwrap_undefined
from src.api.user_context import UserContextABC
from src.db.entities.note.sharing import NoteShareEntity
from src.db.repos.note.note import UserContext
from src.domain.permission_chain import HasNoteViewPerm, PermissionCheckChain


class ShareAccessService(ShareAccessServiceABC):
    def __init__(self, sharing_repo: SharingRepoABC, permission_repo: PermissionRepoABC, logger: LoggingProvider) -> None:
        self._sharing_repo = sharing_repo
        self._permission_repo = permission_repo
        self._log = logger

    async def access_share(self, share_id: str, ctx: Option[UserContextABC]) -> NoteShareEntity:
        # get share from DB
        share = await self._sharing_repo.get_shares_by_id([share_id], ctx)
        if not share:
            raise ValueError(f"Share not found: {share_id}")
        share = share[0]

        # check if the share user has acces to the given note 
        # and at the same time check what permissions the share user has
        note_id = unwrap_undefined(share.note_id)
        permissions = await self._permission_repo.get_permissions(
            user=UserContext(user_id=unwrap_undefined(share.access_as)),
            resource=ObjectRef("note", note_id),
        )
        self._log.debug(f"Share access check for share {share_id} on note {note_id} with permissions {permissions}")
        
        if "reader" in permissions:
            share.permission = "read"
        elif "writer" in permissions:
            share.permission = "write"
        else:
            raise PermissionError("Share user does not have access to the note")
        return share


