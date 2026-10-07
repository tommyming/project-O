# O — Design Specification

**Date:** 2026-10-08
**Status:** Approved design, pre-implementation
**Project:** `project-O` — working name **"O"** (rename at any time; only the config paths below encode it)
**Path:** architectural (new project, full OS image)

---

## 1. Overview

O is a Linux-kernel-based operating system for x86_64 laptops/PCs whose **entire
user experience is a chat window**. From the moment the machine boots there is
no desktop, no terminal, and no login shell in the traditional sense — the user
talks to a small **local LLM** that acts as a personal assistant and, through a
confirm-then-execute loop, installs and configures the software the user asks
for. The model runs fully on the machine; the OS never requires network access.

O is built **from scratch** with Buildroot: we own the kernel config, the libc,
the init system, the root filesystem, and the package corpus.

### Purpose

Research / demo prototype exploring the "**LLM as the OS shell**" concept.
Interaction quality and concept fidelity matter more than robustness,
hardening, or release engineering.

### Success criteria

1. O boots in QEMU and on a real x86_64 laptop (16 GB+ RAM).
2. First boot presents an account-creation form; exactly one account can ever
   exist; subsequent boots require its password.
3. The boot-to-chat flow is: kernel → init → chat window. Nothing else is
   ever visible.
4. The assistant can search, install, and remove software from a local
   on-image package repository, with explicit user confirmation before any
   execution.
5. The whole system works fully offline.
6. If the model runtime dies, the OS stays usable (slash commands, reboot,
   logs, rescue shell).

## 2. Non-goals (explicitly out of scope)

- Multi-user support, account management beyond the single first-boot account.
- Cloud/remote model fallback. No network dependency anywhere.
- GUI / Wayland / X stack. The TTY TUI is the only interface.
- ARM / SBC support (may come later; not now).
- Runtime compilation of packages (no on-device gcc builds). The local repo is
  the only software source.
- Security hardening, secure boot, sandboxing beyond doas + confirmation.
- Package signing and tamper-proofing.

## 3. Requirements summary

| Requirement | Decision |
|---|---|
| Target hardware | x86_64 laptop/PC, 16 GB+ RAM, CPU-only inference |
| Primary dev target | QEMU x86_64 (`qemu-system-x86_64`); real hardware is final validation |
| Boot experience | Full-screen chat TUI on tty1; no getty, no desktop |
| Action model | Confirm-then-execute agent loop |
| Model | Fully local, offline, small (~4B), baked into the image |
| Base system | Buildroot 2026.08.x, musl libc, BusyBox init, ext4 read-write rootfs |
| Accounts | Exactly one, created on first boot, real Linux user (UID 1000) |
| Privilege | Single user wields root via passwordless doas; root login disabled |

## 4. System architecture

### 4.1 Boot chain

```
power → BIOS/UEFI → bootloader (syslinux or GRUB, Buildroot-generated)
      → Linux kernel (mainline 6.x, x86_64)
      → ext4 read-write rootfs
      → BusyBox init (PID 1)
      → inittab: tty1 respawns o-init
```

**First boot:**

```
o-init (root) → no account flag at /var/lib/o/account
   → setup form (username, password, confirm)
   → busybox adduser (UID 1000, home /home/<name>)
   → write one-time flag /var/lib/o/account
   → exec chat UI as the new user
```

**Every subsequent boot:**

```
o-init (root) → account flag present
   → password prompt on tty1 → busybox login (SHA-512 shadow auth)
   → user's login shell is /usr/bin/chat → chat UI starts as the user
```

Chat history persists per-user at `~/.local/share/o/`.

### 4.2 Single-account enforcement — three layers

1. **One creation path.** The first-boot form is the only account-creation
   mechanism in the OS. It runs if and only if `/var/lib/o/account` does not
   exist. The flag is written *after* successful user creation; `o-init`
   detects and repairs half-states (e.g., user exists but flag missing after
   power loss during setup).
2. **No side doors.** No getty on any tty (tty1 runs `o-init`; tty2+ unused by
   default). No sshd in the base image (openssh exists only as an *optional
   install* the user may choose). `adduser`/`useradd` applets are removed from
   BusyBox at build time.
3. **Privilege model.** Root cannot be logged into (no password, shell
   `/sbin/nologin`). The single account is a regular user with passwordless
   doas; the chat's y/n confirmation on privileged actions is the single
   authorization gate.

### 4.3 Maintenance / escape paths

| Need | Path |
|---|---|
| Raw root shell | `/root` slash-command in the TUI → `doas -s` shell on tty2 (deterministic, no LLM) |
| Reboot / logs / about | `/reboot`, `/logs`, `/about` slash-commands (TUI-handled) |
| Broken boot | Kernel cmdline `init=/bin/sh` at the bootloader prompt |

### 4.4 Process model

```
init (PID 1, BusyBox)
├─ o-init (root, tty1, respawn)      — first-boot form OR login
│  └─ login → /usr/bin/chat          — privilege dropped
│     ├─ HTTP → llama-server on 127.0.0.1:8080
│     └─ confirmed commands (doas when privileged)
└─ llama-server (respawn, runs as unprivileged user, reads /opt/models) — loads GGUF at boot, before login
```

If the chat app crashes, inittab respawns it within ~1 s. If llama-server
dies, inittab respawns it; the TUI shows a "brain offline" banner and
slash-commands keep working.

## 5. Assistant stack

### 5.1 Chat UI (`chatd` — Python 3 + Textual, runs as the user on tty1)

- Message history pane (scrollable), streaming assistant output, input box,
  status line (model state, tok/s).
- **Confirmation cards:** every proposed command renders as a distinct block —
  command text, one-line explanation, privilege badge (PRIVILEGED when run via
  doas), package count/size when applicable, `[y] run / [n] decline` keys.
- **Slash commands** handled by the TUI directly, never routed through the
  model: `/root`, `/reboot`, `/logs`, `/about`.
- Esc cancels in-flight generation.

### 5.2 Tool set (exactly six; small models degrade with more)

| Tool | Privileged | Confirmed |
|---|---|---|
| `run_command(cmd, explanation)` | if needed | always |
| `search_packages(query)` | no | no |
| `install_packages(names)` | yes | always |
| `remove_packages(names)` | yes | always |
| `list_installed()` | no | no |
| `sys_info()` | no | no |

### 5.3 Agent loop

```
user msg → model (system prompt + tool defs + history)
  ← streams text and/or exactly one tool call
  → confirmation card (if tool call)
     y → execute (doas if privileged), capture stdout/stderr
     n → model told "user declined", re-plans
  → tool result fed back → model continues
  → repeat until final text answer or a limit is hit
```

Limits: max **8 tool calls per user turn**, **60 s command timeout**, **4 KB
output truncation** fed back to the model. Model identity is set by the system
prompt: the OS's name, the user's account name, tool rules (one tool call at a
time, always explain before proposing, never invent packages not in the repo).

### 5.4 Reliability & safety for small models

1. **Grammar-constrained tool calls** — llama.cpp GBNF grammar forces tool-call
   output to valid JSON matching each tool's schema.
2. **Retry budget** — malformed calls are fed back as errors; max 2 retries,
   then an honest "I can't do that" fallback.
3. **Static blocklist** — catastrophic patterns (`rm -rf /`, `mkfs`, `dd` to
   raw block devices, fork bombs, `chmod -R /`…) are refused **even if the
   user confirms**.

### 5.5 Model & runtime

- **Runtime:** llama.cpp `llama-server`, serving an OpenAI-compatible API on
  `127.0.0.1:8080` with `--jinja` (native chat template → correct tool-call
  format). Started at boot before login; loads the GGUF once. Note: the
  upstream Buildroot `llama-cpp` package (added 2025.11) may build only the
  library/CLI — a small overlay package builds `llama-server` with
  AVX/OpenMP flags if needed.
- **Default model:** Qwen3.5-4B-Instruct, Q4_K_M GGUF (~2–2.5 GB), Apache 2.0.
- **Model selection spike (first implementation task):** scripted tool-calling
  scenarios run against Qwen3.5-4B, Agents-A1-4B (InternScience, agentic
  specialist, official GGUFs; verify license), and Gemma 4 E4B. Scored on
  valid-JSON rate, correct tool choice, install-task completion. Winner ships.
- **Swappability:** the GGUF path lives in `/etc/o/config`; swapping models is
  a config edit + reboot, not a rebuild. One model is baked per image.
- Performance expectation: ~10–25 tok/s CPU generation on the target class;
  streaming makes this acceptable.

## 6. Package system

Buildroot selects packages at compile time; O needs runtime installation.
Three pieces solve this:

### 6.1 `.opk` format

At build time, Buildroot's per-package target directories
(`BR2_PER_PACKAGE_DIRECTORIES=y`) isolate each package's installed files. A
capture script produces, per package:

```
name-version-x86_64.opk = tar.zst(payload files) + manifest.json
manifest.json: { name, version, description, deps[], files[], sha256, size,
                 post_install?: string }
```

Dependencies are derived from Buildroot's dependency graph — build-time deps
used as an approximation of runtime deps. Documented limitation.

### 6.2 Local repository

`/opt/o-repo/` on the rootfs, populated at image build time with ~100–200
pre-built popular packages (git, vim, htop, ffmpeg, tmux, openssh, python
extras, …) plus `index.json` (name, version, description, size) for search.
**Fully offline.** If a package isn't in the repo, the assistant says so —
the system prompt forbids inventing packages.

### 6.3 `o-pkg` installer

~300 lines of Python shipped in the base image, invoked by the agent (and
manually via `/root` shell if desired):

- `o-pkg search <query>` — search `index.json`
- `o-pkg install <pkgs…>` — topological dep-resolution, preflight disk-space
  check, extraction to `/`, post-install hooks, ledger update
- `o-pkg remove <pkgs…>` — delete files per manifest, reverse-order
- `o-pkg list` / `o-pkg info <pkg>`
- Install ledger at `/var/lib/o-pkg/installed.json`; best-effort rollback
  (delete files extracted so far) on failure.

### 6.4 Known limitations (accepted)

- Dep approximation may over- or under-install; over-install is harmless,
  under-install surfaces as a runtime error the assistant can react to
  (install the missing dep).
- No version upgrades / downgrade machinery beyond remove+install.
- No signatures; the repo is baked, tamper-proofing is out of scope.

## 7. Build pipeline

### 7.1 Repository layout

```
project-O/
├── buildroot/          # git submodule, pinned 2026.08.x
├── br2-external-o/     # the OS as a Buildroot external tree
│   ├── configs/o_defconfig
│   ├── package/o-init/        # first-boot setup form (Textual, runs as root) + login orchestration
│   ├── package/o-chatd/       # the chat TUI app
│   ├── package/o-pkg/         # installer
│   ├── package/llama-server/  # overlay if upstream package lacks server bin
│   └── board/o/               # rootfs overlay, post-build scripts, inittab
├── app/                # chatd source (Python/Textual)
├── tools/
│   ├── build-image.sh  # full pipeline, runs on Linux or in container
│   ├── make-opk.sh     # .opk capture from per-package dirs
│   └── download-model.sh  # GGUF fetch, hash-pinned, cached
└── docs/superpowers/specs/
```

### 7.2 Build flow

The development host is macOS; Buildroot requires Linux. All builds run in a
Linux container (Docker/Podman) or natively on CI Linux runners:

1. `make` Buildroot with `o_defconfig` (via `BR2_EXTERNAL`).
2. `make-opk.sh` captures `.opk` files from per-package dirs into the repo
   overlay (`/opt/o-repo/`).
3. `download-model.sh` fetches the pinned GGUF into `/opt/models/`.
4. Assemble `rootfs.ext4` + `bzImage` → QEMU run script and a `dd`-able raw
   disk image for real hardware.

### 7.3 Developer loop

- **chatd iteration (the common case):** run QEMU with `app/` mounted via
  9p/virtfs; edit on macOS, restart chatd inside QEMU. No image rebuild.
- **Package/config changes:** incremental Buildroot rebuild in container.
- **Model change:** edit `/etc/o/config`, restart llama-server.

## 8. Error handling

| Failure | Behavior |
|---|---|
| llama-server dies / OOM | inittab respawns; TUI "brain offline" banner; `/logs`; slash-commands still work |
| Garbage tool call | GBNF grammar → ≤2 retries → honest fallback text |
| Command fails (non-zero exit) | exit code + stderr fed back; model explains / re-plans |
| Install fails mid-way | rollback of extracted files; ledger stays consistent |
| Disk full | preflight check before install; clear error to model/user |
| Power loss during first-boot | flag written after user creation; `o-init` repairs half-states on next boot |
| Chat app crash | inittab respawn (<1 s), session state reloaded from disk |
| Won't boot | `init=/bin/sh` rescue at bootloader |

## 9. Testing strategy

- **Unit (host, pytest):** `o-pkg` dep resolution + rollback; agent loop
  against a **mock llama-server** (streaming, tool-call shapes, malformed
  calls); blocklist matcher.
- **Integration (QEMU, scripted via serial console):** boot → first-boot form
  → login → "install htop" → confirm → assert binary present. Runs in CI.
- **Model spike:** as §5.5 — scripted scenarios × 3 candidate models; winner
  is baked.
- **CI:** GitHub Actions Linux runners: image build (native) + QEMU boot test
  on every commit.
- **Final:** manual test matrix on the real target laptop (boot, WiFi hardware
  present but offline flow, install flows, crash-recovery flows).

## 10. Key decisions & rationale

| Decision | Rationale |
|---|---|
| Buildroot over Debian remaster | From-scratch ownership chosen deliberately; demo timeline not a constraint |
| Custom `.opk` + `o-pkg` over apk-tools | apk repo generation from Buildroot exceeds the effort of a small installer; prototype-appropriate |
| musl + BusyBox init | Minimal, fast, honest "own OS"; upstream llama-cpp + python-textual both available in Buildroot |
| BusyBox `login` for auth (not Python) | Python ≥3.13 removed the `crypt` module; busybox login does SHA-512 shadow auth natively |
| ext4 read-write rootfs | Runtime package installs require writable `/` |
| QEMU-first development | macOS host cannot run Buildroot; QEMU + 9p gives a fast edit-run loop |
| Confirm-then-execute + blocklist | Human gate for every action; hard floor under it for demo survivability |
| Single model baked, config-swappable | Keeps "one brain" concept; cheap A/B via spike; swap is config + reboot |

## 11. Risks & open items

1. **llama-cpp Buildroot package scope** — may not build `llama-server`
   binary; overlay package is the planned remedy (small).
2. **Small-model tool-call reliability** — mitigated by GBNF + retries +
   tiny tool set; the spike quantifies it before anything else is built.
3. **Agents-A1-4B license** — verify before baking; Qwen3.5-4B (Apache 2.0)
   is the safe default.
4. **Dep approximation in `.opk`** — accepted; over-install harmless,
   under-install recoverable via assistant.
5. **Real-hardware quirks** (WiFi firmware, laptop-specific kernels) —
   deferred to final validation phase; QEMU carries the prototype.
6. **Bootloader choice** (syslinux vs GRUB, BIOS vs UEFI) — resolved during
   implementation; target machines of interest are UEFI-era laptops.
