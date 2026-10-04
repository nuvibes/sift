/* Letting other computers reach Sift, which Windows blocks until somebody says otherwise.
 *
 * Turning on "share this library on my network" makes Sift listen on every address this machine
 * has. It does not make Windows deliver anything to it: inbound connections are refused by
 * default and the refusal is silent, so the other computer waits and gives up, which reads
 * exactly like a wrong address.
 *
 * Why this asks for administrator rights and the installer does not: opening a port is a decision
 * about the whole machine, and Sift installs per user and never otherwise prompts. It is asked at
 * the moment sharing is turned on, through Windows' own elevation dialog, so the person sees what
 * is being run and can say no.
 *
 * What it answers is read, not claimed. Both verbs end by asking Windows what the rule is now, so
 * a refusal, a cancelled prompt and a rule removed by something else all end in the same honest
 * answer.
 *
 * The rule is the narrowest one that works: this port, TCP, inbound, private networks only, and
 * only from addresses on the same local network (`LocalSubnet`). Without that last part the rule
 * answers any address that can route to this machine, which on a network with a way in from
 * outside is more than the person agreed to on the strength of "my network". A program rule would
 * have to name the Python process inside Sift rather than Sift itself.
 *
 * Which network the machine is on is read beside the rule. Windows files a new network as Public
 * unless somebody says otherwise, and a private-only rule opens nothing on a Public one, so the
 * answer carries the active networks' categories and the rule's own scope and the screen says
 * what to change. Opening on public networks as well is offered, never assumed.
 */

import { execFile } from 'node:child_process';

/** What Windows says about the port right now. `unknown` means it could not be asked. */
export type FirewallState = 'open' | 'closed' | 'unknown';

/** How Windows files a network the machine is on. `Domain` is a network a domain controller runs. */
export type NetworkCategory = 'Private' | 'Public' | 'Domain';

/** Which networks the rule opens the port on. */
export type FirewallScope = 'private' | 'any';

/**
 * The whole answer: the rule, the networks the machine is on, and which of them the rule reaches.
 *
 * `networks` is null when Windows could not be asked; `scope` is null unless the port is open.
 */
export interface FirewallReport {
	state: FirewallState;
	networks: NetworkCategory[] | null;
	scope: FirewallScope | null;
}

/** The rule's name, and the only name this module will ever create or replace. */
export const RULE_NAME = 'Sift';

/** How long the read may take before it is abandoned. Nobody is waiting on a screen for it. */
const READ_TIMEOUT_MS = 15_000;

/* How long the elevated half may take. It covers a prompt somebody has to physically answer, so it
 * is minutes rather than seconds, and it exists only so a prompt that is never answered does not
 * leave a button spinning for the rest of the day. */
const ELEVATE_TIMEOUT_MS = 180_000;

/** Runs a PowerShell script and answers what it printed. Injected so a test never spawns one. */
export type Runner = (script: string, timeoutMs: number) => Promise<string | null>;

/* THE SCRIPT IS PASSED AS BASE64, and that is not obfuscation.
 *
 * PowerShell's -EncodedCommand takes UTF-16 base64, which removes every quoting question between
 * here and there, and there are three layers of them in an elevated call, because the inner
 * script travels as an argument of the outer one. The alternative is escaping quotes inside quotes
 * inside quotes, which is where this kind of code goes wrong. Nothing here is built from anything a
 * page can influence: the only value that varies is the port, and it is a number checked below.
 */
function encoded(script: string): string {
	return Buffer.from(script, 'utf16le').toString('base64');
}

/** Refuse anything that is not a port. The scripts below are built by string, so this is the one
 *  place a value could ever reach them, and it is checked rather than trusted. */
function portOrThrow(port: number): number {
	if (!Number.isInteger(port) || port < 1 || port > 65535) {
		throw new Error(`not a port: ${port}`);
	}
	return port;
}

/* Ask Windows whether the port is open, by object rather than by parsing text.
 *
 * `Get-NetFirewallRule` answers objects with the same field names in every language Windows ships
 * in; `netsh` answers a translated table. Sift runs on machines whose language nobody here chose,
 * so a check built on reading words would report "closed" on a German machine with the rule sitting
 * right there.
 *
 * A rule is only counted when it is enabled, inbound, allowing, and actually carries this port:
 * a disabled rule with the right name is the shape that would otherwise read as open.
 *
 * The networks the machine is on come first, as one line, and the rule's own profile with the
 * verdict: `networks: Private,Public` then `open Private` or `closed`. Three facts, because the
 * screen has to say why an open rule is letting nothing through.
 */
function readScript(port: number): string {
	return [
		"$ErrorActionPreference = 'SilentlyContinue'",
		'$categories = @(Get-NetConnectionProfile | ForEach-Object { "$($_.NetworkCategory)" })',
		'Write-Output ("networks: " + ($categories -join ","))',
		`$rules = @(Get-NetFirewallRule -DisplayName '${RULE_NAME}')`,
		'foreach ($rule in $rules) {',
		"  if ($rule.Enabled -ne 'True') { continue }",
		"  if ($rule.Direction -ne 'Inbound') { continue }",
		"  if ($rule.Action -ne 'Allow') { continue }",
		'  foreach ($filter in @($rule | Get-NetFirewallPortFilter)) {',
		`    if (@($filter.LocalPort) -contains '${port}') { Write-Output ("open " + "$($rule.Profile)"); exit 0 }`,
		'  }',
		'}',
		"Write-Output 'closed'"
	].join('\n');
}

/* Create the rule, replacing any earlier one of ours.
 *
 * Removed and re-created rather than edited, so that a rule left behind by an older version (on a
 * port Sift no longer uses) ends up correct instead of accumulating beside a second one. Only the
 * rule with our own name is ever touched.
 */
function createScript(port: number, scope: FirewallScope = 'private'): string {
	/* `Any` rather than the three named profiles: it is what the rule reads back as, so the scope
	   the screen shows is the scope that was asked for. Only the two words this module knows. */
	const profile = scope === 'any' ? 'Any' : 'Private';
	return [
		"$ErrorActionPreference = 'Stop'",
		'try {',
		`  Remove-NetFirewallRule -DisplayName '${RULE_NAME}' -ErrorAction SilentlyContinue`,
		`  New-NetFirewallRule -DisplayName '${RULE_NAME}' -Direction Inbound -LocalPort ${port}` +
			` -Protocol TCP -Action Allow -Profile ${profile} -RemoteAddress LocalSubnet | Out-Null`,
		'  exit 0',
		'} catch { exit 1 }'
	].join('\n');
}

/* Run that one elevated, through Windows' own dialog.
 *
 * `-Verb RunAs` is what raises it. The prompt is the operating system's: this application cannot
 * suppress it, pre-answer it or find out what it said except by looking at the result, which is
 * the property that makes offering this from a page acceptable at all.
 */
function elevateScript(inner: string): string {
	return [
		'try {',
		"  $started = Start-Process -FilePath 'powershell.exe' -Verb RunAs -WindowStyle Hidden" +
			' -Wait -PassThru -ErrorAction Stop -ArgumentList' +
			` '-NoProfile','-NonInteractive','-EncodedCommand','${encoded(inner)}'`,
		'  exit $started.ExitCode',
		'} catch { exit 5 }'
	].join('\n');
}

/** Run a script through PowerShell. Nothing goes through a shell, and no argument is a path. */
const powershell: Runner = (script, timeoutMs) =>
	new Promise((resolve) => {
		execFile(
			'powershell.exe',
			/* No execution policy is named, because nothing here runs a FILE: the policy governs
			 * scripts on disk and has never applied to a command passed in. Naming it anyway would be
			 * a line that reads like a bypass and does nothing. */
			['-NoProfile', '-NonInteractive', '-EncodedCommand', encoded(script)],
			{ timeout: timeoutMs, windowsHide: true },
			(error, stdout) => resolve(error ? null : stdout)
		);
	});

/** The categories Windows names, as this module spells them. Anything else is left out. */
function categoriesIn(line: string | undefined): NetworkCategory[] | null {
	if (line === undefined) return null;
	const named = line
		.slice('networks:'.length)
		.split(',')
		.map((one) => one.trim());
	const known: NetworkCategory[] = [];
	for (const one of named) {
		if (one === 'Private' || one === 'Public') known.push(one);
		else if (one === 'DomainAuthenticated' || one === 'Domain') known.push('Domain');
	}
	return known;
}

/** What the rule's own profile field means for the port: private networks only, or every one. */
function scopeOf(profile: string): FirewallScope {
	const named = profile.split(',').map((one) => one.trim());
	if (named.includes('Any') || named.includes('Public')) return 'any';
	return 'private';
}

/** Whether other computers can reach Sift on this port, on which networks. */
export async function firewallState(
	port: number,
	run: Runner = powershell
): Promise<FirewallReport> {
	const answer = await run(readScript(portOrThrow(port)), READ_TIMEOUT_MS);
	if (answer === null) return { state: 'unknown', networks: null, scope: null };
	const lines = answer
		.split(/\r?\n/)
		.map((one) => one.trim())
		.filter(Boolean);
	const networks = categoriesIn(lines.find((one) => one.startsWith('networks:')));
	const last = lines[lines.length - 1] ?? '';
	/* Only the words this script prints. Anything else (a warning banner, a partial line, a
	 * PowerShell that answered something we have never seen) is not evidence either way. */
	if (last.startsWith('open')) {
		return { state: 'open', networks, scope: scopeOf(last.slice('open'.length)) };
	}
	if (last === 'closed') return { state: 'closed', networks, scope: null };
	return { state: 'unknown', networks: null, scope: null };
}

/**
 * Ask Windows to open the port, then answer with what is true afterwards.
 *
 * The exit code of the elevated half is deliberately not the answer. A cancelled prompt, a policy
 * that forbids elevation and a rule that was created and immediately removed are three different
 * stories with one honest ending, and the screen only has to say one thing: it is open, or it is
 * not.
 */
export async function openFirewall(
	port: number,
	run: Runner = powershell,
	scope: FirewallScope = 'private'
): Promise<FirewallReport> {
	const checked = portOrThrow(port);
	await run(elevateScript(createScript(checked, scope)), ELEVATE_TIMEOUT_MS);
	return firewallState(checked, run);
}

/** The scripts, for the tests that assert what this actually asks Windows to do. */
export const scripts = { read: readScript, create: createScript, elevate: elevateScript };
