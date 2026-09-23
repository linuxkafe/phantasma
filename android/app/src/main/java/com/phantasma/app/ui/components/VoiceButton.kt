package com.phantasma.app.ui.components

import androidx.compose.animation.core.animateColorAsState
import androidx.compose.animation.core.tween
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material.icons.filled.MicOff
import androidx.compose.material.icons.filled.HourglassTop
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.phantasma.app.ui.theme.PhantasmaTheme
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.channels.receive
import kotlinx.coroutines.launch

enum class VoiceState {
    IDLE, LISTENING, PROCESSING
}

@Composable
fun VoiceButton(
    state: VoiceState,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    size: Int = 120
) {
    val colors = when (state) {
        VoiceState.IDLE -> PhantasmaTheme.colorScheme.primary to PhantasmaTheme.colorScheme.onPrimary
        VoiceState.LISTENING -> Color.Red to Color.White
        VoiceState.PROCESSING -> Color(0xFFF59E0B) to Color.White
    }

    val animatedColor by animateColorAsState(
        targetValue = colors.first,
        animationSpec = tween(300)
    )
    val animatedIconColor by animateColorAsState(
        targetValue = colors.second,
        animationSpec = tween(300)
    )

    val icon = when (state) {
        VoiceState.IDLE -> Icons.Default.Mic
        VoiceState.LISTENING -> Icons.Default.MicOff
        VoiceState.PROCESSING -> Icons.Default.HourglassTop
    }

    Box(
        modifier = modifier
            .size(size.dp)
            .clip(CircleShape)
            .background(animatedColor)
            .fillMaxWidth()
    ) {
        IconButton(
            onClick = onClick,
            modifier = Modifier
                .size(size.dp)
                .fillMaxSize()
        ) {
            Icon(
                imageVector = icon,
                contentDescription = "Voice button",
                tint = animatedIconColor,
                modifier = Modifier.size(48.dp)
            )
        }
    }

    // State label
    Text(
        text = when (state) {
            VoiceState.IDLE -> "Toque para falar"
            VoiceState.LISTENING -> "Ouvindo…"
            VoiceState.PROCESSING -> "Processando…"
        },
        style = androidx.compose.material3.MaterialTheme.typography.labelLarge,
        color = PhantasmaTheme.colorScheme.onSurfaceVariant,
        modifier = Modifier.padding(top = 16.dp)
    )
}

// Preview
@Preview
@Composable
fun VoiceButtonPreview() {
    PhantasmaTheme {
        VoiceButton(state = VoiceState.IDLE, onClick = {})
    }
}