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
import androidx.compose.material.icons.filled.Memory
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material3.Card
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.phantasma.app.api.models.DeviceInfo
import com.phantasma.app.api.models.HealthStatus
import com.phantasma.app.api.models.MemoryEntry
import com.phantasma.app.ui.components.DeviceCard
import com.phantasma.app.ui.components.MemoryCard
import com.phantasma.app.ui.components.ServerStatus
import com.phantasma.app.ui.components.VoiceButton
import com.phantasma.app.ui.components.VoiceState
import com.phantasma.app.ui.theme.PhantasmaTheme
import com.phantasma.app.ui.theme.Typography
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.channels.receive
import kotlinx.coroutines.launch

@Composable
fun HomeScreen(
    serverStatus: HealthStatus?,
    onVoiceClick: () -> Unit,
    voiceState: VoiceState,
    onRefresh: () -> Unit,
    modifier: Modifier = Modifier
) {
    Column(
        modifier = modifier
            .fillMaxSize()
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp)
    ) {
        // Server status card
        ServerStatus(
            status = serverStatus,
            onRefresh = onRefresh
        )

        // Voice button - main action
        VoiceButton(
            state = voiceState,
            onClick = onVoiceClick,
            size = 140
        )

        // Quick actions
        Text(
            text = "Ações rápidas",
            style = Typography.titleMedium,
            color = PhantasmaTheme.colorScheme.onSurface,
            modifier = Modifier
                .fillMaxWidth()
                .padding(top = 16.dp, bottom = 8.dp)
        )

        // Quick action cards
        androidx.compose.foundation.lazy.LazyRow(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(12.dp),
            contentPadding = androidx.compose.foundation.layout.PaddingValues(horizontal = 16.dp)
        ) {
            item { QuickActionCard("Dispositivos", Icons.Default.Home, PhantasmaTheme.colorScheme.primary) }
            item { QuickActionCard("Memória", Icons.Default.Memory, PhantasmaTheme.colorScheme.tertiary) }
            item { QuickActionCard("Configurações", Icons.Default.Settings, PhantasmaTheme.colorScheme.secondary) }
        }
    }
}

@Composable
fun QuickActionCard(
    title: String,
    icon: androidx.compose.ui.graphics.vector.ImageVector,
    color: Color,
    modifier: Modifier = Modifier
) {
    Card(
        modifier = modifier
            .size(120.dp)
            .padding(bottom = 16.dp),
        colors = androidx.compose.material3.CardDefaults.cardColors(
            containerColor = color.copy(alpha = 0.1f),
            contentColor = color
        ),
        shape = androidx.compose.foundation.shape.RoundedCornerShape(16.dp)
    ) {
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(16.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.Center
        ) {
            androidx.compose.material3.Icon(
                imageVector = icon,
                contentDescription = title,
                modifier = Modifier.size(32.dp),
                tint = color
            )
            androidx.compose.foundation.layout.Spacer(modifier = androidx.compose.foundation.layout.padding(8.dp))
            Text(
                text = title,
                style = androidx.compose.material3.MaterialTheme.typography.labelLarge,
                color = color
            )
        }
    }
}

// Preview
@Preview
@Composable
fun HomeScreenPreview() {
    PhantasmaTheme {
        HomeScreen(
            serverStatus = null,
            onVoiceClick = {},
            voiceState = VoiceState.IDLE,
            onRefresh = {}
        )
    }
}