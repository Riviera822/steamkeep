package dev.steamvault.app.repo

import dev.steamvault.app.net.VaultApiClient
import dev.steamvault.app.net.model.ClientOut

/** Suspend-based repository over `GET /v1/clients` (WP 4b.2 brief). */
interface ClientsRepository {
    suspend fun list(): List<ClientOut>
}

class VaultClientsRepository(private val client: VaultApiClient) : ClientsRepository {
    override suspend fun list(): List<ClientOut> = client.clients()
}
