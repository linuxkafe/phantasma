package com.phantasma.app.discovery

import kotlinx.serialization.Serializable

@Serializable
data class DiscoveredServer(
    val name: String,
    val host: String,
    val port: Int,
    val serviceType: String,
    val baseUrl: String = "http://$host:$port"
) {
    override fun toString(): String = "$name ($baseUrl)"
}