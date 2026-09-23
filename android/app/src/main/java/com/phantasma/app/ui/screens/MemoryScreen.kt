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
import androidx.compose.material.icons.filled.Memory
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material3.Card
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.Text
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.FilterChip
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.phantasma.app.api.models.MemoryEntry
import com.phantasma.app.ui.components.MemoryCard
import com.phantasma.app.ui.theme.PhantasmaTheme
import com.phantasma.app.ui.theme.Typography

@Composable
fun MemoryScreen(
    memories: List<MemoryEntry>,
    onAddMemory: (String, String) -> Unit,
    onDelete: (MemoryEntry) -> Unit,
    onRefresh: () -> Unit,
    modifier: Modifier = Modifier
) {
    var showAddDialog by remember { mutableStateOf(false) }
    var newKey by remember { mutableStateOf("") }
    var newValue by remember { mutableStateOf("") }

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
                text = "Memória",
                style = Typography.headlineMedium,
                color = PhantasmaTheme.colorScheme.onSurface
            )

            Row(
                horizontalArrangement = Arrangement.spacedBy(8.dp)
            ) {
                androidx.compose.material3.IconButton(
                    onClick = { showAddDialog = true }
                ) {
                    Icon(
                        imageVector = Icons.Default.Add,
                        contentDescription = "Adicionar memória",
                        tint = PhantasmaTheme.colorScheme.onSurface
                    )
                }

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
        }

        // Memory list
        if (memories.isEmpty()) {
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
                        imageVector = Icons.Default.Memory,
                        contentDescription = "Sem memórias",
                        modifier = Modifier.size(64.dp),
                        tint = PhantasmaTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.5f)
                    )
                    androidx.compose.foundation.layout.Spacer(modifier = androidx.compose.foundation.layout.padding(16.dp))
                    Text(
                        text = "Nenhuma memória guardada",
                        style = Typography.titleMedium,
                        color = PhantasmaTheme.colorScheme.onSurfaceVariant
                    )
                    Text(
                        text = "Diga \"memoriza que...\" ou use o botão +",
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
                items(memories) { memory ->
                    MemoryCard(
                        memory = memory,
                        onDelete = { onDelete(memory) }
                    )
                }
            }
        }

        // Add memory dialog
        if (showAddDialog) {
            AddMemoryDialog(
                onDismiss = { showAddDialog = false },
                onConfirm = { key, value ->
                    onAddMemory(key, value)
                    showAddDialog = false
                }
            )
        }
    }
}

@Composable
fun AddMemoryDialog(
    onDismiss: () -> Unit,
    onConfirm: (String, String) -> Unit
) {
    var key by remember { mutableStateOf("") }
    var value by remember { mutableStateOf("") }

    androidx.compose.material3.AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(text = "Nova Memória", style = Typography.titleLarge) },
        text = {
            Column(
                modifier = Modifier.padding(16.dp).fillMaxWidth(),
                verticalArrangement = Arrangement.spacedBy(16.dp)
            ) {
                androidx.compose.material3.OutlinedTextField(
                    value = key,
                    onValueChange = { key = it },
                    label = { Text("Chave (ex: nome do gato)") },
                    modifier = Modifier.fillMaxWidth()
                )
                androidx.compose.material3.OutlinedTextField(
                    value = value,
                    onValueChange = { value = it },
                    label = { Text("Valor (ex: Bimby)") },
                    modifier = Modifier.fillMaxWidth()
                )
            }
        },
        confirmButton = {
            androidx.compose.material3.TextButton(
                onClick = {
                    if (key.isNotBlank() && value.isNotBlank()) {
                        onConfirm(key, value)
                    }
                }
            ) {
                Text(text = "Salvar")
            }
        },
        dismissButton = {
            androidx.compose.material3.TextButton(onClick = onDismiss) {
                Text(text = "Cancelar")
            }
        }
    )
}

// Preview
@Preview
@Composable
fun MemoryScreenPreview() {
    PhantasmaTheme {
        MemoryScreen(
            memories = listOf(
                com.phantasma.app.api.models.MemoryEntry(
                    id = 1,
                    key = "nome do gato",
                    value = "Bimby",
                    createdAt = "2024-01-15T10:30:00"
                ),
                com.phantasma.app.api.models.MemoryEntry(
                    id = 2,
                    key = "comida favorita",
                    value = "Feijão com arroz",
                    createdAt = "2024-01-10T14:20:00"
                )
            ),
            onAddMemory = { _, _ -> },
            onDelete = {},
            onRefresh = {}
        )
    }
}