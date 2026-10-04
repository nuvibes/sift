/* The app's furniture: the pieces every screen is built out of.
 *
 * They live here rather than in the screens that use them because most of them are used by more than
 * one, and the ones that are not yet will be. A second screen that builds its own badge is a second
 * badge, and then amber means one thing on one screen and something else on the next.
 */

export { default as ActionBar } from './ActionBar.svelte';
export { default as Avatar } from './Avatar.svelte';
export type { AvatarShape } from './Avatar.svelte';
export { default as Badge } from './Badge.svelte';
export { default as Button } from './Button.svelte';
export type { ButtonSize, ButtonTone } from './Button.svelte';
export { default as BackButton } from './BackButton.svelte';
export { default as Breadcrumbs } from './Breadcrumbs.svelte';
export type { Crumb } from './Breadcrumbs.svelte';
export { default as Checkbox } from './Checkbox.svelte';
export type { CheckState } from './Checkbox.svelte';
export { default as Chip } from './Chip.svelte';
export { default as ChipRow } from './ChipRow.svelte';
export type { ChipRowGap } from './ChipRow.svelte';
export { default as ChooseFile } from './ChooseFile.svelte';
export { default as BarPanel } from './BarPanel.svelte';
export { default as ConfirmDialog } from './ConfirmDialog.svelte';
export { default as ContextMenu } from './ContextMenu.svelte';
export { default as ContextMenuItem } from './ContextMenuItem.svelte';
export { default as ContextMenuGroup } from './ContextMenuGroup.svelte';
export { default as ContextMenuSeparator } from './ContextMenuSeparator.svelte';
export { default as DataRow } from './DataRow.svelte';
export { default as DataRows } from './DataRows.svelte';
export { default as DoorCard } from './DoorCard.svelte';
export { default as Drawer } from './Drawer.svelte';
export type { DrawerSide } from './Drawer.svelte';
export { default as DateField } from './DateField.svelte';
export { default as DateRange } from './DateRange.svelte';
export { default as TimeField } from './TimeField.svelte';
export { default as Empty } from './Empty.svelte';
export { default as EntityRow } from './EntityRow.svelte';
export { default as Field } from './Field.svelte';
export { default as FormCard } from './FormCard.svelte';
/* A card about the thing under the pointer, shown while a link is hovered. NOT `Tooltip`: that
   labels a control and nothing in one may be operated: this can be walked into, and the links in
   it are real. See the component. */
export { default as LinkPreview } from './LinkPreview.svelte';
export { default as MenuButton } from './MenuButton.svelte';
export { default as PressSize } from './PressSize.svelte';
export { default as Meter } from './Meter.svelte';
export { default as NarrowBox } from './NarrowBox.svelte';
export { default as MoreAbout } from './MoreAbout.svelte';
export { default as Fold } from './Fold.svelte';
/* One value edited where it stands, with the cross and the tick inside its box. See the component. */
export { default as EditMarks } from './EditMarks.svelte';
/* The host every surface showing files renders inside: it owns the actions, the sheets that ask
   before they write, and the handlers tying the two together. See the component. */
export { default as FileVerbs } from './FileVerbs.svelte';
export { default as Heart } from './Heart.svelte';
/* Where a face's name came from, as its glyph in the accent: the strip under a file and a
   person's faces wall draw the same marks. See the component. */
export { default as FaceMark } from './FaceMark.svelte';
/* A history is what happened to a thing, in order. ONE row component draws an event everywhere
   (a file's, and a person's or a queue's when those arrive) and the list draws the thread between
   them. The shape and the glyph table travel with them. */
export { default as HistoryRow } from './HistoryRow.svelte';
export { default as HistoryList } from './HistoryList.svelte';
/* One History line drawn from the pieces the server built: the thread, the feed and Recent
   decisions draw a line with it, so a line reads and links one way everywhere. */
export { default as HistorySentence } from './HistorySentence.svelte';
export { BY_YOU, markOf, whenText } from './history';
export type { HistoryEvent, HistoryPiece, HistoryUndo } from './history';
export { default as NumberInput } from './NumberInput.svelte';
export { default as PasswordInput } from './PasswordInput.svelte';
/* The PIN, one digit per cell. `PIN_DIGITS` travels with it so no screen writes the 6 out. */
export { default as PinBox } from './PinBox.svelte';
export { PIN_DIGITS } from './PinBox.svelte';
export { default as LabelledRow } from './LabelledRow.svelte';
/* A name with the letters somebody typed picked out. The SPLIT is `$lib/search/search-marks`; this is
   what a matched run looks like, in one place rather than in each list that shows one. */
export { default as MarkedText } from './MarkedText.svelte';
export { default as Modal } from './Modal.svelte';
export { default as PickDialog } from './PickDialog.svelte';
/* The same pick, offered as the menu row opening out into the list instead of as a sheet over the
   screen. Both surfaces make the same write. See the component for which one each is right for. */
export { default as PickMenu } from './PickMenu.svelte';
export type { ChoicePicture, PickChoice, VerbPick } from './verbs';
/* The words on the pick sheet's finishing button. One per verb rather than one per screen. See
   the module. */
export { tagConfirmLabel } from './pick-labels';
/* The line a screen shows when something failed. One look, one announcement. See the component
   for why neither is a per-screen choice. The message under a form FIELD is `Field`'s, not this. */
/* A sentence with a symbol in front of it: a standing fact about a screen, said the same way
   everywhere. NOT `Problem`, which reports a failure and interrupts. See the component. */
export { default as Note } from './Note.svelte';
export type { NoteTone } from './Note.svelte';
export { default as Problem } from './Problem.svelte';
export type { Choice } from './PickDialog.svelte';
export { default as PasswordStrength } from './PasswordStrength.svelte';
/* The one look of a pick on a wall (a media tile or an entity card), whatever the pick is for. */
export { default as PickMark } from './PickMark.svelte';
export type { PickPurpose } from './PickMark.svelte';
export { default as ProgressBar } from './ProgressBar.svelte';
export { default as Pressable } from './Pressable.svelte';
export { default as RatingChip } from './RatingChip.svelte';
/* One picture, filling the screen, with nothing else on it, and the arithmetic that magnifies it,
   which the player's still uses as well. See the component for why it is a flavour of `Modal`
   rather than an overlay of its own. */
export { default as PictureViewer } from './PictureViewer.svelte';
export { Zoomable } from './zoom.svelte';
export { default as RowMenu } from './RowMenu.svelte';
export { default as Scroller } from './Scroller.svelte';
/* The quiet line across the top of the app. One shape for every one of them. See the component
   for why one shape. */
export { default as ShellBanner } from './ShellBanner.svelte';
/* Where somebody is in a flow of several steps, in words and as a bar. Both are announced, and
   for different reasons. See the component. */
/* Every slider in the app: the track, the fill on both engines, the thumb and the vertical form.
   See the component for the fill, which is the half a shared stylesheet cannot do. */
export { default as Slider } from './Slider.svelte';
export { default as Skeleton } from './Skeleton.svelte';
export type { SkeletonShape } from './Skeleton.svelte';
export { default as SuggestInput } from './SuggestInput.svelte';
export { default as Spinner } from './Spinner.svelte';
export type { SpinnerSize } from './Spinner.svelte';
export { default as Select } from './Select.svelte';
export { default as SplitButton } from './SplitButton.svelte';
export { default as AssetLink } from './AssetLink.svelte';
export { default as SettingLink } from './SettingLink.svelte';
export { default as SharingMark } from './SharingMark.svelte';
export type { SelectOption } from './Select.svelte';
export { default as Switch } from './Switch.svelte';
/* The one tab row: addresses, a filter, or panes, at page or panel size. */
export { default as Tabs } from './Tabs.svelte';
export type { TabChoice, TabLink } from './Tabs.svelte';
export { default as TagChip } from './TagChip.svelte';
export { default as Toaster } from './Toaster.svelte';
export { default as Tree } from './Tree.svelte';
/* The two renderers a declared verb list becomes markup through: one per surface, and the only
   two. See `$lib/grid/verbs`: between them they are what makes "the bar and the menu offer the
   same verbs" a fact rather than a habit. */
export { default as VerbButtons } from './VerbButtons.svelte';
export { default as VerbMenuItems } from './VerbMenuItems.svelte';
export { default as VerbMore } from './VerbMore.svelte';

export type { BadgeState } from './Badge.svelte';
export type { FaceMarkKind } from './FaceMark.svelte';
export type { FlatNode, TreeNode } from './tree';
export { flattenTree } from './tree';
export { applyFrozenOrder } from './frozen-order';
export { Selection } from './selection.svelte';
export { TileGesture, TILE_ID } from './tile-gesture.svelte';
export {
	ASSIGN_TYPE,
	carriesAssign,
	dropTarget,
	readAssign,
	startAssign
} from './drag-assign.svelte';
export type { AssignPayload, DropTarget } from './drag-assign.svelte';
/* The design declaration in each file is checked against this list (`check_design_entries.js`):
   a primitive the barrel does not name is a primitive half the app cannot find. */
export { default as ChoiceCard } from './ChoiceCard.svelte';
export { default as KeptPill } from './KeptPill.svelte';
export { default as KeyEcho } from './KeyEcho.svelte';
export { default as Pager } from './Pager.svelte';
export { default as RatingChoices } from './RatingChoices.svelte';
export { default as Tooltip } from './Tooltip.svelte';
export { default as Veil } from './Veil.svelte';
export { default as Withheld } from './Withheld.svelte';
export { default as Covered } from './Covered.svelte';
export { default as DropOffer } from './DropOffer.svelte';
export { default as PageShield } from './PageShield.svelte';
export { default as Separator } from './Separator.svelte';
/* The heading over one group of a page, and the hairline between groups. See the component for how
   it knows which group is first. */
export { default as SectionHeading } from './SectionHeading.svelte';
export { default as TextInput } from './TextInput.svelte';
/*
 * Choosing a colour, drawn from this app's own parts rather than opened as the operating system's.
 */
export { default as ColorPicker } from './ColorPicker.svelte';
export type { DerivedSwatch } from './ColorPicker.svelte';
export { default as TextArea } from './TextArea.svelte';
export { default as ChoiceGroup } from './ChoiceGroup.svelte';
export { default as Panel } from './Panel.svelte';
export { default as Toggle } from './Toggle.svelte';
export { default as Popover } from './Popover.svelte';
