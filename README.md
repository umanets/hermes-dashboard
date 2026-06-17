# Hermes Tools — Management Dashboard + Module Catalog

A web app for **provisioning a client's [Hermes](https://) agent installation**, plus a
catalog of installable modules. The operator points the dashboard at a Hermes install
folder and, over the **local filesystem**, enables/disables skills and MCP tools and
injects per-client credentials — without depending on Hermes running.

## Layout (npm workspaces monorepo)

```
hermes-tools/
  apps/
    api/      NestJS backend — reads/writes config.yaml, .env, skills (filesystem)
    web/      Vue 3 + Vite + Pinia frontend
  modules/    catalog of installable modules (each has a hermes-manifest.yaml)
    apple-tidal/   (Apple Music + TIDAL MCP)   ← see note below
```

> **pnpm note:** the plan targeted pnpm, but corepack/pnpm is blocked in this environment
> (`EPERM` on the nvm dir), so the repo uses **npm workspaces** instead — functionally
> equivalent for `apps/*`.

> **apple-tidal relocation is deferred:** moving `apple-tidal/` into `modules/` is pending
> because the live Hermes `music` MCP server still has it open. The catalog scanner is
> zero-config: it finds `apple-tidal` at the repo root for now, and will pick it up under
> `modules/` once moved. To move it: close Hermes (frees the file lock), then
> `git mv apple-tidal modules/apple-tidal` and update the `music` server path in
> `config.yaml` (or just reinstall it via the dashboard).

## Run (dev)

```bash
npm install
npm run dev:api      # NestJS on http://127.0.0.1:4000
npm run dev:web      # Vite dev server on http://127.0.0.1:5180 (proxies /api -> :4000)
```

Open the web app, paste a Hermes install path (e.g. `C:/Users/<you>/AppData/Local/hermes`),
Connect, then use the **Skills / MCP Servers / Catalog** tabs. Changes apply on the next
Hermes restart.

## Run from scratch (production — Ubuntu or Windows)

The code is cross-platform. `npm start` builds everything and runs a **single process**: the
API serves the built web UI, so the whole dashboard is at `http://127.0.0.1:4000`.

### Full sequence (Ubuntu, copy-paste)

```bash
# 1) Install prerequisites (once) — Node 20+, Docker, git, python
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo bash - && sudo apt install -y nodejs
sudo apt install -y docker.io git python3 python3-venv python3-pip

# 2) Add yourself to the docker group so `docker` runs without sudo, then RE-LOGIN
sudo usermod -aG docker "$USER"
newgrp docker            # or just log out/in
ssh -T git@github.com    # confirm SSH access to the private hermes-agent repo

# 3) Get the code + configure
git clone <repo-or-bundle> hermes-tools && cd hermes-tools
cp .env.example .env
sed -i "s/^HERMES_UID=.*/HERMES_UID=$(id -u)/; s/^HERMES_GID=.*/HERMES_GID=$(id -g)/" .env
nano .env                # set CLIENTS_ROOT and OPENROUTER_API_KEY

# 4) Install, build, run (single process on :4000)
npm install
npm start
```

The numbered steps below explain each part.

**1. Prerequisites**

```bash
# Node 20+
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo bash - && sudo apt install -y nodejs
# Docker engine (+ run docker without sudo), git, python (for host-side module installs / Apple auth)
sudo apt install -y docker.io git python3 python3-venv python3-pip
sudo usermod -aG docker "$USER"     # log out/in so `docker` works without sudo
ssh -T git@github.com               # SSH access to the private hermes-agent repo (for image build)
```

**2. Configure** — copy `.env.example` to `.env` (in the repo root) and fill it in. On **Linux set
`HERMES_UID`/`HERMES_GID` to your own** (`id -u` / `id -g`) so container-written volume files stay
editable. `OPENROUTER_API_KEY` is required to run the Hindsight memory service.

**3. Install, build & run**

```bash
npm install        # do NOT copy node_modules/dist/.venv across machines — rebuild here
npm start          # build api+web, then serve the dashboard on :4000
# (after the first build, `npm run serve` skips the rebuild)
```

**4. In the dashboard** (Agents screen): **Build image** (clones hermes-agent over SSH — slow once)
→ **Start** the *Memory service (Hindsight)* → **+Agent** → configure it → **Start Background
Service** → **Enable Hindsight memory**.

> **Copying to a new machine:** move the **source only** (a `git` clone/bundle is easiest — the
> `.gitignore` already excludes `node_modules/`, `dist/`, `.venv/`, `.env`, `*.p8`). Don't migrate
> Docker containers — recreate agents on the new host. If you use the `music` module, rebuild its
> venv on the new OS: `cd apple-tidal && python3 -m venv .venv && .venv/bin/pip install -e .`.

## Write safety

Every config/.env write goes through one chokepoint with: an advisory write lock
(`proper-lockfile`), a timestamped `.bak` backup (Hermes' convention), and an atomic
temp-file→fsync→rename (so Hermes never reads a half-written file). The full YAML document
is round-tripped — **unmanaged sections are never dropped**. Secrets live only in `.env`
(UTF-8, no BOM, written verbatim) and are never logged or returned by the API.

## Test

```bash
npm test     # api unit + integration tests (offline, temp fixtures)
```

## v1 scope / not yet

In: connect one install; list+toggle skills; list+toggle MCP servers and their tools;
browse catalog; install a module (credential form → `.env` + `mcp_servers` block).
Out (future): multi-client registry & profiles; Telegram/gateway provisioning; remote
(non-filesystem) access; auto-running module `bootstrap`; installing skills from the catalog.
