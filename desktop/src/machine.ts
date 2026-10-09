/* What this computer is, for a Sift whose library lives on another one. */

import { execFile } from "node:child_process";
import * as os from "node:os";

/** One graphics adapter in this computer. Every field independently absent. */
export interface LocalCard {
  name: string | null;
  vram_bytes: number | null;
}

/** What the page draws under "This computer". Each fact independently absent. */
export interface LocalMachine {
  cpu_model: string | null;
  /** Logical processors: THREADS, not cores. A twelve-core chip with two threads each answers 24. */
  thread_count: number;
  installed_ram_bytes: number | null;
  gpu_cards: LocalCard[];
}

/** How long the memory query may take before this settles for what Node knows. */
const QUERY_TIMEOUT_MS = 4000;

/** How a question is put to the machine. Injected so a test never starts a process. */
export type Ask = (file: string, args: string[]) => Promise<string | null>;

const askWindows: Ask = (file, args) =>
  new Promise((answer) => {
    execFile(
      file,
      args,
      { timeout: QUERY_TIMEOUT_MS, windowsHide: true },
      (error, out) => answer(error ? null : out),
    );
  });

/* Asked once. */
let remembered: number | null | undefined;

export function forgetMemory(): void {
  remembered = undefined;
}

/** What the memory sticks in this computer add up to, or null. */
export async function installedMemory(
  ask: Ask = askWindows,
): Promise<number | null> {
  if (remembered !== undefined) return remembered;
  remembered = null;
  if (process.platform === "win32") {
    const said = await ask("powershell.exe", [
      "-NoProfile",
      "-NonInteractive",
      "-Command",
      "(Get-CimInstance Win32_PhysicalMemory | Measure-Object -Property Capacity -Sum).Sum",
    ]);
    const bytes = said === null ? Number.NaN : Number(said.trim());
    if (Number.isFinite(bytes) && bytes > 0) {
      remembered = bytes;
      return remembered;
    }
  }
  const known = os.totalmem();
  remembered = known > 0 ? known : null;
  return remembered;
}

/* Adapters Windows lists that are not hardware anybody has: `Microsoft Remote Display Adapter`
 * on any machine that has had a remote session, `Microsoft Basic Display Adapter` before a real
 * driver is installed. */
const NOT_REALLY_A_CARD = "microsoft ";

/* Where `AdapterRAM` stops being an answer: the field is 32 bits, so every card with 4 GB or
 * more reports this exact number and no more. */
const ADAPTER_RAM_CAP = 4293918720;

/* What the driver recorded about the card, and what is merely present. */
const ADAPTER_QUERY =
  "$mem = @{};" +
  " Get-ItemProperty" +
  " 'HKLM:\\SYSTEM\\CurrentControlSet\\Control\\Class\\{4d36e968-e325-11ce-bfc1-08002be10318}\\0*'" +
  " -ErrorAction SilentlyContinue | ForEach-Object {" +
  " if ($_.DriverDesc -and $_.'HardwareInformation.qwMemorySize')" +
  " { $mem[$_.DriverDesc] = $_.'HardwareInformation.qwMemorySize' } };" +
  " Get-CimInstance Win32_VideoController | ForEach-Object {" +
  " '{0}|{1}|{2}|{3}' -f $_.Name, $_.DriverVersion, $mem[$_.Name], $_.AdapterRAM }";

/** A size out of one field. Null for anything that is not a positive number. */
function asBytes(field: string | undefined): number | null {
  const value = Number((field ?? "").trim());
  return Number.isFinite(value) && value > 0 ? value : null;
}

/** One row of `Name|DriverVersion|RecordedBytes|AdapterRAM`, as far as it can be read. */
export function cardFrom(line: string): LocalCard | null {
  const parts = line.split("|").map((part) => part.trim());
  const name = parts[0] ? parts[0] : null;
  if (name === null || name.toLowerCase().startsWith(NOT_REALLY_A_CARD))
    return null;
  // What the driver recorded first, because it is the real figure.
  const recorded = asBytes(parts[2]);
  const capped = asBytes(parts[3]);
  const vram =
    recorded ?? (capped !== null && capped < ADAPTER_RAM_CAP ? capped : null);
  return { name, vram_bytes: vram };
}

/** Every graphics adapter in this computer, whoever made it. Empty where nothing will say. */
export async function graphicsCards(
  ask: Ask = askWindows,
): Promise<LocalCard[]> {
  if (process.platform !== "win32") return [];
  const said = await ask("powershell.exe", [
    "-NoProfile",
    "-NonInteractive",
    "-Command",
    ADAPTER_QUERY,
  ]);
  if (said === null) return [];
  return said
    .split(/\r?\n/)
    .map((line) => cardFrom(line))
    .filter((card): card is LocalCard => card !== null);
}

/** The longest name a computer answers to on a network (one DNS label). */
const LONGEST_NAME = 63;

/** What this computer is called, or null where it has no name to give. */
export function machineName(read: () => string = os.hostname): string | null {
  const name = read().trim().slice(0, LONGEST_NAME);
  return name === "" ? null : name;
}

/** This computer, as the settings screen draws it. */
export async function thisMachine(
  ask: Ask = askWindows,
): Promise<LocalMachine> {
  const processors = os.cpus();
  /* Both questions together. Each starts a process, and a settings screen that opened in half a
   * second and then took another half for a second answer would be slower for no reason. */
  const [memory, cards] = await Promise.all([
    installedMemory(ask),
    graphicsCards(ask),
  ]);
  return {
    /* Every entry carries the same model name; the first is as good as any. */
    cpu_model: processors[0]?.model?.trim() || null,
    thread_count: processors.length,
    installed_ram_bytes: memory,
    gpu_cards: cards,
  };
}
