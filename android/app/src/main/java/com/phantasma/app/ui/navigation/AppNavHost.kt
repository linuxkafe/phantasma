package com.phantasma.app.ui.navigation

import androidx.compose.runtime.Composable
import androidx.navigation.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.rememberNavController
import com.phantasma.app.ui.screens.DevicesScreen
import com.phantasma.app.ui.screens.HomeScreen
import com.phantasma.app.ui.screens.MemoryScreen
import com.phantasma.app.ui.screens.SettingsScreen

@Composable
fun AppNavHost() {
    val navController = rememberNavController()
    NavHost(navController, startDestination = "home") {
        composable("home") {
            HomeScreen(
                serverStatus = null,
                onVoiceClick = {},
                voiceState = com.phantasma.app.ui.components.VoiceState.IDLE,
                onRefresh = {}
            )
        }
        composable("devices") {
            DevicesScreen(
                devices = emptyList(),
                onToggle = {},
                onControl = { _, _ -> },
                onRefresh = {}
            )
        }
        composable("memory") {
            MemoryScreen(
                memories = emptyList(),
                onAddMemory = { _, _ -> },
                onDelete = {},
                onRefresh = {}
            )
        }
        composable("settings") {
            SettingsScreen(
                serverIp = "",
                serverPort = 5000,
                autoDiscover = true,
                themeMode = 2,
                onServerIpChange = {},
                onServerPortChange = {},
                onAutoDiscoverChange = {},
                onThemeModeChange = {},
                onTestConnection = {}
            )
        }
    }
}

sealed class Screen(val route: String, val label: String, val icon: androidx.compose.ui.graphics.vector.ImageVector) {
    object Home : Screen("home", "Início", Icons.Default.Home)
    object Devices : Screen("devices", "Dispositivos", Icons.Default.Home)
    object Memory : Screen("memory", "Memória", Icons.Default.Memory)
    object Settings : Screen("settings", "Configurações", Icons.Default.Settings)
}