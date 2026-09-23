package com.phantasma.app.audio

import android.content.Context
import android.media.AudioFormat
import android.media.AudioTrack
import android.util.Base64
import android.util.Log
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.channels.trySend

class AudioPlayer(
    private val context: Context,
    sampleRate: Int = 22050,
    channelConfig: Int = AudioFormat.CHANNEL_OUT_MONO,
    audioFormat: Int = AudioFormat.ENCODING_PCM_16BIT
) {
    private val bufferSize = AudioTrack.getMinBufferSize(sampleRate, channelConfig, audioFormat)
    private var audioTrack: AudioTrack? = null
    private val audioChannel = Channel<ByteArray>(capacity = 50)

    init {
        audioTrack = AudioTrack.Builder()
            .setAudioAttributes(
                android.media.AudioAttributes.Builder()
                    .setUsage(android.media.AudioAttributes.USAGE_MEDIA)
                    .setContentType(android.media.AudioAttributes.CONTENT_TYPE_SPEECH)
                    .build()
            )
            .setAudioFormat(
                android.media.AudioFormat.Builder()
                    .setSampleRate(sampleRate)
                    .setChannelMask(channelConfig)
                    .setEncoding(audioFormat)
                    .build()
            )
            .setBufferSizeInBytes(bufferSize.coerceAtLeast(4096))
            .setTransferMode(AudioTrack.MODE_STREAM)
            .build()
    }

    fun playBase64Audio(base64Audio: String) {
        val decoded = Base64.decode(base64Audio, Base64.DEFAULT)
        playAudio(decoded)
    }

    fun playAudio(audioData: ByteArray) {
        if (audioTrack?.playState != AudioTrack.PLAYSTATE_PLAYING) {
            audioTrack?.play()
        }
        audioTrack?.write(audioData, 0, audioData.size)
    }

    fun playBase64AudioAsync(base64Audio: String) {
        val decoded = Base64.decode(base64Audio, Base64.DEFAULT)
        playAudioAsync(decoded)
    }

    fun playAudioAsync(audioData: ByteArray) {
        Thread(start = true) {
            try {
                audioChannel.trySend(audioData)
            } catch (e: Exception) {
                Log.e("AudioPlayer", "Channel send failed", e)
            }
        }.start()
    }

    private fun processQueue() {
        Thread(start = true) {
            for (data in audioChannel) {
                if (audioTrack?.playState != AudioTrack.PLAYSTATE_PLAYING) {
                    audioTrack?.play()
                }
                audioTrack?.write(data, 0, data.size)
            }
        }.start()
    }

    init {
        processQueue()
    }

    fun stop() {
        audioTrack?.stop()
        audioTrack?.release()
        audioTrack = null
        audioChannel.close()
    }

    fun pause() {
        audioTrack?.pause()
    }

    fun resume() {
        audioTrack?.play()
    }
}