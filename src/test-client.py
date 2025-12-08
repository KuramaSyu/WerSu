import asyncio
import grpc

from grpc_mod.proto.note_pb2 import (
    GetNoteRequest,
    GetSearchNotesRequest,
    Note,
    PostNoteRequest,
)
from grpc_mod.proto.note_pb2_grpc import NoteServiceStub
from grpc_mod.proto.user_pb2 import GetUserRequest, PostUserRequest, User
from grpc_mod.proto.user_pb2_grpc import UserServiceStub


async def get_note(stub: NoteServiceStub, note_id: int):
    request = GetNoteRequest(id=note_id)
    try:
        response = await stub.GetNote(request)
        print("GetNote response:")
        print(response)
        return response
    except grpc.aio.AioRpcError as e:
        print(f"Error getting note: {e}")
        return None


async def search_note(stub: NoteServiceStub, query: str):
    request = GetSearchNotesRequest(
        query=query, search_type=GetSearchNotesRequest.SearchType.Context, limit=50
    )
    print(f"GetSearchNotes response: ")
    notes = []
    try:
        response_stream = stub.SearchNotes(request)
        async for note in response_stream:
            print(note)
            notes.append(note)
    except grpc.aio.AioRpcError as e:
        print(f"Error searching notes: {e}")
    return notes


async def get_user(stub: UserServiceStub, user_id: int | None, discord_id: int | None) -> User | None:
    request = GetUserRequest(id=user_id, discord_id=discord_id)
    try:
        response = await stub.GetUser(request)
        print("GetUser response:")
        print(response)
        return response
    except grpc.aio.AioRpcError as e:
        print(f"Error getting user: {e}")
        return None


async def post_user(stub: UserServiceStub, discord_id: int, avatar_url: str):
    request = PostUserRequest(
        discord_id=discord_id,
        avatar_url=avatar_url,
    )
    try:
        response = await stub.PostUser(request)
        print("PostUser response:")
        print(response)
        return response
    except grpc.aio.AioRpcError as e:
        print(f"Error posting user: {e}")
        return None


async def post_note(stub: NoteServiceStub, title: str, content: str, author_id: int = 1):
    request = PostNoteRequest(
        title=title,
        content=content,
        author_id=author_id,
    )
    try:
        response = await stub.PostNote(request)
        print(f"PostNote response: ")
        print(response)
        return response
    except grpc.aio.AioRpcError as e:
        print(f"Error posting note: {e}")
        return None


async def main():
    # Connect to server
    async with grpc.aio.insecure_channel("localhost:50051") as channel:
        note_stub = NoteServiceStub(channel)
        user_stub = UserServiceStub(channel)

        # generate user
        print("Posting user...")
        user_response = await post_user(
            user_stub,
            discord_id=987654321,
            avatar_url="http://example.com/avatar.png",
        )
        if not user_response:
            return

        user = await get_user(user_stub, user_id=None, discord_id=987654321)
        if not user:
            return

        # Test: create/update note
        print(f"Posting note for user with id {user.id}...")
        resp: Note | None = await post_note(
            note_stub, title="Hello World", content="This is a test note", author_id=user.id
        )
        if not resp:
            return
        
        print(f"Posted note, got: {resp}")

        # Test: fetch note
        print(f"\nFetching note ({resp.id})...")
        await search_note(note_stub, query="test")

        print("Posting note with empty content to perform a search")


async def run():
    try:
        await main()
    except Exception as e:
        print(f"An unexpected error occurred: {e}")


if __name__ == "__main__":
    asyncio.run(run())
