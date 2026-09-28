package com.phantasma.app.api.models

import kotlinx.serialization.Serializable

@Serializable
data class CommandRequest(
    val text: String,
    val type: CommandType = CommandType.TEXT,
    val language: String? = null,
    val deviceId: String? = null
)

@Serializable
enum class CommandType(val value: String) {
    VOICE("voice"),
    TEXT("text"),
    DEVICE_CONTROL("device_control"),
    MEMORY("memory")
}

@Serializable
data class CommandResponse(
    val success: Boolean,
    val text: String? = null,
    val audioBase64: String? = null,
    val audioFormat: String? = null,
    val deviceStates: Map<String, Any>? = null,
    val memoryUpdated: Boolean? = null,
    val error: String? = null,
    val processingTimeMs: Double = 0.0
)

@Serializable
data class DeviceInfo(
    val name: String,
    val type: String,
    val room: String? = null,
    val state: Map<String, Any>? = null,
    val online: Boolean = true
)

@Serializable
data class DeviceControlRequest(
    val deviceName: String,
    val action: String,
    val parameters: Map<String, Any>? = null
)

@Serializable
data class DeviceControlResponse(
    val success: Boolean,
    val deviceName: String,
    val newState: Map<String, Any>? = null,
    val error: String? = null
)

@Serializable
data class MemoryEntry(
    val id: Int? = null,
    val key: String,
    val value: String,
    val createdAt: String,
    val updatedAt: String? = null
)

@Serializable
data class MemoryRequest(
    val key: String,
    val value: String
)

@Serializable
data class MemoryResponse(
    val success: Boolean,
    val entry: MemoryEntry? = null,
    val entries: List<MemoryEntry>? = null,
    val error: String? = null
)

@Serializable
data class HealthStatus(
    val status: String,
    val version: String,
    val uptimeSeconds: Double,
    val components: Map<String, String>,
    val timestamp: String
)

@Serializable
data class ServerInfo(
    val name: String = "pHantasma",
    val version: String,
    val apiVersion: String = "v1",
    val endpoints: List<String>,
    val capabilities: List<String>
)

@Serializable
data class STTRequest(
    val audioBase64: String,
    val language: String? = null,
    val format: String = "wav"
)

@Serializable
data class STTResponse(
    val success: Boolean,
    val text: String? = null,
    val language: String? = null,
    val durationMs: Double = 0.0,
    val error: String? = null
)

@Serializable
data class TTSRequest(
    val text: String,
    val language: String = "pt",
    val format: String = "wav"
)

@Serializable
data class TTSResponse(
    val success: Boolean,
    val audioBase64: String? = null,
    val format: String = "wav",
    val durationMs: Double = 0.0,
    val error: String? = null
)