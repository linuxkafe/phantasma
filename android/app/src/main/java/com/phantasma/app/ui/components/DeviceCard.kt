package com.phantasma.app.ui.components

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Lightbulb
import androidx.compose.material.icons.filled.ToggleOn
import androidx.compose.material.icons.filled.Sensors
import androidx.compose.material.icons.filled.CleaningServices
import androidx.compose.material.icons.filled.DeviceUnknown
import androidx.compose.material3.Card
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.phantasma.app.api.models.DeviceInfo
import com.phantasma.app.ui.theme.PhantasmaTheme
import com.phantasma.app.ui.theme.Typography

@Composable
fun DeviceCard(
    device: DeviceInfo,
    onToggle: () -> Unit,
    onControl: (String) -> Unit,
    modifier: Modifier = Modifier
) {
    val isOn = device.state?.get("online") as? Boolean ?: device.online
    val stateColor = if (isOn) Color(0xFF22C55E) else Color(0xFF737373)
    val deviceIcon = when (device.type) {
        "tuya_light", "xiaomi_light" -> Lightbulb
        "tuya_switch" -> ToggleOn
        "tuya_sensor" -> Sensors
        "xiaomi_vacuum" -> CleaningServices
        else -> DeviceUnknown
    }

    Card(
        modifier = modifier
            .fillMaxWidth()
            .padding(horizontal = 16.dp, vertical = 8.dp),
        colors = androidx.compose.material3.CardDefaults.cardColors(
            containerColor = PhantasmaTheme.colorScheme.surface,
            contentColor = PhantasmaTheme.colorScheme.onSurface
        ),
        shape = RoundedCornerShape(16.dp),
        elevation = androidx.compose.material3.CardDefaults.cardElevation(defaultElevation = 4.dp)
    ) {
        Column(
            modifier = Modifier.fillMaxWidth(),
            horizontalAlignment = Alignment.CenterHorizontally
        ) {
            // Device icon with status indicator
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(16.dp)
            ) {
                Box(
                    modifier = Modifier
                        .size(80.dp)
                        .align(Alignment.Center)
                        .clip(androidx.compose.foundation.shape.CircleShape)
                        .background(PhantasmaTheme.colorScheme.primaryContainer)
                ) {
                    Icon(
                        imageVector = deviceIcon,
                        contentDescription = device.name,
                        tint = PhantasmaTheme.colorScheme.onPrimaryContainer,
                        modifier = Modifier
                            .size(40.dp)
                            .align(Alignment.Center)
                    )
                }

                // Status indicator
                Box(
                    modifier = Modifier
                        .size(20.dp)
                        .align(Alignment.BottomEnd)
                        .offset(x = 8.dp, y = 8.dp)
                        .clip(androidx.compose.foundation.shape.CircleShape)
                        .background(stateColor)
                )
            }

            // Device name and type
            Text(
                text = device.name,
                style = Typography.titleMedium,
                color = PhantasmaTheme.colorScheme.onSurface,
                textAlign = androidx.compose.ui.text.TextAlign.Center,
                modifier = Modifier.padding(top = 8.dp, bottom = 4.dp)
            )

            Text(
                text = device.type.replace("_", " ").capitalize(),
                style = Typography.bodySmall,
                color = PhantasmaTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(bottom = 8.dp)
            )

            // Room
            device.room?.let { room ->
                Text(
                    text = room,
                    style = Typography.bodySmall,
                    color = PhantasmaTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.7f),
                    modifier = Modifier.padding(bottom = 16.dp)
                )
            }

            // Actions
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 16.dp, bottom = 16.dp),
                horizontalArrangement = Arrangement.spacedBy(8.dp)
            ) {
                // Toggle button
                androidx.compose.material3.FilledButton(
                    onClick = onToggle,
                    colors = androidx.compose.material3.ButtonDefaults.filledButtonColors(
                        containerColor = if (isOn) Color(0xFF22C55E) else PhantasmaTheme.colorScheme.surfaceVariant,
                        contentColor = if (isOn) Color.White else PhantasmaTheme.colorScheme.onSurfaceVariant
                    ),
                    modifier = Modifier.weight(1f)
                ) {
                    Text(text = if (isOn) "Desligar" else "Ligar", fontSize = 14.sp)
                }

                // Control button (for brightness, color, etc.)
                if (device.type.contains("light")) {
                    androidx.compose.material3.OutlinedButton(
                        onClick = { onControl("brightness") },
                        modifier = Modifier.weight(1f)
                    ) {
                        Text(text = "Brilho", fontSize = 14.sp)
                    }
                }
            }
        }
    }
}

// Preview
@Preview
@Composable
fun DeviceCardPreview() {
    PhantasmaTheme {
        DeviceCard(
            device = DeviceInfo(
                name = "Luz da Sala",
                type = "tuya_light",
                room = "Sala",
                state = mapOf("online" to true),
                online = true
            ),
            onToggle = {},
            onControl = {}
        )
    }
}