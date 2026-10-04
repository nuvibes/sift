// What `check_card_fill.js` refuses, as a function of one file's code (comments already taken out)
// and where the file is, so the unit suite can hand it lines and watch it refuse them.
//
// A CARD is a thing that stands on the page as an object: an Insights card, an Organize board
// card, a person's card on the wall, a Settings choice. It wears the card's light, the one value
// `--sift-card` (or `--sift-card-fill` where it has no edge), declared once in `app.css`. Every
// other ground in the card tone stays flat: a menu, a popover, a sheet, a dialog, a field, a chip,
// a well holding a value, the shell's own furniture. A floating thing with a light of its own
// reads as a second lamp in the room.
//
// So every file that paints the flat card tone (`background: var(--sift-surface-2)`) is named
// here, either as a card (and then the flat tone is refused in it, but for a rule that is named
// as the floating form) or as excused, with the reason. A file in neither list is refused too:
// the next component painting the card tone has to say which it is, which is how the one written
// after this list does not quietly stay flat.

/**
 * The cards, as paths from `src`, each with the rules in it that may keep the flat tone.
 *
 * @type {Record<string, { flat: string[], why: string }>}
 */
export const CARDS = {
	'lib/components/common/Panel.svelte': {
		flat: ['.raised'],
		why: 'a raised panel standing on the page; `.raised` alone is the floating form'
	},
	'lib/components/common/ChoiceCard.svelte': { flat: [], why: 'a Settings choice' },
	'lib/components/entity/EntityCard.svelte': { flat: [], why: 'a card on the People wall' },
	'lib/components/FacesInThis.svelte': { flat: [], why: "a face's card on a file" },
	'lib/components/organize/IdentifiedPanel.svelte': { flat: [], why: 'a person on Organize' },
	'lib/components/insights/RecapCard.svelte': { flat: [], why: 'a recap card kept hidden' },
	'lib/settings-ui/UnlockSecrets.svelte': { flat: [], why: 'the locked card in Settings' },
	'lib/library/AddFolder.svelte': { flat: [], why: 'the add-a-folder card' },
	'routes/people/+page.svelte': { flat: [], why: 'the question card on People' },
	'routes/downloads/DownloadDetail.svelte': { flat: [], why: "a download's detail card" },
	'lib/settings-ui/ThemeChoices.svelte': {
		flat: ['.accents :global(.accent)'],
		why: "a base's miniature card; the accent pills are chips"
	}
};

/**
 * Files that paint the flat card tone and are not cards, with the reason.
 *
 * @type {Record<string, string>}
 */
export const EXCUSED = {
	'lib/components/SettingsModal.svelte': 'the close button of a dialog',
	'lib/components/Tile.svelte': 'the ground under a picture, seen only until the picture arrives',
	'lib/components/common/ActionBar.svelte': 'a floating bar',
	'lib/components/common/Drawer.svelte': 'a sheet',
	'lib/components/common/Empty.svelte': 'the disc behind an empty screen glyph',
	'lib/components/common/FormCard.svelte': 'a form has no ground at rest; this is its drop state',
	'lib/components/common/HistoryRow.svelte': "a history row's mark",
	'lib/components/common/PictureViewer.svelte': 'a button over a picture',
	'lib/components/common/PinBox.svelte': 'a field under the pointer',
	'lib/components/common/ShellBanner.svelte': "the shell's own banner row",
	'lib/components/common/Skeleton.svelte': 'a placeholder while a screen loads',
	'lib/components/common/Toaster.svelte': 'a floating toast',
	'lib/components/common/Tooltip.svelte': 'a floating tooltip',
	'lib/components/faces/MoveFacesDialog.svelte': 'the ground under a face picture in a dialog',
	'lib/components/faces/PileDetail.svelte': 'the ground under a face picture',
	'lib/components/organize/IdentifiedForPerson.svelte': 'the ground under a face picture',
	'lib/components/organize/MatchRow.svelte': 'a row',
	'lib/components/organize/TaggerPanel.svelte': 'a bar held at the foot of the panel',
	'lib/components/shell/FilterBar.svelte': 'the bar and its floating editor',
	'lib/components/shell/SearchSuggestions.svelte': 'a popover',
	'lib/components/theater/CellControls.svelte': "a bar's floating panel",
	'lib/library/FolderPicker.svelte': 'a chooser inside a sheet',
	'lib/settings-ui/ApplicationLog.svelte': 'a well holding the log, and its pinned day',
	'lib/settings-ui/NetworkSharing.svelte': 'a well holding a command',
	'lib/settings-ui/RecognitionNote.svelte': "a note's ground inside a settings row",
	'lib/settings-ui/SettingsShell.svelte': "the chosen section in a dialog's list",
	'lib/settings-ui/Tunnels.svelte': 'a drop zone',
	'lib/settings-ui/Updates.svelte': 'a well holding the release notes',
	'lib/settings-ui/Users.svelte': 'a well holding a generated password',
	'routes/library-location/+page.svelte': 'a well holding a folder path'
};

/** The DESIGN GALLERY draws a specimen of every flat surface as itself. */
const GALLERY = /^routes\/design\//;

/** The card tone painted flat: the ground a card no longer wears. */
const FLAT = /\bbackground(?:-color)?\s*:\s*var\(\s*--sift-surface-2\s*\)/;

/** The card's light, read. */
const LIT = /var\(\s*--sift-card(?:-fill)?\s*\)/;

/**
 * Every rule block in a file's code: its selector and its body, with the body's line.
 *
 * @param {string} code
 * @returns {{ selector: string, body: string, line: number }[]}
 */
function rules(code) {
	/** @type {{ selector: string, body: string, line: number }[]} */
	const found = [];
	for (const match of code.matchAll(/([^{};]+)\{([^{}]*)\}/g)) {
		const line = code.slice(0, (match.index ?? 0) + match[0].indexOf('{')).split('\n').length;
		found.push({
			selector: (match[1].trim().split('\n').pop() ?? '').trim(),
			body: match[2],
			line
		});
	}
	return found;
}

/**
 * Every refusal in one file.
 *
 * @param {string} code the file with its comments taken out
 * @param {string} where the file's path from `src`
 * @returns {{ line: number, what: string }[]}
 */
export function unlitCardsIn(code, where) {
	if (GALLERY.test(where)) return [];
	const flat = rules(code).filter((rule) => FLAT.test(rule.body));
	const card = CARDS[where];

	if (card) {
		const refused = flat
			.filter((rule) => !card.flat.includes(rule.selector))
			.map((rule) => ({
				line: rule.line,
				what: `a card painted in the flat card tone (\`${rule.selector}\`): ${card.why}`
			}));
		if (!LIT.test(code)) {
			refused.push({ line: 1, what: `a card that never wears the card's light: ${card.why}` });
		}
		return refused;
	}

	if (where in EXCUSED) return [];
	return flat.map((rule) => ({
		line: rule.line,
		what:
			`the flat card tone in a file nobody has named (\`${rule.selector}\`): a card wears ` +
			'`--sift-card`; anything else is excused in `scripts/lib/card-fill.js` with its reason'
	}));
}

/**
 * The shell's screen paints the page's light, and the flat canvas where an entity's blurred cover
 * is the ground; read as text, because the mapping is what breaks.
 *
 * @param {string} code the layout with its comments taken out
 * @returns {string[]}
 */
export function unlitScreenIn(code) {
	const all = rules(code);
	const screen = all.find((one) => one.selector === '.content');
	if (!screen) return ['no `.content` rule: the shell paints no screen'];
	const faults = [];
	if (!/\bbackground\s*:\s*var\(\s*--sift-page-fill\s*\)/.test(screen.body)) {
		faults.push("the shell's screen is not painted with `--sift-page-fill`");
	}
	const onCover = all.find((one) => /^\.content:has\(.*\.frame\.on-a-picture/.test(one.selector));
	if (!onCover || !/\bbackground\s*:\s*var\(\s*--sift-bg\s*\)/.test(onCover.body)) {
		faults.push("the screen under an entity's blurred cover is not the flat canvas");
	}
	return faults;
}
