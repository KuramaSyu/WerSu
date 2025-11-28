# i-will-find-it

# Development Docs
### Compile Protobufs (`.proto` files):
1. install requirements:
    ```bash
    pip install -r requirements.txt
    ```
2. [install protobuf compiler on the system](https://github.com/protocolbuffers/protobuf#protobuf-compiler-installation)
3. compile the `src/grpc/note.proto` file:
    ```bash
    cd src
    python -m grpc_tools.protoc -I grpc/proto --python_out grpc/generated --grpc_python_out=grpc/generated note.proto
    ``` 