# Architecture
WerSu uses a microservice-architecture. The main component is the **WerSu-gRPC-Service**. It handles:
- note creation as well as the embedding creation
- search algorithms like vector search
- note updates, deletes and similar
- user operations
- creation and management of permissions via gRPC stored on SpiceDB

But a react app or more specific, a browser, doesn't support gRPC directly yet, hence the **REST Proxy** 
is the main access point for frontends like the react app. It's responsiblitities are:
- most important: translating incoming REST-Calls to gRPC-services, hence _proxying_ the request
- handeling the user authentication which is currently done by reading the credentials of the REST-Call and
  checking if the Discord token is in there. Otherwise an error is returned and the user needs to login via Discord
- uploading attachments to _Garage_, the S3 storage, and creating the permissions for that via gRPC on SpiceDB
- proxying image-preview requests to _imgproxy_, a microservice to generate image previews

Next there is the Image Proxy _imgproxy_ which just takes the S3 bucket key out of the request URL, fetches it and
generates an image preview for it. It supports image previews for images and PDFs. 

The _WerSu Frontend_ is the main user interface, which interacts with the _REST Proxy_ for currently all operations 
you can do on the website.

Lastly there are the storage solutions:
- **SpiceDB**: handles permissions. For that it implements Zanzibar, which is a way to represent them.
  Example permissions are:
  - has a user permission to view a note? Done with `note:42#view@user:alice`
  - has a user transitive permissions, like view on the parent directory of note with id 42, then it returns also true
  - has a user file permissions? Here it also checks transitive permissions of the note and directory
- **PostgreSQL**: Used from WerSu gRPC Service to store notes, users and other app related data
- **Garage**: Garage is the S3 Bucket, used to store note attachments as well as images.
  
![img](../wersu-structure.drawio.png)
