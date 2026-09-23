package com.phantasma.app.api

import com.phantasma.app.api.models.*
import io.ktor.client.*
import io.ktor.client.call.*
import io.ktor.client.engine.cio.*
import io.ktor.client.plugins.contentnegotiation.*
import io.ktor.client.plugins.logging.*
import io.ktor.client.request.*
import io.ktor.client.statement.*
import io.ktor.http.*
import io.ktor.serialization.kotlinx.json.*
import kotlinx.coroutines.*
import kotlinx.serialization.json.Json
import kotlinx.serialization.decodeFromString
import kotlinx.serialization.json.JsonObject
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody.Companion.toRequestBody
import java.util.concurrent.TimeUnit

class PhantasmaApiClient(
    private val baseUrl: String,
    private val timeoutSeconds: Long = 30
) {
    private val client = HttpClient(CIO) {
        install(JsonFeature) {
            serializer = KotlinxSerializer(Json {
                ignoreUnknownKeys = true
                isLenient = true
            })
        }
        install(Logging) {
            level = LogLevel.ALL
        }
        expectSuccess = false
        engine {
            connectTimeout = timeoutSeconds.seconds
            socketTimeout = timeoutSeconds.seconds
        }
    }

    suspend fun healthCheck(): HealthStatus? {
        return try {
            client.get("$baseUrl/health") {
                accept(ContentType.Application.Json)
            }.body()
        } catch (e: Exception) {
            null
        }
    }

    suspend fun serverInfo(): ServerInfo? {
        return try {
            client.get("$baseUrl/api/info") {
                accept(ContentType.Application.Json)
            }.body()
        } catch (e: Exception) {
            null
        }
    }

    suspend fun sendCommand(request: CommandRequest): CommandResponse? {
        return try {
            client.post("$baseUrl/api/command") {
                contentType(ContentType.Application.Json)
                accept(ContentType.Application.Json)
                setBody(request)
            }.body()
        } catch (e: Exception) {
            CommandResponse(
                success = false,
                error = e.message ?: "Unknown error"
            )
        }
    }

    suspend fun stt(request: STTRequest): STTResponse? {
        return try {
            client.post("$baseUrl/api/stt") {
                contentType(ContentType.Application.Json)
                accept(ContentType.Application.Json)
                setBody(request)
            }.body()
        } catch (e: Exception) {
            STTResponse(
                success = false,
                error = e.message ?: "Unknown error"
            )
        }
    }

    suspend fun tts(request: TTSRequest): TTSResponse? {
        return try {
            client.post("$baseUrl/api/tts") {
                contentType(ContentType.Application.Json)
                accept(ContentType.Application.Json)
                setBody(request)
            }.body()
        } catch (e: Exception) {
            TTSResponse(
                success = false,
                error = e.message ?: "Unknown error"
            )
        }
    }

    suspend fun listDevices(): List<DeviceInfo>? {
        return try {
            val response = client.get("$baseUrl/api/devices") {
                accept(ContentType.Application.Json)
            }
            val json = response.body<JsonObject>()
            json.get("devices")?.let {
                Json { ignoreUnknownKeys = true }.decodeFromJsonElement<List<DeviceInfo>>(it)
            }
        } catch (e: Exception) {
            null
        }
    }

    suspend fun controlDevice(name: String, request: DeviceControlRequest): DeviceControlResponse? {
        return try {
            client.post("$baseUrl/api/devices/$name/control") {
                contentType(ContentType.Application.Json)
                accept(ContentType.Application.Json)
                setBody(request)
            }.body()
        } catch (e: Exception) {
            DeviceControlResponse(
                success = false,
                deviceName = name,
                error = e.message ?: "Unknown error"
            )
        }
    }

    suspend fun listMemory(): List<MemoryEntry>? {
        return try {
            val response = client.get("$baseUrl/api/memory") {
                accept(ContentType.Application.Json)
            }
            json.decodeFromString<List<MemoryEntry>>(response.body())
        } catch (e: Exception) {
            null
        }
    }

    suspend fun addMemory(request: MemoryRequest): MemoryResponse? {
        return try {
            client.post("$baseUrl/api/memory") {
                contentType(ContentType.Application.Json)
                accept(ContentType.Application.Json)
                setBody(request)
            }.body()
        } catch (e: Exception) {
            MemoryResponse(
                success = false,
                error = e.message ?: "Unknown error"
            )
        }
    }

    fun close() {
        client.close()
    }
}