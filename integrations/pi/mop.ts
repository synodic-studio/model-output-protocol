/**
 * MOP output-gate extension for Pi.
 *
 * Gates Pi's final assistant message through MOP by shelling out to the `mop`
 * CLI (Pi is TypeScript; MOP is Python). Lives in the model-output-protocol
 * repo at integrations/pi/mop.ts; load it with `pi -e <repo>/integrations/pi/mop.ts`
 * or symlink into ~/.pi/agent/extensions/.
 *
 * Modes (env MOP_MODE):
 *   - "log" (default): observe-only. Reads the final assistant text, calls
 *     `mop check` to evaluate + audit it, and does NOT alter the message.
 *     Relies on nothing undocumented — as solid as any observer.
 *   - "enforce": mutates the final message in place to the MOP verdict
 *     (rewrite applied, rejection redacted). This rewrites the object Pi later
 *     writes to stdout / persists. NOTE: this leans on Pi passing the final
 *     message BY REFERENCE through the awaited `message_end` handler — verified
 *     for `pi -p` text mode and for the `agent_end` payload that relays read,
 *     but it is Pi-internal behavior that could change across versions. Keep it
 *     opt-in and re-verify before trusting it as a hard gate.
 *
 * Config (env, same surface as the Hermes plugin):
 *   MOP_MODE=log|enforce      default log
 *   MOP_CMD=<path>            mop CLI (default: <repo>/.venv/bin/mop, else `mop`)
 *   MOP_RULES_DIR=<dir>       a .mop-style rules dir (unset = no local rules)
 *   MOP_AUDIT_LOG=<dir>       JSONL flight recorder (read by the mop CLI)
 *   MOP_BUILTINS=1            opt in to packaged built-in rules
 *   MOP_EVALUATOR_MODEL=<m>   litellm model/tier (read by the mop CLI)
 */

import { execFileSync } from "node:child_process";
import { existsSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import type { ExtensionAPI } from "@mariozechner/pi-coding-agent";

const REDACTION_NOTICE = "[message withheld by MOP]";

function resolveMopCmd(): string {
	if (process.env.MOP_CMD) return process.env.MOP_CMD;
	// This file is <repo>/integrations/pi/mop.ts → repo root is two dirs up.
	const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");
	const venvMop = resolve(repoRoot, ".venv", "bin", "mop");
	return existsSync(venvMop) ? venvMop : "mop";
}

function extractText(content: unknown): string {
	if (!Array.isArray(content)) return "";
	return content
		.filter((c): c is { type: "text"; text: string } => !!c && (c as any).type === "text")
		.map((c) => c.text)
		.join("\n");
}

type Verdict = { verdict: "accepted" | "rewritten" | "rejected"; rewritten: string | null };

function runMopCheck(text: string, mode: string): Verdict | null {
	const mop = resolveMopCmd();
	const args = ["check", "--host", "pi", "--json"];
	if (mode !== "enforce") args.push("--no-rewrite"); // log mode: cheap, judge-only
	if (process.env.MOP_RULES_DIR) args.push("--rules-dir", process.env.MOP_RULES_DIR);
	if (process.env.MOP_BUILTINS && !["", "0", "false"].includes(process.env.MOP_BUILTINS))
		args.push("--builtins");
	// mop exits 0/1/2 for accepted/rewritten/rejected and 3 on error. execFileSync
	// throws on any non-zero exit, so read stdout/status off the thrown error too.
	let stdout: string;
	try {
		// stdin=pipe (the text), stdout=pipe (the verdict), stderr=ignore so MOP's
		// "no active rules" notice can't leak into Pi's own stderr stream.
		stdout = execFileSync(mop, args, {
			input: text,
			encoding: "utf8",
			env: process.env,
			stdio: ["pipe", "pipe", "ignore"],
		});
	} catch (err: any) {
		if (err && err.status === 3) return null; // real error → fail-open (don't gate)
		if (err && typeof err.stdout === "string") stdout = err.stdout;
		else return null;
	}
	try {
		return JSON.parse(stdout) as Verdict;
	} catch {
		return null;
	}
}

export default function (pi: ExtensionAPI) {
	const mode = process.env.MOP_MODE || "log";

	pi.on("message_end", async (event, _ctx) => {
		const message: any = (event as any).message;
		if (!message || message.role !== "assistant") return;
		const text = extractText(message.content);
		if (!text.trim()) return;

		const verdict = runMopCheck(text, mode);
		if (!verdict) return; // fail-open: never withhold on our own error

		// log mode: observe + audit only. The mop CLI already wrote the audit
		// record (via MOP_AUDIT_LOG); we leave the message untouched.
		if (mode !== "enforce") return;

		// enforce mode: mutate the final message in place (see header note).
		if (verdict.verdict === "rewritten" && verdict.rewritten) {
			message.content = [{ type: "text", text: verdict.rewritten }];
		} else if (verdict.verdict === "rejected") {
			message.content = [{ type: "text", text: REDACTION_NOTICE }];
		}
	});
}
