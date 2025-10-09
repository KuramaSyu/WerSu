from dataclasses import dataclass

from numpy import strings
import psycopg
from concurrent import futures
import grpc
#import notes_pb2, notes_pb2_grpc
from sentence_transformers import SentenceTransformer

# Load model once
embedder = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")

def embed(text: str):
    return embedder.encode(text).tolist()

@dataclass
class NoteResponse:
    id: int
    request_content: str

@dataclass
class NoteRequest:
    content: str


class NotesService:
    def __init__(self):
        self.conn = psycopg.connect("dbname=notes user=postgres password=postgres host=localhost")

    def AddNote(self, request: NoteRequest, context) -> NoteResponse:
        vec = embed(request.content)
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO notes (content, embedding) VALUES (%s, %s) RETURNING id",
                (request.content, vec)
            )
            if not (note_ids := cur.fetchone()):
                raise RuntimeError(f"Nothing found for request {request.content}")

            note_id = note_ids[0]
            self.conn.commit()
        return NoteResponse(note_id, request.content)

    def SearchNotes(self, request, context):
        vec = embed(request.query)
        with self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, content,
                       1 - (embedding <=> %s::vector) AS score
                FROM notes
                ORDER BY embedding <=> %s::vector
                LIMIT %s
                """,
                (vec, vec, request.top_k or 5)
            )
        #     results = [notes_pb2.SearchResult(id=r[0], content=r[1], score=float(r[2])) for r in cur.fetchall()]
        # return notes_pb2.SearchResponse(results=results)

def serve():
    # server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    # notes_pb2_grpc.add_NotesServiceServicer_to_server(NotesService(), server)
    # server.add_insecure_port("[::]:50051")
    # server.start()
    # server.wait_for_termination()
    ...

if __name__ == "__main__":
    serve()
