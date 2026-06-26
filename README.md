# Wersu
### Project Structure
![img](wersu-structure.drawio.png)

# Todo
- better logging, more di
- don't regenerate embedding generator too often 

# Development Docs
### Logging configuration
Logging levels can be configured with a YAML file. By default the app looks for `logging.yaml` in the workspace root, or you can override the path with `LOGGING_CONFIG_PATH`.

Example:
```yaml
level: INFO
loggers:
    __main__: DEBUG
    src: DEBUG
    src.db.database: WARNING
```

Logger names use the most specific match, so `src.db.database` wins over `src`.

### Compile Protobufs (`.proto` files):
1. install requirements:
    ```bash
    pip install -r requirements.txt
    ```
2. [install protobuf compiler on the system](https://github.com/protocolbuffers/protobuf#protobuf-compiler-installation)
3. compile the `src/grpc_mod/note.proto` and `src/grpc_mod/user.proto`file:
    ```bash
    python -m grpc_tools.protoc \
        -I . \
        --python_out=. \
        --grpc_python_out=. \
        --mypy_out=. \
        src/grpc_mod/proto/*.proto
    ```

### Start gRPC server
```bash
docker compose down; rm -r data; docker compose up --build -d; env PYTHONTRACEMALLOC=1 python -m src.main
```

### Pytest and setup
1. Create and activate a virtual environment:
    ```bash
    uv sync
    ```

- Run the default test suite (integration tests are excluded by default):
    ```bash
    pytest tests/
    ```

- Run SpiceDB integration tests (requires Docker running, started automatically via testcontainers):
    ```bash
    pytest -m "integration and spicedb" tests/
    ```

- Optional: run everything (including integration):
    ```bash
    pytest -o addopts='' tests/
    ```

- Optional: run proof of concepts (e.g. Garage test):
    ```bash
    pytest -m "proof_of_concept" tests/
    ```

# SpiceDB and Zanzibar
Zanzibar is a way to store relations between objects. One implementation for it is SpiceDB. Let's say you want to 
say that a user has admin rights for a Note, because it's the creator, then you would typically create an entity
for the note, persist it so that it gets an ID. Now you could store a `has_admin` in the note relation where the 
note is also stored. But this has a view limitations. Let's first take a look on how to do it right:
Create a Zanzibar Relation in the following format:
`resource:resource_id#relation@object:object_id`
in the case of giving a user admin-rights for a file it would look like this:
`note:note_id#admin@user:user_id` (read as user `user_id` has admin permission for note `note_id`).
Now you can not only store permissions, but also create relations like storing what directory a note belongs to:
`directory:directory_id#parent@note:note_id`