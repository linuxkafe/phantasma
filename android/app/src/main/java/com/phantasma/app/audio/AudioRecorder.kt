package com.phantasma.app.audio

import android.content.Context
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.util.Log
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.channels.trySend

class AudioRecorder(
    private val context: Context,
    sampleRate: Int = 16000,
    channelConfig: Int = AudioFormat.CHANNEL_IN_MONO,
    audioFormat: Int = AudioFormat.ENCODING_PCM_16BIT
) {
    private val bufferSize = AudioRecord.getMinBufferSize(sampleRate, channelConfig, audioFormat)
    private var audioRecord: AudioRecord? = null
    private var isRecording = false
    private val audioChannel = Channel<ByteArray>(capacity = 50)

    init {
        if (bufferSize == AudioRecord.ERROR || bufferSize == AudioRecord.ERROR_BAD_VALUE) {
            throw IllegalArgumentException("Invalid audio configuration")
        }
    }

    fun start(): Boolean {
        if (isRecording) return true

        audioRecord = AudioRecord(
            MediaRecorder.AudioSource.VOICE_RECOGNITION,
            sampleRate,
            channelConfig,
            audioFormat,
            bufferSize.coerceAtLeast(4096)
        )

        val state = audioRecord?.state ?: AudioRecord.STATE_UNINITIALIZED
        if (state != AudioRecord.STATE_INITIALIZED) {
            Log.e("AudioRecorder", "AudioRecord not initialized")
            return false
        }

        audioRecord?.startRecording()
        isRecording = true

        // Start recording loop
        Thread(start = true) {
            val buffer = ByteArray(bufferSize)
            while (isRecording) {
                val read = audioRecord?.read(buffer, 0, buffer.size) ?: 0
                if (read > 0) {
                    val data = buffer.copyOf(read)
                    try {
                        audioChannel.trySend(data)
                    } catch (e: Exception) {
                        Log.e("AudioRecorder", "Channel send failed", e)
                    }
                } else if (read < 0) {
                    Log.e("AudioRecorder", "Read error: $read")
                }
            }
        }.start()

        return true
    }

    fun stop() {
        isRecording = false
        audioRecord?.stop()
        audioRecord?.release()
        audioRecord = null
        audioChannel.close()
    }

    fun getAudioChannel() = audioChannel

    fun isRecording() = isRecording
}