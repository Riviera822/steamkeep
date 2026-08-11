package dev.steamvault.app.ui.settings

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Checkbox
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SegmentedButton
import androidx.compose.material3.SegmentedButtonDefaults
import androidx.compose.material3.SingleChoiceSegmentedButtonRow
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import dev.steamvault.app.R
import dev.steamvault.app.net.model.SettingInfoOut
import dev.steamvault.app.repo.SteamIdentityState
import dev.steamvault.app.storage.ProfileKind
import dev.steamvault.app.ui.settings.logic.SettingDraft
import dev.steamvault.app.ui.settings.logic.SettingsApplies
import dev.steamvault.app.ui.settings.logic.SettingsSource
import dev.steamvault.app.ui.settings.logic.canResetSetting
import dev.steamvault.app.ui.settings.logic.effectiveAsFieldText
import dev.steamvault.app.ui.settings.logic.parseSettingsApplies
import dev.steamvault.app.ui.settings.logic.parseSettingsSource
import kotlinx.coroutines.launch

/**
 * The Settings screen (WP 4b.7 brief) -- replaces `Destination.SETTINGS`'s
 * previous placeholder (bare [dev.steamvault.app.ui.identity.IdentityScreen]).
 * Three sections mirror [SettingsController]'s three independent surfaces --
 * see that class's kdoc.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SettingsScreen(
    controller: SettingsController,
    onSignInSteamClick: () -> Unit,
    onReconnectClick: () -> Unit,
    onDisconnected: () -> Unit,
    onRequestNotificationPermission: () -> Unit,
) {
    val scope = rememberCoroutineScope()
    LaunchedEffect(controller) { controller.load() }

    Scaffold(topBar = { TopAppBar(title = { Text(stringResource(R.string.settings_title)) }) }) { innerPadding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(innerPadding)
                .verticalScroll(rememberScrollState())
                .padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(16.dp),
        ) {
            when {
                controller.loading -> Text(stringResource(R.string.settings_loading))
                controller.loadError != null ->
                    Text(
                        stringResource(R.string.settings_load_error, controller.loadError.orEmpty()),
                        color = MaterialTheme.colorScheme.error,
                    )
                else -> SettingsForm(controller, scope)
            }

            HorizontalDivider()
            SteamIdentitySection(controller, onSignInSteamClick)
            HorizontalDivider()
            NotificationsSection(onRequestNotificationPermission)
            HorizontalDivider()
            ConnectionSection(controller, onReconnectClick, onDisconnected)
        }
    }
}

// ---------------------------------------------------------------------
// Notifications section (WP 4b.8)
// ---------------------------------------------------------------------

/**
 * The one piece of UI this WP adds to Settings: an explicit way to trigger
 * the POST_NOTIFICATIONS runtime prompt on API 33+ (brief: "request from
 * Settings screen context"). Deliberately minimal -- a button plus context,
 * always shown regardless of current grant state or SDK level (checking the
 * live permission state here would need a `LocalLifecycleOwner` resume
 * observer to refresh after the user returns from the system permission
 * dialog or app-info screen; out of scope for this WP's "keep it simple"
 * instruction, and harmless to omit -- tapping an already-granted
 * permission's request re-shows nothing on Android, and below API 33 the
 * tap is a documented no-op, per `MainActivity`'s own
 * `requestNotificationPermission` kdoc).
 * The background poll itself needs no permission at all and keeps running
 * either way (`NotificationPollWorker`'s kdoc).
 */
@Composable
private fun NotificationsSection(onRequestNotificationPermission: () -> Unit) {
    Text(stringResource(R.string.settings_section_notifications), style = MaterialTheme.typography.titleMedium)
    Text(
        stringResource(R.string.settings_notifications_desc),
        color = MaterialTheme.colorScheme.onSurfaceVariant,
        style = MaterialTheme.typography.bodySmall,
    )
    OutlinedButton(onClick = onRequestNotificationPermission) {
        Text(stringResource(R.string.settings_notifications_enable_button))
    }
}

// ---------------------------------------------------------------------
// GET/PATCH /v1/settings form
// ---------------------------------------------------------------------

@Composable
private fun SettingsForm(controller: SettingsController, scope: kotlinx.coroutines.CoroutineScope) {
    val response = controller.settingsResponse ?: return
    val entries = response.settings.associateBy { it.key }

    if (response.readonly) {
        Text(stringResource(R.string.settings_readonly_banner), style = MaterialTheme.typography.bodySmall)
    }

    Text(stringResource(R.string.settings_section_vault), style = MaterialTheme.typography.titleMedium)
    entries["vault_name"]?.let {
        SettingTextField(
            entry = it,
            draft = controller.drafts["vault_name"],
            label = stringResource(R.string.settings_vault_name_label),
            placeholder = stringResource(R.string.settings_vault_name_placeholder),
            readonly = response.readonly,
            onValueChange = { v -> controller.setDraft("vault_name", SettingDraft.Text(v)) },
            onReset = { controller.resetDraft("vault_name") },
        )
    }

    Text(stringResource(R.string.settings_section_schedule), style = MaterialTheme.typography.titleMedium)
    entries["schedule_window"]?.let {
        SettingTextField(
            entry = it,
            draft = controller.drafts["schedule_window"],
            label = stringResource(R.string.settings_schedule_window_label),
            placeholder = stringResource(R.string.settings_schedule_window_placeholder),
            hint = stringResource(R.string.settings_schedule_window_hint),
            readonly = response.readonly,
            onValueChange = { v -> controller.setDraft("schedule_window", SettingDraft.Text(v)) },
            onReset = { controller.resetDraft("schedule_window") },
        )
    }
    entries["schedule_interval_minutes"]?.let {
        SettingTextField(
            entry = it,
            draft = controller.drafts["schedule_interval_minutes"],
            label = stringResource(R.string.settings_schedule_interval_label),
            readonly = response.readonly,
            onValueChange = { v -> controller.setDraft("schedule_interval_minutes", SettingDraft.Text(v)) },
            onReset = { controller.resetDraft("schedule_interval_minutes") },
        )
    }
    entries["schedule_client_stale_days"]?.let {
        SettingTextField(
            entry = it,
            draft = controller.drafts["schedule_client_stale_days"],
            label = stringResource(R.string.settings_schedule_stale_days_label),
            readonly = response.readonly,
            onValueChange = { v -> controller.setDraft("schedule_client_stale_days", SettingDraft.Text(v)) },
            onReset = { controller.resetDraft("schedule_client_stale_days") },
        )
    }
    entries["auto_gc"]?.let { entry ->
        AutoGcField(entry, controller.drafts["auto_gc"], response.readonly) { v ->
            controller.setDraft("auto_gc", SettingDraft.Text(v))
        }
    }

    Text(stringResource(R.string.settings_section_webhook), style = MaterialTheme.typography.titleMedium)
    entries["webhook_url"]?.let {
        SettingTextField(
            entry = it,
            draft = controller.drafts["webhook_url"],
            label = stringResource(R.string.settings_webhook_url_label),
            placeholder = stringResource(R.string.settings_webhook_url_placeholder),
            hint = stringResource(R.string.settings_webhook_url_hint),
            readonly = response.readonly,
            onValueChange = { v -> controller.setDraft("webhook_url", SettingDraft.Text(v)) },
            onReset = { controller.resetDraft("webhook_url") },
        )
    }
    entries["webhook_events"]?.let { entry ->
        WebhookEventsField(entry, controller.drafts["webhook_events"], response.readonly) { v ->
            controller.setDraft("webhook_events", SettingDraft.EventsList(v))
        }
    }

    if (!response.readonly) {
        controller.saveError?.let {
            Text(stringResource(R.string.settings_save_error, it), color = MaterialTheme.colorScheme.error)
        }
        if (controller.isDirty) {
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.fillMaxWidth()) {
                OutlinedButton(onClick = { controller.discard() }, modifier = Modifier.weight(1f)) {
                    Text(stringResource(R.string.settings_discard_changes))
                }
                Button(
                    onClick = { scope.launch { controller.save() } },
                    enabled = !controller.saving,
                    modifier = Modifier.weight(1f),
                ) { Text(stringResource(R.string.settings_save_changes)) }
            }
        }
    }
}

@Composable
private fun captionFor(entry: SettingInfoOut): String {
    val source = when (parseSettingsSource(entry.source)) {
        SettingsSource.DB -> stringResource(R.string.settings_source_db)
        SettingsSource.ENV -> stringResource(R.string.settings_source_env)
        SettingsSource.DEFAULT -> stringResource(R.string.settings_source_default)
        SettingsSource.UNKNOWN -> stringResource(R.string.settings_source_unknown)
    }
    val applies = when (parseSettingsApplies(entry.applies)) {
        SettingsApplies.IMMEDIATELY -> stringResource(R.string.settings_applies_immediately)
        SettingsApplies.NEXT_SWEEP -> stringResource(R.string.settings_applies_next_sweep)
        SettingsApplies.RESTART_REQUIRED -> stringResource(R.string.settings_applies_restart_required)
        SettingsApplies.UNSPECIFIED -> stringResource(R.string.settings_applies_unspecified)
    }
    return stringResource(R.string.settings_caption, source, applies)
}

@Composable
private fun SettingTextField(
    entry: SettingInfoOut,
    draft: SettingDraft?,
    label: String,
    readonly: Boolean,
    onValueChange: (String) -> Unit,
    onReset: () -> Unit,
    placeholder: String? = null,
    hint: String? = null,
) {
    val fieldValue = when (draft) {
        is SettingDraft.Text -> draft.value
        is SettingDraft.Reset -> effectiveAsFieldText(entry.copy(effective = entry.fallback))
        else -> effectiveAsFieldText(entry)
    }
    Column {
        OutlinedTextField(
            value = fieldValue,
            onValueChange = onValueChange,
            label = { Text(label) },
            placeholder = placeholder?.let { { Text(it) } },
            singleLine = true,
            enabled = !readonly,
            modifier = Modifier.fillMaxWidth(),
        )
        Text(captionFor(entry), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        hint?.let { Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant) }
        if (!readonly && canResetSetting(entry)) {
            TextButton(onClick = onReset) { Text(stringResource(R.string.settings_reset)) }
        }
    }
}

@Composable
private fun AutoGcField(
    entry: SettingInfoOut,
    draft: SettingDraft?,
    readonly: Boolean,
    onSelect: (String) -> Unit,
) {
    val options = listOf(
        "off" to stringResource(R.string.settings_auto_gc_off),
        "dry-run" to stringResource(R.string.settings_auto_gc_dry_run),
        "execute" to stringResource(R.string.settings_auto_gc_execute),
    )
    val current = (draft as? SettingDraft.Text)?.value ?: effectiveAsFieldText(entry)
    Column {
        Text(stringResource(R.string.settings_auto_gc_label))
        SingleChoiceSegmentedButtonRow(modifier = Modifier.fillMaxWidth()) {
            options.forEachIndexed { index, (mode, label) ->
                SegmentedButton(
                    selected = mode == current,
                    onClick = { onSelect(mode) },
                    enabled = !readonly,
                    shape = SegmentedButtonDefaults.itemShape(index = index, count = options.size),
                ) { Text(label) }
            }
        }
        Text(captionFor(entry), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

@Composable
private fun WebhookEventsField(
    entry: SettingInfoOut,
    draft: SettingDraft?,
    readonly: Boolean,
    onChange: (List<String>) -> Unit,
) {
    val options = listOf(
        "job.done" to stringResource(R.string.settings_webhook_event_job_done),
        "job.error" to stringResource(R.string.settings_webhook_event_job_error),
        "job.cancelled" to stringResource(R.string.settings_webhook_event_job_cancelled),
        "client.bypass_suspected" to stringResource(R.string.settings_webhook_event_bypass_suspected),
        "client.bypass_resolved" to stringResource(R.string.settings_webhook_event_bypass_resolved),
    )
    val current: Set<String> = when (draft) {
        is SettingDraft.EventsList -> draft.values.toSet()
        else -> effectiveAsFieldText(entry).split(",").map { it.trim() }.filter { it.isNotEmpty() }.toSet()
    }
    Column {
        Text(stringResource(R.string.settings_webhook_events_label))
        for ((value, label) in options) {
            Row {
                Checkbox(
                    checked = value in current,
                    enabled = !readonly,
                    onCheckedChange = { checked ->
                        onChange(if (checked) (current + value).toList() else (current - value).toList())
                    },
                )
                Text(label, modifier = Modifier.padding(top = 12.dp))
            }
        }
        Text(captionFor(entry), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

// ---------------------------------------------------------------------
// Steam identity section (existing sign-in state + Web API key management)
// ---------------------------------------------------------------------

@Composable
private fun SteamIdentitySection(controller: SettingsController, onSignInSteamClick: () -> Unit) {
    Text(stringResource(R.string.settings_section_steam), style = MaterialTheme.typography.titleMedium)
    val identity: SteamIdentityState = controller.identityState

    if (!identity.isSignedIn) {
        controller.loginError?.let {
            Text(stringResource(R.string.identity_login_failed, it), color = MaterialTheme.colorScheme.error)
        }
        Button(onClick = onSignInSteamClick) { Text(stringResource(R.string.identity_sign_in)) }
    } else {
        Text(stringResource(R.string.identity_steamid_label, identity.steamId64.orEmpty()))
        Text(
            identity.personaName?.let { stringResource(R.string.identity_persona_label, it) }
                ?: stringResource(R.string.identity_persona_unknown),
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        OutlinedButton(onClick = { controller.signOutSteam() }) { Text(stringResource(R.string.identity_sign_out)) }
    }

    Text(stringResource(R.string.onboarding_steam_key_label), style = MaterialTheme.typography.titleSmall)
    // Masked display (WP brief: "only whether set, never the value").
    Text(
        if (identity.hasWebApiKey) stringResource(R.string.settings_steam_key_masked) else stringResource(R.string.settings_steam_key_not_set),
        color = MaterialTheme.colorScheme.onSurfaceVariant,
    )
    OutlinedTextField(
        value = controller.webApiKeyInput,
        onValueChange = { controller.webApiKeyInput = it },
        placeholder = { Text(stringResource(R.string.onboarding_steam_key_placeholder)) },
        singleLine = true,
        modifier = Modifier.fillMaxWidth(),
    )
    controller.webApiKeyError?.let { Text(it, color = MaterialTheme.colorScheme.error) }
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        Button(onClick = { controller.submitWebApiKeyEntry() }) { Text(stringResource(R.string.onboarding_steam_key_save)) }
        if (identity.hasWebApiKey) {
            OutlinedButton(onClick = { controller.removeWebApiKey() }) { Text(stringResource(R.string.settings_steam_key_remove)) }
        }
    }
}

// ---------------------------------------------------------------------
// Connection section
// ---------------------------------------------------------------------

@Composable
private fun ConnectionSection(
    controller: SettingsController,
    onReconnectClick: () -> Unit,
    onDisconnected: () -> Unit,
) {
    var showDisconnectConfirm by remember { mutableStateOf(false) }
    Text(stringResource(R.string.settings_section_connection), style = MaterialTheme.typography.titleMedium)

    val summary = controller.connectionSummary()
    if (summary.isConfigured) {
        val profileLabel = when (summary.profileKind) {
            ProfileKind.PUBLIC_DOMAIN -> stringResource(R.string.onboarding_profile_public_domain)
            else -> stringResource(R.string.onboarding_profile_system_vpn)
        }
        Text(stringResource(R.string.settings_connection_current, summary.baseUrl.orEmpty(), profileLabel))
    }

    Text(stringResource(R.string.settings_reconnect_title), style = MaterialTheme.typography.titleSmall)
    Text(stringResource(R.string.settings_reconnect_desc), color = MaterialTheme.colorScheme.onSurfaceVariant)
    OutlinedButton(onClick = onReconnectClick) { Text(stringResource(R.string.settings_reconnect_button)) }

    Text(stringResource(R.string.settings_disconnect_title), style = MaterialTheme.typography.titleSmall)
    Text(stringResource(R.string.settings_disconnect_desc), color = MaterialTheme.colorScheme.onSurfaceVariant)
    OutlinedButton(onClick = { showDisconnectConfirm = true }) { Text(stringResource(R.string.settings_disconnect_button)) }

    if (showDisconnectConfirm) {
        AlertDialog(
            onDismissRequest = { showDisconnectConfirm = false },
            title = { Text(stringResource(R.string.settings_disconnect_confirm_title)) },
            text = { Text(stringResource(R.string.settings_disconnect_confirm_body)) },
            confirmButton = {
                TextButton(onClick = {
                    showDisconnectConfirm = false
                    controller.disconnect()
                    onDisconnected()
                }) { Text(stringResource(R.string.settings_disconnect_confirm_confirm)) }
            },
            dismissButton = {
                TextButton(onClick = { showDisconnectConfirm = false }) {
                    Text(stringResource(R.string.settings_disconnect_confirm_cancel))
                }
            },
        )
    }
}
