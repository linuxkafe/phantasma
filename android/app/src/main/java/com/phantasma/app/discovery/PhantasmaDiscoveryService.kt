package com.phantasma.app.discovery

import android.content.Context
import android.net.nsd.NsdManager
import android.net.nsd.NsdServiceInfo
import android.util.Log
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.channels.receive
import kotlinx.coroutines.channels.send

class PhantasmaDiscoveryService(private val context: Context) {

    private val nsdManager = context.getSystemService(Context.NSD_SERVICE) as NsdManager
    private val discoveryListener = object : NsdManager.DiscoveryListener {
        override fun onDiscoveryStarted(serviceType: String) {
            Log.d("PhantasmaDiscovery", "Discovery started: $serviceType")
        }

        override fun onServiceFound(serviceInfo: NsdServiceInfo) {
            Log.d("PhantasmaDiscovery", "Service found: ${serviceInfo.serviceName}")
            if (serviceInfo.serviceType == SERVICE_TYPE) {
                nsdManager.resolveService(serviceInfo, resolveListener)
            }
        }

        override fun onServiceLost(serviceInfo: NsdServiceInfo) {
            Log.d("PhantasmaDiscovery", "Service lost: ${serviceInfo.serviceName}")
        }

        override fun onDiscoveryStopped(serviceType: String) {
            Log.d("PhantasmaDiscovery", "Discovery stopped: $serviceType")
        }

        override fun onStartDiscoveryFailed(serviceType: String, errorCode: Int) {
            Log.e("PhantasmaDiscovery", "Start discovery failed: $errorCode")
        }

        override fun onStopDiscoveryFailed(serviceType: String, errorCode: Int) {
            Log.e("PhantasmaDiscovery", "Stop discovery failed: $errorCode")
        }
    }

    private val resolveListener = object : NsdManager.ResolveListener {
        override fun onResolveFailed(serviceInfo: NsdServiceInfo, errorCode: Int) {
            Log.e("PhantasmaDiscovery", "Resolve failed: $errorCode")
        }

        override fun onServiceResolved(serviceInfo: NsdServiceInfo) {
            Log.d("PhantasmaDiscovery", "Service resolved: ${serviceInfo.host}:${serviceInfo.port}")
            val server = DiscoveredServer(
                name = serviceInfo.serviceName,
                host = serviceInfo.host.toString(),
                port = serviceInfo.port,
                serviceType = serviceInfo.serviceType
            )
            // Send to channel for UI consumption
            try {
                serverChannel.trySend(server)
            } catch (e: Exception) {
                Log.e("PhantasmaDiscovery", "Failed to send server", e)
            }
        }
    }

    private val serverChannel = Channel<DiscoveredServer>(capacity = 10)

    companion object {
        const val SERVICE_TYPE = "_phantasma._http._tcp."
    }

    fun startDiscovery() {
        nsdManager.discoverServices(SERVICE_TYPE, NsdManager.PROTOCOL_DNS_SD, discoveryListener)
    }

    fun stopDiscovery() {
        try {
            nsdManager.stopServiceDiscovery(discoveryListener)
        } catch (e: Exception) {
            Log.e("PhantasmaDiscovery", "Error stopping discovery", e)
        }
    }

    fun getServerChannel() = serverChannel
}