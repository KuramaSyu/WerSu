"""Patch the AttachmentFacadeImpl to add polymorphic link/unlink/list methods."""
from pathlib import Path

path = Path(r"c:/Users/paulz/Documents/GitHub/i-will-find-it/src/services/attachment_facade.py")
data = path.read_bytes()

OLD = b'''    async def link_attachment_to_note(
        self,
        attachment_key: str,
        note_id: str,
        user_ctx: UserContextABC,
    ) -> None:
        permission_chain = (
            HasAttachmentViewPerm(attachment_key)
            .set_permission_repo(self._permission_repo)
            .set_next(HasNoteViewPerm(note_id))
        )
        has_permission = await permission_chain.get_first().check(user_ctx)
        if not has_permission:
            raise has_permission.error

        await self._attachments_note_link_table.insert(
            {
                "note_id": note_id,
                "attachment_key": attachment_key,
                "linked_at": self.get_now(),
            }
        )

        await self._permission_repo.insert(
            [
                Relationship(
                    ObjectRef(ObjectTypeEnum.ATTACHMENT, attachment_key),
                    AttachmentRelationEnum.PARENT_NOTE,
                    SubjectRef(ObjectTypeEnum.NOTE, note_id),
                )
            ]
        )

    async def unlink_attachment_from_note(
        self,
        attachment_key: str,
        note_id: str,
        user_ctx: UserContextABC,
    ) -> None:
        permission_chain = (
            HasAttachmentViewPerm(attachment_key)
            .set_permission_repo(self._permission_repo)
            .set_next(HasNoteViewPerm(note_id))
        )
        has_permission = await permission_chain.get_first().check(user_ctx)
        if not has_permission:
            raise has_permission.error

        await self._attachments_note_link_table.delete(
            {"note_id": note_id, "attachment_key": attachment_key}
        )

        await self._permission_repo.delete(
            Relationship(
                ObjectRef(ObjectTypeEnum.ATTACHMENT, attachment_key),
                AttachmentRelationEnum.PARENT_NOTE,
                SubjectRef(ObjectTypeEnum.NOTE, note_id),
            )
        )

    async def list_attachments_for_note(
        self,
        note_id: str,
        user_ctx: UserContextABC,
    ) -> list[Attachment]:
        check = HasNoteViewPerm(note_id).set_permission_repo(self._permission_repo)
        has_permission = await check.check(user_ctx)
        if not has_permission:
            raise has_permission.error

        links = await self._attachments_note_link_table.select(where={"note_id": note_id})\r
        attachments: list[Attachment] = []
        if not links:
            return []
        for link in links:
            attachment = await self.get_attachment(
                link["attachment_key"], user_ctx=user_ctx
            )
            attachments.append(attachment)
        return attachments'''

NEW = b'''    async def link_attachment(
        self,
        attachment_key: str,
        sub_type: LinkTargetType,
        sub_id: str,
        user_ctx: UserContextABC,
    ) -> None:
        await self._check_link_permission(attachment_key, sub_type, sub_id, user_ctx)

        if sub_type == "note":
            await self._attachments_note_link_table.insert(
                {
                    "note_id": sub_id,
                    "attachment_key": attachment_key,
                    "linked_at": self.get_now(),
                }
            )
            await self._permission_repo.insert(
                [
                    Relationship(
                        ObjectRef(ObjectTypeEnum.ATTACHMENT, attachment_key),
                        AttachmentRelationEnum.PARENT_NOTE,
                        SubjectRef(ObjectTypeEnum.NOTE, sub_id),
                    )
                ]
            )
        elif sub_type == "user":
            await self._attachments_user_link_table.insert(
                {
                    "user_id": sub_id,
                    "attachment_key": attachment_key,
                    "linked_at": self.get_now(),
                }
            )
            await self._permission_repo.insert(
                [
                    Relationship(
                        ObjectRef(ObjectTypeEnum.ATTACHMENT, attachment_key),
                        AttachmentRelationEnum.PARENT_USER,
                        SubjectRef(ObjectTypeEnum.USER, sub_id),
                    )
                ]
            )
        else:  # pragma: no cover - guarded by LinkTargetType Literal
            raise ValueError(f"unsupported attachment link sub_type: {sub_type!r}")

    async def unlink_attachment(
        self,
        attachment_key: str,
        sub_type: LinkTargetType,
        sub_id: str,
        user_ctx: UserContextABC,
    ) -> None:
        await self._check_link_permission(attachment_key, sub_type, sub_id, user_ctx)

        if sub_type == "note":
            await self._attachments_note_link_table.delete(
                {"note_id": sub_id, "attachment_key": attachment_key}
            )
            await self._permission_repo.delete(
                Relationship(
                    ObjectRef(ObjectTypeEnum.ATTACHMENT, attachment_key),
                    AttachmentRelationEnum.PARENT_NOTE,
                    SubjectRef(ObjectTypeEnum.NOTE, sub_id),
                )
            )
        elif sub_type == "user":
            await self._attachments_user_link_table.delete(
                {"user_id": sub_id, "attachment_key": attachment_key}
            )
            await self._permission_repo.delete(
                Relationship(
                    ObjectRef(ObjectTypeEnum.ATTACHMENT, attachment_key),
                    AttachmentRelationEnum.PARENT_USER,
                    SubjectRef(ObjectTypeEnum.USER, sub_id),
                )
            )
        else:  # pragma: no cover - guarded by LinkTargetType Literal
            raise ValueError(f"unsupported attachment link sub_type: {sub_type!r}")

    async def list_attachments(
        self,
        sub_type: LinkTargetType,
        sub_id: str,
        user_ctx: UserContextABC,
    ) -> list[Attachment]:
        if sub_type == "note":
            check = HasNoteViewPerm(sub_id).set_permission_repo(self._permission_repo)
            has_permission = await check.check(user_ctx)
            if not has_permission:
                raise has_permission.error
            links = await self._attachments_note_link_table.select(
                where={"note_id": sub_id}
            )\r
        elif sub_type == "user":
            if sub_id != user_ctx.user_id:
                raise PermissionError(
                    f"user {user_ctx.user_id} cannot list attachments of user {sub_id}"
                )
            links = await self._attachments_user_link_table.select(
                where={"user_id": sub_id}
            )
        else:  # pragma: no cover - guarded by LinkTargetType Literal
            raise ValueError(f"unsupported attachment link sub_type: {sub_type!r}")

        attachments: list[Attachment] = []
        if not links:
            return []
        for link in links:
            attachment = await self.get_attachment(
                link["attachment_key"], user_ctx=user_ctx
            )
            attachments.append(attachment)
        return attachments

    async def _check_link_permission(
        self,
        attachment_key: str,
        sub_type: LinkTargetType,
        sub_id: str,
        user_ctx: UserContextABC,
    ) -> None:
        """Common permission gate for link / unlink calls.

        For ``sub_type == "note"`` the actor must be able to view the
        attachment AND view the target note (matches the legacy
        :meth:`link_attachment_to_note` chain).

        For ``sub_type == "user"`` the actor must own the target user
        id -- we do not let one user link attachments to another.
        """
        if sub_type == "note":
            permission_chain = (
                HasAttachmentViewPerm(attachment_key)
                .set_permission_repo(self._permission_repo)
                .set_next(HasNoteViewPerm(sub_id))
            )
            has_permission = await permission_chain.get_first().check(user_ctx)
            if not has_permission:
                raise has_permission.error
        elif sub_type == "user":
            if sub_id != user_ctx.user_id:
                raise PermissionError(
                    f"user {user_ctx.user_id} cannot link attachments to user {sub_id}"
                )
        else:  # pragma: no cover - guarded by LinkTargetType Literal
            raise ValueError(f"unsupported attachment link sub_type: {sub_type!r}")'''

if OLD in data:
    new_data = data.replace(OLD, NEW, 1)
    path.write_bytes(new_data)
    print("OK -- impl methods replaced, len:", len(data), "->", len(new_data))
else:
    print("NOT FOUND")
    idx = data.find(b"link_attachment_to_note")
    print("signature at:", idx)
    print(repr(data[idx : idx + 300]))
