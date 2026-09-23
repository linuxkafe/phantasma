package com.phantasma.app.ui.components

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Card
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.phantasma.app.api.models.MemoryEntry
import com.phantasma.app.ui.theme.PhantasmaTheme
import com.phantasma.app.ui.theme.Typography

@Composable
fun MemoryCard(
    memory: MemoryEntry,
    onDelete: () -> Unit,
    modifier: Modifier = Modifier
) {
    Card(
        modifier = modifier
            .fillMaxWidth()
            .padding(horizontal = 16.dp, vertical = 8.dp),
        colors = androidx.compose.material3.CardDefaults.cardColors(
            containerColor = PhantasmaTheme.colorScheme.surface,
            contentColor = PhantasmaTheme.colorScheme.onSurface
        ),
        shape = RoundedCornerShape(12.dp),
        elevation = androidx.compose.material3.CardDefaults.cardElevation(defaultElevation = 2.dp)
    ) {
        Column(
            modifier = Modifier.fillMaxWidth().padding(16.dp)
        ) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = androidx.compose.foundation.layout.Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Text(
                    text = memory.key,
                    style = Typography.titleMedium,
                    color = PhantasmaTheme.colorScheme.onSurface,
                    maxLines = 1,
                    overflow = androidx.compose.ui.text.overflow.TextOverflow.Ellipsis,
                    modifier = Modifier.weight(1f)
                )

                IconButton(
                    onClick = onDelete,
                    modifier = Modifier.padding(start = 8.dp)
                ) {
                    Icon(
                        imageVector = Icons.Default.Delete,
                        contentDescription = "Apagar memória",
                        tint = PhantasmaTheme.colorScheme.onSurfaceVariant
                    )
                }
            }

            Text(
                text = memory.value,
                style = Typography.bodyMedium,
                color = PhantasmaTheme.colorScheme.onSurfaceVariant,
                maxLines = 3,
                overflow = androidx.compose.ui.text.overflow.TextOverflow.Ellipsis,
                modifier = Modifier.padding(top = 8.dp)
            )

            Text(
                text = "Memorizado em ${memory.createdAt}",
                style = Typography.labelSmall,
                color = PhantasmaTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.6f),
                modifier = Modifier.padding(top = 8.dp)
            )
        }
    }
}

// Preview
@Preview
@Composable
fun MemoryCardPreview() {
    PhantasmaTheme {
        MemoryCard(
            memory = com.phantasma.app.api.models.MemoryEntry(
                id = 1,
                key = "nome do gato",
                value = "Bimby",
                createdAt = "2024-01-15T10:30:00",
                updatedAt = null
            ),
            onDelete = {}
        )
    }
}