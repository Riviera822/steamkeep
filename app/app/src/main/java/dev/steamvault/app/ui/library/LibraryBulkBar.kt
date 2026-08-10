package dev.steamvault.app.ui.library

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import dev.steamvault.app.R
import dev.steamvault.app.net.model.GameSummary
import dev.steamvault.app.ui.library.logic.buildBulkDownloadPlan
import dev.steamvault.app.ui.library.logic.classifyBulkDeleteEligibility
import dev.steamvault.app.ui.library.logic.classifyBulkSelection
import dev.steamvault.app.ui.library.logic.formatBytesGB
import kotlinx.coroutines.CoroutineScope

/**
 * Multi-select bottom bar (WP 4b.4 brief: bulk download split + bulk
 * delete). Mirrors `web/js/views/library.js`'s `syncBulk`: the selected set
 * is resolved against [allGames] (the FULL merged library, not just what is
 * currently visible) so a selection survives a search/filter change, same
 * as the mockup's "selections survive a search" rule.
 */
@Composable
fun LibraryBulkBar(controller: LibraryController, allGames: List<GameSummary>, scope: CoroutineScope) {
    val picked = remember(allGames, controller.selectedAppids) {
        allGames.filter { it.appid in controller.selectedAppids }
    }
    val classification = remember(picked, controller.jobs) { classifyBulkSelection(picked, controller.jobs) }
    val plan = remember(classification, controller.selectedAppids.size) {
        buildBulkDownloadPlan(classification, controller.selectedAppids.size)
    }
    val deletable = remember(picked, controller.jobs) { classifyBulkDeleteEligibility(picked, controller.jobs) }

    Column(modifier = Modifier.fillMaxWidth().padding(12.dp)) {
        Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
            Text(
                pluralStringResource(
                    R.plurals.library_bulk_selected_count,
                    controller.selectedAppids.size,
                    controller.selectedAppids.size,
                ),
                style = MaterialTheme.typography.titleMedium,
            )
            TextButton(onClick = { controller.exitSelect() }) {
                Text(stringResource(R.string.library_bulk_cancel))
            }
        }
        if (plan.note.isNotEmpty()) {
            Text(
                text = plan.note,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        Row(
            modifier = Modifier.fillMaxWidth().padding(top = 8.dp),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            if (deletable.isNotEmpty()) {
                OutlinedButton(onClick = {
                    controller.openDeleteConfirm(scope, deletable.map { it.appid })
                }) {
                    Text(stringResource(R.string.library_bulk_delete, deletable.size))
                }
            }
            if (plan.secondaryLabel != null) {
                OutlinedButton(onClick = { controller.startBulkDownload(scope, plan.secondaryTargets) }) {
                    Text(plan.secondaryLabel)
                }
            }
            Button(
                enabled = plan.primaryEnabled,
                onClick = { controller.startBulkDownload(scope, plan.primaryTargets) },
            ) {
                Text(plan.primaryLabel)
            }
        }
    }
}

/** Bulk-delete confirm dialog (WP 4b.4 brief: "bulk delete with set-aware
 * multiPlan arithmetic"). Mirrors `library.js`'s `openDeleteConfirm`/
 * `renderDeletePlan` three phases. */
@Composable
fun DeleteConfirmDialog(controller: LibraryController, scope: CoroutineScope) {
    val state = controller.deletePlan
    val ids = when (state) {
        is DeletePlanUiState.Hidden -> emptyList()
        is DeletePlanUiState.Loading -> state.ids
        is DeletePlanUiState.Ready -> state.ids
        is DeletePlanUiState.Error -> state.ids
    }
    if (ids.isEmpty()) return

    AlertDialog(
        onDismissRequest = { controller.closeDeleteConfirm() },
        title = { Text(pluralStringResource(R.plurals.library_delete_title, ids.size, ids.size)) },
        text = {
            when (state) {
                is DeletePlanUiState.Loading -> Row(verticalAlignment = Alignment.CenterVertically) {
                    CircularProgressIndicator(modifier = Modifier.padding(end = 8.dp))
                    Text(stringResource(R.string.library_delete_calculating))
                }
                is DeletePlanUiState.Error -> Text(stringResource(R.string.library_delete_error, state.message))
                is DeletePlanUiState.Ready -> DeletePlanBody(state)
                is DeletePlanUiState.Hidden -> {}
            }
        },
        confirmButton = {
            TextButton(
                enabled = state is DeletePlanUiState.Ready,
                onClick = { controller.confirmDelete(scope, ids) },
            ) {
                Text(stringResource(R.string.library_delete_confirm))
            }
        },
        dismissButton = {
            TextButton(onClick = { controller.closeDeleteConfirm() }) {
                Text(stringResource(R.string.library_delete_cancel))
            }
        },
    )
}

@Composable
private fun DeletePlanBody(state: DeletePlanUiState.Ready) {
    val plan = state.plan
    Column {
        for (row in plan.sharedRows) {
            Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                Text(row.depotid.toString(), style = MaterialTheme.typography.bodySmall)
                Text(
                    text = if (row.free) {
                        stringResource(R.string.library_delete_row_freed)
                    } else {
                        stringResource(R.string.library_delete_row_kept)
                    },
                    style = MaterialTheme.typography.bodySmall,
                    color = if (row.free) MaterialTheme.colorScheme.onSurfaceVariant else MaterialTheme.colorScheme.error,
                )
                Text(formatBytesGB(row.sizeBytes) ?: "—", style = MaterialTheme.typography.bodySmall)
            }
        }
        Text(
            text = stringResource(
                R.string.library_delete_freed_kept,
                formatBytesGB(plan.freedBytes) ?: "0 GB",
                formatBytesGB(plan.keptBytes) ?: "0 GB",
            ),
            style = MaterialTheme.typography.bodyMedium,
            modifier = Modifier.padding(top = 8.dp),
        )
    }
}
