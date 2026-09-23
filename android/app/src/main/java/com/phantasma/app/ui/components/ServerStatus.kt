package com.phantasma.app.ui.components

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.Card
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.phantasma.app.api.models.HealthStatus
import com.phantasma.app.ui.theme.PhantasmaTheme
import com.phantasma.app.ui.theme.Typography

@Composable
fun ServerStatus(
    status: HealthStatus?,
    onRefresh: () -> Unit,
    modifier: Modifier = Modifier
) {
    val isHealthy = status?.status == "healthy"
    val statusColor = if (isHealthy) Color(0xFF22C55E) else Color(0xFFF59E0B)
    val statusText = when (status?.status) {
        "healthy" -> "Conectado"
        "degraded" -> "Degradado"
        "unhealthy" -> "Offline"
        else -> "Desconhecido"
    }

    Card(
        modifier = modifier.fillMaxWidth(),
        colors = androidx.compose.material3.CardDefaults.cardColors(
            containerColor = PhantasmaTheme.colorScheme.surface,
            contentColor = PhantasmaTheme.colorScheme.onSurface
        ),
        shape = androidx.compose.foundation.shape.RoundedCornerShape(16.dp),
        elevation = androidx.compose.material3.CardDefaults.cardElevation(defaultElevation = 2.dp)
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(16.dp)
        ) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = androidx.compose.foundation.layout.Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Row(
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Box(
                        modifier = Modifier
                            .size(12.dp)
                            .clip(CircleShape)
                            .background(statusColor)
                    )
                    androidx.compose.foundation.layout.Spacer(modifier = androidx.compose.foundation.layout.padding(8.dp))
                    Text(
                        text = statusText,
                        style = Typography.titleMedium,
                        color = PhantasmaTheme.colorScheme.onSurface
                    )
                }

                androidx.compose.material3.IconButton(
                    onClick = onRefresh
                ) {
                    Icon(
                        imageVector = Icons.Default.Refresh,
                        contentDescription = "Atualizar status",
                        tint = PhantasmaTheme.colorScheme.onSurfaceVariant
                    )
                }
            }

            // Components status
            status?.components?.let { components ->
                androidx.compose.foundation.layout.Spacer(modifier = androidx.compose.foundation.layout.padding(top = 12.dp))
                Column(
                    modifier = Modifier.fillMaxWidth()
                ) {
                    components.entries.forEach { (name, state) ->
                        val isComponentHealthy = state == "healthy"
                        val componentColor = if (isComponentHealthy) Color(0xFF22C55E) else Color(0xFFF59E0B)

                        Row(
                            modifier = Modifier
                                .fillMaxWidth()
                                .padding(vertical = 4.dp),
                            horizontalArrangement = androidx.compose.foundation.layout.Arrangement.SpaceBetween
                        ) {
                            Text(
                                text = name.capitalize(),
                                style = Typography.bodySmall,
                                color = PhantasmaTheme.colorScheme.onSurfaceVariant
                            )
                            Box(
                                modifier = Modifier
                                    .size(8.dp)
                                    .clip(CircleShape)
                                    .background(componentColor)
                            )
                        }
                    }
                }
            }
        }
    }
}

// Preview
@Preview
@Composable
fun ServerStatusPreview() {
    PhantasmaTheme {
        ServerStatus(
            status = com.phantasma.app.api.models.HealthStatus(
                status = "healthy",
                version = "0.1.0",
                uptimeSeconds = 3600.0,
                components = mapOf(
                    "pipeline" to "healthy",
                    "stt" to "healthy",
                    "llm" to "healthy",
                    "tts" to "healthy",
                    "audio" to "healthy",
                    "ollama" to "healthy"
                ),
                timestamp = "2024-01-15T10:30:00"
            ),
            onRefresh = {}
        )
    }
}