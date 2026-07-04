# What is Seatbelt/bwrap?

In the context of Node.js, **Seatbelt** and **bwrap** are not built-in JavaScript modules or core Node APIs. Instead, they are **OS-level sandboxing primitives** that modern Node.js applications—particularly AI coding assistants and CLI tools—rely on to safely execute untrusted code or commands via child processes.

Because tools like AI agents (e.g., Claude Code, OpenAI Codex, or the Mastra framework) need to run terminal commands on your local machine, they use these tools to isolate the execution environment and prevent untrusted scripts from accessing your entire filesystem, modifying system settings, or leaking secrets.

Here is a breakdown of what each tool is and how they function:

### 1. Seatbelt (macOS)

Seatbelt is the internal codename for macOS's native application sandboxing mechanism.

* **How it works:** It uses the `sandbox-exec` command along with dynamically generated configuration profiles (Seatbelt profiles) written in a Scheme-like language.
* **What it does:** It intercepts system calls at the kernel level. When a Node.js app spawns a sandboxed process on a Mac, Seatbelt acts as the bouncer, strictly enforcing which specific directories the process can read or write to (e.g., allowing access to the current project folder but blocking `~/.ssh` or `/etc`), and restricting network access to specific localhost ports.

### 2. bwrap / Bubblewrap (Linux & WSL2)

Bubblewrap (`bwrap`) is a core sandboxing tool for Linux environments.

* **How it works:** It creates unprivileged, restricted environments using Linux user and mount namespaces.
* **What it does:** Instead of requiring Docker or root access, `bwrap` spins up a lightweight, isolated container for a single process. Node applications use it to bind-mount only the necessary project files, shadow sensitive files (by mounting an empty temporary filesystem over them), and remove network namespaces so the sandboxed code cannot make unauthorized external web requests.

---

### How They Are Used in the Node.js Ecosystem

If you are looking at packages like `@anthropic-ai/sandbox-runtime` or building a secure local execution environment, Node acts as the orchestrator. The workflow generally looks like this:

1. **Configuration:** The Node app defines a strict policy (e.g., "Allow read/write only in `./workspace`, block network access").
2. **Detection:** The app detects the host OS. If it is macOS, it generates a Seatbelt profile. If it is Linux, it builds an array of `bwrap` arguments.
3. **Execution:** Node's `child_process.spawn()` is used to execute the command, but instead of running `bash script.sh` directly, it wraps it:
* *macOS:* `spawn('sandbox-exec', ['-f', 'profile.sb', 'bash', 'script.sh'])`
* *Linux:* `spawn('bwrap', ['--ro-bind', '/', '/', '--bind', './workspace', './workspace', 'bash', 'script.sh'])`

### Summary Comparison

| Feature | Seatbelt | bwrap (Bubblewrap) |
| --- | --- | --- |
| **Target OS** | macOS | Linux, WSL2 |
| **Underlying Tech** | Kernel-level syscall interception | Linux namespaces (User, Mount, Network) |
| **CLI Implementation** | `sandbox-exec` | `bwrap` |
| **Setup Required** | Built-in by default | Usually requires package installation (e.g., `apt install bubblewrap`) |

In short, if you see these terms in a Node repository, the developers are implementing secure, native, OS-level containment to ensure that spawned child processes cannot escape their designated boundaries.
