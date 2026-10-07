/* What this computer is, for a Sift whose library lives on another one.
 *
 * ## Why the settings screen needs two answers rather than one
 *
 * Everything Sift does with a machine (scanning, transcoding, recognising faces) happens on
 * the computer running the library, so the server's hardware block describes the server and says
 * so. This is the other one: the computer the person is sitting at, which plays the video, holds
 * the window, and takes the file when they drag one out.
 *
 * Its graphics card is named even though it does no work for Sift, because a block headed "This
 * computer" that says nothing about its graphics has a hole in it; the sentence under the block
 * says that nothing here does the work.
 *
 * ## Why the memory is not `os.totalmem()`
 *
 * It is, as a fallback. Node reports what the operating system can address, which is the
 * installed total less what the firmware reserves. The server's
 * block reports what is installed, so this asks Windows the same question the backend asks
 * (once, at first use, and cached), and the two blocks agree.
 */

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

/* Asked once. The memory in a computer does not change while it is running, and the query starts a
 * process, so a settings screen opened five times would start five of them for one answer that
 * was already known. Null means "not asked yet"; a cached failure is remembered as such. */
let remembered: number | null | undefined;

export function forgetMemory(): void {
  remembered = undefined;
}

/**
 * What the memory sticks in this computer add up to, or null.
 *
 * The sticks are summed. `Win32_ComputerSystem.TotalPhysicalMemory` has the obvious name and
 * answers the addressable total: the number Node already knows, a little under what is fitted.
 * `Win32_PhysicalMemory` is the firmware's own table of what is fitted.
 *
 * Falls back to what Node knows, which is a little smaller, and still better than no row at all.
 */
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

/* Adapters Windows lists that are not hardware anybody has: `Microsoft Remote Display Adapter` on
 * any machine that has had a remote session, `Microsoft Basic Display Adapter` before a real driver
 * is installed. Both are real entries and neither is a card. Kept in step with the same rule in the
 * backend's own probe (see `_NOT_REALLY_A_CARD` in sift/kernel/hardware.py). */
const NOT_REALLY_A_CARD = "microsoft ";

/* Where `AdapterRAM` stops being an answer: the field is 32 bits, so every card with 4 GB or more
 * reports this exact number and no more.
 */
const ADAPTER_RAM_CAP = 4293918720;

/* What the driver recorded about the card, and what is merely present.
 *
 * `HardwareInformation.qwMemorySize` in the registry is 64 bits and is the real figure: a 16 GB
 * card has its size there and nothing at all in `AdapterRAM`, which stops at 32 bits. But the
 * registry keeps entries for drivers that are no longer installed, so it cannot say what is
 * PRESENT; `Win32_VideoController` can, and is the vague one about memory. One invocation, joined
 * on the name. Kept in step with `_ADAPTER_QUERY` in sift/kernel/hardware.py, which asks the same
 * two questions the same way. */
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
  // What the driver recorded first, because it is the real figure. The capped field only as a
  // fallback, and only below the cap: at the cap it means "four gigabytes or more" and no more.
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

/**
 * What this computer is called, or null where it has no name to give.
 *
 * For the name a phone's remote lists this window under: two copies of the app on two computers
 * both read "The Sift app on Windows" without it, and are told apart only by what they are
 * playing. Read from the operating system on every ask (a rename takes effect without a restart),
 * and held to one network label's length, which is the longest a name on a home network is.
 */
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
    /* Every entry carries the same model name; the first is as good as any. An empty list is
     * possible on a platform Node cannot enumerate, and answers nothing rather than crashing. */
    cpu_model: processors[0]?.model?.trim() || null,
    thread_count: processors.length,
    installed_ram_bytes: memory,
    gpu_cards: cards,
  };
}
