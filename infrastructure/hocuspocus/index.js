import { Server } from '@hocuspocus/server'
import { v1 } from '@authzed/authzed-node'
import jwt from 'jsonwebtoken'

const client = v1.NewClient(
  process.env.SPICEDB_CREDENTIALS,
  process.env.SPICEDB_ADDRESS,
  v1.ClientSecurity.INSECURE_PLAINTEXT_CREDENTIALS
)
const server = new Server({
  port: 8666,

  async onAuthenticate({token, documentName}) {
    var userId = null;
    try {
      const payload = jwt.verify(token, process.env.JWT_SECRET)

      console.log(`Authenticate user ${payload.sub}`)

      userId = payload.sub;
      
    } catch (error) {
      console.error('Authentication failed:', error.message)
      throw new Error('Not authenticated')
    }

    // now user is authenticated
    // authorize him and check if he as write permissions on the requested document
    // for that cut {note}-{id} into note and id. Just strip note-, since the ID contains -
    const noteId = documentName.replace('note-', '')

    // make request to gRPC spiceDB instance
    console.log(`Checking permissions for user ${userId} on note ${noteId}`)
    try {

      const response = await client.checkPermission(
        v1.CheckPermissionRequest.create({
          resource: v1.ObjectReference.create({
            objectType: 'note',
            objectId: noteId,
          }),
          permission: 'write',
          subject: v1.SubjectReference.create({
            object: v1.ObjectReference.create({
              objectType: 'user',
              objectId: userId,
            })
          })
        }),
        (err, response) => {
          if (err) {
            console.log('SpiceDB response:', err, response)
          }
        }
      )

      if (response.permissionship !== v1.CheckPermissionResponse_Permissionship.PERMISSIONSHIP_HAS_PERMISSION) {
        throw new Error('Not authorized to write this document')
      }
      
    } catch (err) {
      console.error('SpiceDB error:', err)
      console.error('SpiceDB stack:', err.stack)
      throw err
    }


    // user is authenticated and authorized, return the userId as context for the document
    return {
      userId: userId,
    }

  }
})

server.listen()