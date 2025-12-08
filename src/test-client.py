import grpc
from grpc_mod import NoteService, GetNoteRequest, PostNoteRequest, NoteServiceStub, PostUserRequest, UserServiceStub, GetUserRequest, User
from grpc_mod.proto.note_pb2 import Note

def get_note(stub, note_id):
    request = GetNoteRequest(id=note_id)
    response = stub.GetNote(request)
    print("GetNote response:")
    print(response)
    return response

def get_user(stub, user_id, discord_id) -> User:
    request = GetUserRequest(id=user_id, discord_id=discord_id)
    response = stub.GetUser(request)
    print("GetUser response:")
    print(response)
    return response

def post_user(stub, discord_id: int, avatar_url: str):
    request = PostUserRequest(
        discord_id=discord_id,
        avatar_url=avatar_url,
    )

    response = stub.PostUser(request)
    print("PostUser response:")
    print(response)
    return response

def post_note(stub, title: str, content: str, author_id: int = 1):
    request = PostNoteRequest(
        title=title,
        content=content,
        author_id=author_id,
    )

    response = stub.PostNote(request)
    print("PostNote response:")
    print(response)
    return response

def main():
    # Connect to server
    channel = grpc.insecure_channel("localhost:50051")
    stub = NoteServiceStub(channel)

    # generate user
    print("Posting user...")
    try: 
        user_response = post_user(
            UserServiceStub(channel),
            discord_id=987654321,
            avatar_url="http://example.com/avatar.png"
        )
    except Exception as e:
        print(f"User posting failed: {e}")

    user = get_user(UserServiceStub(channel), user_id=None, discord_id=987654321)
    # Test: create/update note
    print(f"Posting note for user with id {user.id}...")
    resp: Note = post_note(
        stub,
        title="Hello World",
        content="This is a test note",
        author_id=user.id
    )
    print(f"Posted note, got: {resp}")
    
    # Test: fetch note
    print(f"\nFetching note ({resp.id})...")
    get_note(stub, note_id=resp.id)

    print("Posting note with empty content to perform a search")
    get_note(stub, )

if __name__ == "__main__":
    main()
