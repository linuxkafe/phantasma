package com.phantasma.app.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Home
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material3.Card
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.phantasma.app.api.models.DeviceInfo
import com.phantasma.app.ui.components.DeviceCard
import com.phantasma.app.ui.theme.PhantasmaTheme
import com.phantasma.app.ui.theme.Typography

@Composable
fun DevicesScreen(
    devices: List<DeviceInfo>,
    onToggle: (DeviceInfo) -> Unit,
    onControl: (DeviceInfo, String) -> Unit,
    onRefresh: () -> Unit,
    modifier: Modifier = Modifier
) {
    Column(
        modifier = modifier.fillMaxSize(),
        verticalArrangement = Arrangement.spacedBy(8.dp)
    ) {
        // Header
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(16.dp),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically
        ) {
            Text(
                text = "Dispositivos",
                style = Typography.headlineMedium,
                color = PhantasmaTheme.colorScheme.onSurface
            )

            androidx.compose.material3.IconButton(
                onClick = onRefresh
            ) {
                Icon(
                    imageVector = Icons.Default.Refresh,
                    contentDescription = "Atualizar",
                    tint = PhantasmaTheme.colorScheme.onSurfaceVariant
                )
            }
        }

        // Device list
        if (devices.isEmpty()) {
            Box(
                modifier = Modifier
                    .fillMaxSize()
                    .padding(16.dp),
                contentAlignment = Alignment.Center
            ) {
                Column(
                    horizontalAlignment = Alignment.CenterHorizontally
                ) {
                    Icon(
                        imageVector = Icons.Default.Home,
                        contentDescription = "Sem dispositivos",
                        modifier = Modifier.size(64.dp),
                        tint = PhantasmaTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.5f)
                    )
                    androidx.compose.foundation.layout.Spacer(modifier = androidx.compose.foundation.layout.padding(16.dp))
                    Text(
                        text = "Nenhum dispositivo configurado",
                        style = Typography.titleMedium,
                        color = PhantasmaTheme.colorScheme.onSurfaceVariant
                    )
                    Text(
                        text = "Configure dispositivos no arquivo config.py do servidor",
                        style = Typography.bodySmall,
                        color = PhantasmaTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.7f),
                        textAlign = androidx.compose.ui.text.TextAlign.Center,
                        modifier = Modifier.padding(top = 8.dp)
                    )
                }
            }
        } else {
            LazyColumn(
                modifier = Modifier
                    .fillMaxSize()
                    .padding(16.dp),
                verticalArrangement = Arrangement.spacedBy(12.dp)
            ) {
                items(devices) { device ->
                    DeviceCard(
                        device = device,
                        onToggle = { onToggle(device) },
                        onControl = { action -> onControl(device, action) }
                    )
                }
            }
        }
    }
}

// Preview
@Preview
@Composable
fun DevicesScreenPreview() {
    PhantasmaTheme {
        DevicesScreen(
            devices = listOf(
                com.phantasma.app.api.models.DeviceInfo(
                    name = "Luz da Sala",
                    type = "tuya_light",
                    room = "Sala",
                    state = mapOf("online" to true),
                    online = true
                ),
                com.phantasma.app.api.models.DeviceInfo(
                    name = "Exaustor",
                    type = "tuya_switch",
                    room = "Cozinha",
                    state = mapOf("online" to false),
                    online = false
                )
            ),
            onToggle = {},
            onControl = { _, _ -> },
            onRefresh = {}
        )
    }
}