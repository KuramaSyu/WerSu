"""
This POC contains the idea I tried before I really started
this project, to evaluate, if it's even worth instead of 
just taking an existing solution.
"""

import asyncio
import asyncpg
from datetime import datetime
from typing import *
from pprint import pprint
from sentence_transformers import SentenceTransformer




# Insert into PostgreSQL postgresql://[user[:password]@][netloc][:port][/dbname][?param1=value1&...]
conn: asyncpg.Connection = None  # type: ignore
async def init():
    global conn
    conn = await asyncpg.connect("postgresql://postgres:postgres@localhost:5433/db")

    content: str
    # was valid until a long time ago, where the schema was in there.
    with open("init.sql") as f:
        content = f.read()

    await conn.execute(content)


notes = [
    ["sci-fi-test", "A suspenseful sci-fi adventure with a twist ending."],
    ["math", "Multiplicating 2 numbers means creating the sum of number one number two times."],

    # --- Added notes ---
    ["fantasy-quest", "A young hero discovers a magical ping amulet that can alter reality."],
    ["romance-cafe", "Two strangers fall in love after a chance meeting in a quiet coffee shop."],
    ["historical-war", "A soldier writes letters home during a devastating 18th-century war."],
    ["cooking-tips", "A guide to making perfect homemade pasta, from dough to sauce."],
    ["horror-haunted", "A family moves into an old house only to find it inhabited by ghosts."],
    ["space-exploration", "Astronauts uncover alien microbes on an icy moon of Jupiter."],
    ["ai-ethics", "Discussion about whether advanced AI systems should have rights."],
    ["mystery-detective", "A detective investigates a locked-room murder with no clear motive."],
    ["self-help-focus", "How daily habits can improve concentration and productivity."],
    ["gardening-guide", "Beginner advice on growing vegetables in small urban gardens."],
    ["philosophy-mind", "Exploring theories about consciousness and the nature of reality."],
    ["economics-basics", "An introduction to supply, demand, and market equilibrium."],
    ["fitness-workout", "A beginner full-body workout routine requiring no equipment."],
    ["travel-japan", "A traveler's experience exploring shrines and street food in Tokyo."],
    ["comedy-sketch", "A humorous tale about a cat who accidentally becomes mayor."],
    ["crime-thriller", "A journalist uncovers a conspiracy involving a corrupt corporation."],
    ["biology-evolution", "Explaining natural selection and how species adapt over time."],
    ["music-theory", "Understanding major scales, minor scales, and chord progressions."],
    ["poetry-nature", "A poetic reflection pings on the changing colors of autumn leaves."],

]

notes.extend([
    ["python-basics", "An introduction to Python variables, loops, and basic data types."],
    ["linux-permissions", "Explains file permissions in Linux and how to use chmod and chown."],
    ["docker-intro", "A beginner guide to building containers and running Docker images."],
    ["git-workflow", "How to use branching, merging, and pull requests in Git version control."],
    ["linux-networking", "Basics of using ifconfig, ping, and netstat to troubleshoot networks."],
    ["async-programming", "Overview of asynchronous programming concepts using async and await."],
    ["javascript-promises", "Explains how Promises work in JavaScript and how to chain them."],
    ["linux-shell-scripting", "How to create and run Bash shell scripts with variables and loops."]
])


model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")

def to_str_vec(l: List[Any]) -> str:
    return f"[{','.join(str(x) for x in l)}]"

for i, n in enumerate(notes):
    embedding = model.encode(n[1])
    notes[i].append(to_str_vec(embedding.tolist()))



test_search = "I hate artificial intelligence"
search_embedding = model.encode(test_search)
search_vec = to_str_vec(search_embedding.tolist())


async def insert_values():
    for n in notes:
        result = await conn.fetch(
            "INSERT INTO note.metadata (title, author_id) VALUES ($1, $2) RETURNING id",
            n[0], 1
        )
        note_id = result[0]['id']
        await conn.execute(
            "INSERT INTO note.page (note_id, created_at, content) VALUES ($1, $2, $3)",
            note_id, datetime.now(), n[1]
        )

        await conn.execute(
            "INSERT INTO note.embedding (note_id, embedding) VALUES ($1, $2)",
            note_id,
            n[2] 
        )

async def order_by_cosine_similarity():
    result = await conn.fetch(
        """SELECT title, (embedding <=> $1::vector) AS similarity
        FROM note.embedding
        JOIN note.metadata on note.metadata.id = note.embedding.note_id
        ORDER BY similarity ASC
        """,
        search_vec
    )
    pprint(result)


async def main():
    print(test_search)
    await init()
    # await insert_values()
    await order_by_cosine_similarity()
    await conn.close()

asyncio.run(main())