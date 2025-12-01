# i-will-find-it

# Todo
- UserRepo
- gRPC user service

# Development Docs
### Compile Protobufs (`.proto` files):
1. install requirements:
    ```bash
    pip install -r requirements.txt
    ```
2. [install protobuf compiler on the system](https://github.com/protocolbuffers/protobuf#protobuf-compiler-installation)
3. compile the `src/grpc_mod/note.proto` file:
    ```bash
    python -m grpc_tools.protoc   -I grpc_mod/proto   --python_out=grpc_mod/proto   --grpc_python_out=grpc_mod/proto   --mypy_out=grpc_mod/proto   note.proto
    ```
4. compile the `src/grpc_mod/user.proto` file:
    ```bash
    python -m grpc_tools.protoc   -I grpc_mod/proto   --python_out=grpc_mod/proto/   --grpc_python_out=grpc_mod/proto/   --mypy_out=gr
    pc_mod/proto/   user.proto
    ```

### Start gRPC server
```bash
cd src
env PYTHONTRACEMALLOC=1 python main.py
```