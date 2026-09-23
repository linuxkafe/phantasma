package com.phantasma.app.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Dns
import androidx.compose.material.icons.filled.Palette
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material.icons.filled.Info
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Add
import androidx.compose.material3.Card
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextField
import androidx.compose.material3.FilterChip
import androidx.compose.material3.FilledButton
import androidx.compose.material3.IconButton
import androidx.compose.material3.Icon
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.OutlinedTextField
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.phantasma.app.ui.theme.PhantasmaTheme
import com.phantasma.app.ui.theme.Typography

@Composable
fun SettingsScreen(
    serverIp: String,
    serverPort: Int,
    autoDiscover: Boolean,
    themeMode: Int, // 0 = system, 1 = light, 2 = dark
    onServerIpChange: (String) -> Unit,
    onServerPortChange: (Int) -> Unit,
    onAutoDiscoverChange: (Boolean) -> Unit,
    onThemeModeChange: (Int) -> Unit,
    onTestConnection: () -> Unit,
    modifier: Modifier = Modifier
) {
    val context = LocalContext.current

    Column(
        modifier = modifier.fillMaxSize(),
        verticalArrangement = Arrangement.spacedBy(8.dp)
    ) {
        // Header
        Text(
            text = "Configurações",
            style = Typography.headlineMedium,
            color = PhantasmaTheme.colorScheme.onSurface,
            modifier = Modifier.padding(16.dp)
        )

        // Server section
        SectionCard(title = "Servidor", icon = Icons.Default.Dns) {
            Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                // Auto discover
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Column {
                        Text(text = "Descoberta automática", style = Typography.bodyLarge)
                        Text(
                            text = "Usar mDNS para encontrar o servidor",
                            style = Typography.bodySmall,
                            color = PhantasmaTheme.colorScheme.onSurfaceVariant
                        )
                    }
                    Switch(
                        checked = autoDiscover,
                        onCheckedChange = onAutoDiscoverChange,
                        colors = androidx.compose.material3.SwitchDefaults.colors(
                            checkedThumbColor = PhantasmaTheme.colorScheme.primary,
                            checkedTrackColor = PhantasmaTheme.colorScheme.primaryContainer
                        )
                    )
                }

                // Server IP
                androidx.compose.material3.OutlinedTextField(
                    value = serverIp,
                    onValueChange = onServerIpChange,
                    label = { Text("Endereço IP do servidor") },
                    placeholder = { Text("192.168.1.100") },
                    keyboardOptions = androidx.compose.ui.text.input.KeyboardOptions(
                        keyboardType = KeyboardType.Number
                    ),
                    modifier = Modifier.fillMaxWidth()
                )

                // Server Port
                androidx.compose.material3.OutlinedTextField(
                    value = serverPort.toString(),
                    onValueChange = { onServerPortChange(it.toIntOrNull() ?: 5000) },
                    label = { Text("Porta") },
                    placeholder = { Text("5000") },
                    keyboardOptions = androidx.compose.ui.text.input.KeyboardOptions(
                        keyboardType = KeyboardType.Number
                    ),
                    modifier = Modifier.fillMaxWidth()
                )

                // Test connection button
                androidx.compose.material3.FilledButton(
                    onClick = onTestConnection,
                    modifier = Modifier.fillMaxWidth().padding(top = 8.dp)
                ) {
                    Text(text = "Testar conexão")
                }
            }
        }

        // Appearance section
        SectionCard(title = "Aparência", icon = Icons.Default.Palette) {
            Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                Text(text = "Tema", style = Typography.bodyLarge)
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(8.dp)
                ) {
                    listOf(
                        0 to "Sistema",
                        1 to "Claro",
                        2 to "Escuro"
                    ).forEach { (value, label) ->
                        androidx.compose.material3.FilterChip(
                            selected = themeMode == value,
                            onClick = { onThemeModeChange(value) },
                            label = { Text(text = label) },
                            colors = androidx.compose.material3.FilterChipDefaults.colors(
                                selectedContainerColor = PhantasmaTheme.colorScheme.primaryContainer,
                                selectedContentColor = PhantasmaTheme.colorScheme.onPrimaryContainer
                            )
                        )
                    }
                }
            }
        }

        // Voice section
        SectionCard(title = "Voz", icon = Icons.Default.Mic) {
            Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                Text(text = "Idioma de reconhecimento", style = Typography.bodyLarge)
                Row(
                    horizontalArrangement = Arrangement.spacedBy(8.dp)
                ) {
                    listOf("pt" to "Português", "en" to "Inglês").forEach { (code, label) ->
                        androidx.compose.material3.FilterChip(
                            selected = false, // TODO: bind to state
                            onClick = {},
                            label = { Text(text = label) }
                        )
                    }
                }
            }
        }

        // About section
        SectionCard(title = "Sobre", icon = Icons.Default.Info) {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Row(
                    horizontalArrangement = Arrangement.SpaceBetween
                ) {
                    Text(text = "Versão", style = Typography.bodyLarge)
                    Text(text = "1.0.0", style = Typography.bodyLarge, color = PhantasmaTheme.colorScheme.onSurfaceVariant)
                }
                Row(
                    horizontalArrangement = Arrangement.SpaceBetween
                ) {
                    Text(text = "Servidor", style = Typography.bodyLarge)
                    Text(text = "pHantasma 0.1.0", style = Typography.bodyLarge, color = PhantasmaTheme.colorScheme.onSurfaceVariant)
                }
            }
        }
    }
}

@Composable
fun SectionCard(
    title: String,
    icon: androidx.compose.ui.graphics.vector.ImageVector,
    content: @Composable () -> Unit
) {
    Card(
        modifier = Modifier
            .fillMaxWidth()
            .padding(16.dp),
        colors = androidx.compose.material3.CardDefaults.cardColors(
            containerColor = PhantasmaTheme.colorScheme.surface,
            contentColor = PhantasmaTheme.colorScheme.onSurface
        ),
        shape = androidx.compose.foundation.shape.RoundedCornerShape(16.dp),
        elevation = androidx.compose.material3.CardDefaults.cardElevation(defaultElevation = 2.dp)
    ) {
        Column(
            modifier = Modifier.fillMaxWidth().padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp)
        ) {
            Row(
                verticalAlignment = Alignment.CenterVertically
            ) {
                Icon(
                    imageVector = icon,
                    contentDescription = title,
                    tint = PhantasmaTheme.colorScheme.primary,
                    modifier = Modifier.size(24.dp)
                )
                androidx.compose.foundation.layout.Spacer(modifier = androidx.compose.foundation.layout.padding(12.dp))
                Text(
                    text = title,
                    style = Typography.titleMedium,
                    color = PhantasmaTheme.colorScheme.onSurface
                )
            }
            content()
        }
    }
}

// Preview
@Preview
@Composable
fun SettingsScreenPreview() {
    PhantasmaTheme {
        SettingsScreen(
            serverIp = "192.168.1.100",
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