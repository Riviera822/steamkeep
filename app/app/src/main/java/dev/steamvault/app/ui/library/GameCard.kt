package dev.steamvault.app.ui.library

import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Checkbox
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import dev.steamvault.app.ui.library.logic.GameCardModel
import dev.steamvault.app.ui.library.logic.StatusActionType
import dev.steamvault.app.ui.status.StatusIcon
import dev.steamvault.app.ui.status.StatusIconSize
import dev.steamvault.app.ui.theme.VaultColors

/**
 * One grid cell (2/3-column layouts) — the mockup's capsule pill: status
 * icon + size, one tappable object over the artwork (docs/design/
 * vault-app-mockup-NOTES.md round 6, "The capsule pill"). See
 * `ui/library/logic/GameCardModel.kt`'s kdoc for why this composable is
 * skip-safe across a poll tick as long as [model] compares equal.
 */
@OptIn(ExperimentalFoundationApi::class)
@Composable
fun GameCard(
    model: GameCardModel,
    selecting: Boolean,
    onOpen: (Int) -> Unit,
    onLongPress: (Int) -> Unit,
    onToggleSelect: (Int) -> Unit,
    onAction: (Int, StatusActionType) -> Unit,
    modifier: Modifier = Modifier,
) {
    Column(
        modifier = modifier
            .combinedClickable(
                onClick = { if (selecting) onToggleSelect(model.appid) else onOpen(model.appid) },
                onLongClick = { onLongPress(model.appid) },
            ),
    ) {
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .aspectRatio(2f / 3f)
                .clip(RoundedCornerShape(10.dp)),
        ) {
            CoverArtImage(
                coverUrl = model.coverUrl,
                name = model.name,
                fallbackHues = model.fallbackHues,
                modifier = Modifier.fillMaxSize(),
            )

            if (selecting) {
                Checkbox(
                    checked = model.selected,
                    onCheckedChange = { onToggleSelect(model.appid) },
                    modifier = Modifier.align(Alignment.TopStart),
                )
            }

            CapsulePill(
                model = model,
                onAction = onAction,
                selecting = selecting,
                modifier = Modifier
                    .align(Alignment.BottomStart)
                    .padding(6.dp),
            )
        }
        Text(
            text = model.name,
            style = MaterialTheme.typography.bodySmall,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
            modifier = Modifier
                .fillMaxWidth()
                .padding(top = 4.dp),
        )
        // Mockup round 6: "the grids got the status word back" -- the pill
        // carries icon + size, the meta row underneath carries the word, so
        // nothing in the roomier layouts is said only once via colour/shape.
        Text(
            text = stringResource(model.kind.labelRes),
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
            modifier = Modifier.fillMaxWidth(),
        )
    }
}

/** The status icon + size pill riding on the cover art (round-6 "one object
 * instead of a colour dot plus a duplicate line"). Inert (no click target)
 * for kinds with no honest action, and always inert while multi-selecting
 * (a tap must toggle selection, mockup parity — `model.action` is already
 * `null` in that case, see `GameStatus.kt::statusAction`). */
@Composable
private fun CapsulePill(
    model: GameCardModel,
    onAction: (Int, StatusActionType) -> Unit,
    selecting: Boolean,
    modifier: Modifier = Modifier,
) {
    val action = model.action
    val clickableModifier = if (!selecting && action != null) {
        Modifier.clickable { onAction(model.appid, action.type) }
    } else {
        Modifier
    }
    Row(
        modifier = modifier
            .clip(RoundedCornerShape(50))
            .background(Color.Black.copy(alpha = 0.55f))
            .then(clickableModifier)
            .padding(horizontal = 6.dp, vertical = 3.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        StatusIcon(kind = model.kind, size = StatusIconSize.SMALL)
        if (model.sizeLabel != null) {
            Text(
                text = model.sizeLabel,
                style = MaterialTheme.typography.labelSmall,
                color = VaultColors.Text,
                modifier = Modifier.padding(start = 4.dp),
            )
        }
    }
}
