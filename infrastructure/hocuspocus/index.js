import { randomUUID } from 'node:crypto'
import { promises as fs } from 'node:fs'
import path from 'node:path'
import { Server } from '@hocuspocus/server'
import { v1 } from '@authzed/authzed-node'
import jwt from 'jsonwebtoken'
import * as Y from 'yjs'

// Hocuspocus unloads a document from memory when its last client disconnects.
// Without an onLoadDocument/onStoreDocument pair that state is lost, which is
// why a reload could reopen an already-populated room as empty. Keep one
// binary Yjs update file per document on a mounted volume instead.
const storageDir =
  process.env.HOCUSPOCUS_STORAGE_DIR || path.join(process.cwd(), 'storage')

function documentFile(documentName) {
  // Document names are "note-<uuid>", but escape them anyway so a hostile
  // client cannot escape the storage directory.
  return path.join(storageDir, `${encodeURIComponent(documentName)}.yjs`)
}

async function loadDocumentUpdate(documentName) {
  try {
    return await fs.readFile(documentFile(documentName))
  } catch (error) {
    if (error?.code === 'ENOENT') return null
    throw error
  }
}

async function storeDocumentUpdate(documentName, document) {
  await fs.mkdir(storageDir, { recursive: true })
  const target = documentFile(documentName)
  const temporary = `${target}.${process.pid}.${randomUUID()}.tmp`
  // Write then rename so an interrupted store never leaves a truncated file.
  await fs.writeFile(temporary, Y.encodeStateAsUpdate(document))
  await fs.rename(temporary, target)
}

const client = v1.NewClient(
  process.env.SPICEDB_CREDENTIALS,
  process.env.SPICEDB_ADDRESS,
  v1.ClientSecurity.INSECURE_PLAINTEXT_CREDENTIALS
)
const server = new Server({
  port: 8666,

  async onLoadDocument({ documentName, document }) {
    const update = await loadDocumentUpdate(documentName)
    if (update) Y.applyUpdate(document, update)
  },

  async onStoreDocument({ documentName, document }) {
    await storeDocumentUpdate(documentName, document)
  },

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

// Flush the debounced onStoreDocument hook before the container exits.
for (const signal of ['SIGINT', 'SIGTERM']) {
  process.once(signal, async () => {
    try {
      await server.destroy()
    } finally {
      process.exit(0)
    }
  })
}
