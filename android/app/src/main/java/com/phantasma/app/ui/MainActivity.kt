package com.phantasma.app.ui

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.viewModels
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.Surface
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.lifecycle.viewmodel.ViewModel
import androidx.lifecycle.viewmodel.ViewModelProvider
import androidx.navigation.NavController
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.rememberNavController
import com.phantasma.app.ui.navigation.Screen
import com.phantasma.app.ui.screens.DevicesScreen
import com.phantasma.app.ui.screens.HomeScreen
import com.phantasma.app.ui.screens.MemoryScreen
import com.phantasma.app.ui.screens.SettingsScreen
import com.phantasma.app.ui.theme.PhantasmaTheme
import dagger.hilt.android.AndroidEntryPoint
import kotlinx.coroutines.channels.Channel

@AndroidEntryPoint
class MainActivity : ComponentActivity() {
    private val mainViewModel: MainViewModel by viewModels()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            PhantasmaTheme {
                Surface(
                    modifier = androidx.compose.foundation.layout.Modifier.fillMaxSize(),
                    color = androidx.compose.material3.MaterialTheme.colorScheme.background
                ) {
                    MainNavHost()
                }
            }
        }
    }
}

@Composable
fun MainNavHost() {
    val navController = rememberNavController()
    val selectedScreen by remember { mutableStateOf(Screen.Home) }

    androidx.navigation.compose.NavHost(navController, startDestination = "home") {
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

    // Bottom navigation bar
    NavigationBar(modifier = androidx.compose.foundation.layout.Modifier
        .fillMaxWidth()
        .padding(bottom = 16.dp)
    ) {
        listOf(Screen.Home, Screen.Devices, Screen.Memory, Screen.Settings).forEach { screen ->
            NavigationBarItem(
                icon = { androidx.compose.material3.Icon(screen.icon, contentDescription = screen.label) },
                label = { androidx.compose.material3.Text(screen.label) },
                selected = selectedScreen == screen,
                onClick = { selectedScreen = screen },
                alwaysShowLabel = true
            )
        }
    }
}

class MainViewModel : ViewModel() {
    // TODO: Add server discovery, API client, repositories
}