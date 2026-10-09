/* Letting other computers reach Sift, which Windows blocks until somebody says otherwise. */

import { execFile } from 'node:child_process';

/** What Windows says about the port right now. `unknown` means it could not be asked. */
export type FirewallState = 'open' | 'closed' | 'unknown';

/** How Windows files a network the machine is on. `Domain` is a network a domain controller runs. */
export type NetworkCategory = 'Private' | 'Public' | 'Domain';

/** Which networks the rule opens the port on. */
export type FirewallScope = 'private' | 'any';

/** The whole answer: the rule, the networks the machine is on, and which of them the rule
 * reaches. */
export interface FirewallReport {
	state: FirewallState;
	networks: NetworkCategory[] | null;
	scope: FirewallScope | null;
}

/** The rule's name, and the only name this module will ever create or replace. */
export const RULE_NAME = 'Sift';

/** How long the read may take before it is abandoned. Nobody is waiting on a screen for it. */
const READ_TIMEOUT_MS = 15_000;

/* How long the elevated half may take. */
const ELEVATE_TIMEOUT_MS = 180_000;

/** Runs a PowerShell script and answers what it printed. Injected so a test never spawns one. */
export type Runner = (script: string, timeoutMs: number) => Promise<string | null>;

/* THE SCRIPT IS PASSED AS BASE64, and that is not obfuscation. */
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

/* Ask Windows whether the port is open, by object rather than by parsing text. */
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

/* Create the rule, replacing any earlier one of ours. */
function createScript(port: number, scope: FirewallScope = 'private'): string {
	/* `Any` rather than the three named profiles: it is what the rule reads back as, so the
	   scope the screen shows is the scope that was asked for. */
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

/* Run that one elevated, through Windows' own dialog. */
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
			 * scripts on disk and has never applied to a command passed in. */
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

/** Ask Windows to open the port, then answer with what is true afterwards. */
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
