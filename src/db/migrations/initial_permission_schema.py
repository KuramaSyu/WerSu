from authzed.api.v1 import (
    Client, CheckPermissionRequest, 
    CheckPermissionResponse, InsecureClient, 
    ObjectReference,
    SubjectReference
    
)
from grpcutil import bearer_token_credentials

client = InsecureClient(
    "localhost:50051",
    "my-secret-password"
)


