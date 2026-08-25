# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Two modes of work — pick the right one

- **Building an app *with* Reachy Mini** (user wants a robot app, JS or Python): read [AGENTS.md](AGENTS.md) first. It is the full app-development guide (app flavours, SDK usage, `plan.md` rule, safety limits) and points into skills in `C:\Users\EGO\Documents\reachy mini\reachy_mini\skills` (or [skills/](skills/)) and [ts/APP_CREATION_GUIDE.md](ts/APP_CREATION_GUIDE.md).
- **Using Skills:** You can use and reference all guide skills located at `C:\Users\EGO\Documents\reachy mini\reachy_mini\skills/` (e.g. `create-app.md`, `create-js-app.md`, `control-loops.md`, `motion-philosophy.md`, `safe-torque.md`, `ai-integration.md`, `rest-api.md`, etc.).
- **Working *on* this repository** (the SDK, daemon, or JS SDK itself): the rest of this file.

## Commands

> **CRITICAL RULE:** All future dependencies and Python packages **MUST** be installed inside the virtual environment (`reachy_mini_env`). Never install packages to global Python.
> - Activate virtualenv: `reachy_mini_env\Scripts\activate`
> - Install dependencies: `uv pip install <package_name>`

Python (uv-based; CI runs `uv sync --frozen --all-extras --group dev`):

```bash
# Virtual Environment & Dependencies
reachy_mini_env\Scripts\activate      # Always activate virtual environment first (Windows)
uv pip install "reachy-mini"          # Install base package
uv pip install "reachy-mini[mujoco]"  # Install with MuJoCo simulation support
uv pip install <package_name>         # Install any future packages inside this env
uv sync --all-extras --group dev      # match CI's environment (mypy results depend on it)
uv run pytest                         # unit tests only (testpaths = tests/unit_tests)
uv run pytest tests/unit_tests/test_daemon.py::test_name -vv   # single test
uv run pytest -m "audio"              # hardware-marked subsets: audio, respeaker, loopback, video, wireless, webrtc
uv run pytest -m 'not audio and not video and not wireless and not webrtc'   # what CI runs by default
uv run ruff check . && uv run ruff format .   # ruff 0.12.0 pinned in CI + pre-commit
uv run mypy                           # strict, over src/ and examples/
pre-commit install                    # ruff check + format on staged files
```

`tests/integration_tests/` are manual scripts against a physical robot, not collected by pytest.

TypeScript SDK (from `ts/`):

```bash
npm run test        # vitest
npm run typecheck   # sdk + host
npm run build       # tsc for the SDK, vite + tsc for the host shell
npm run dev         # vite dev server for the host shell
```

Running the daemon locally:

```bash
uv run reachy-mini-daemon --mockup-sim     # no hardware, no MuJoCo
uv run reachy-mini-daemon --sim            # MuJoCo simulation
uv run reachy-mini-daemon                  # real robot (auto-detects serial port)
# useful flags: --fastapi-port, --log-level DEBUG, --kinematics-engine {AnalyticalKinematics,Placo,NN}, --check-collision
```

**If you change a daemon route, model, or route docstring**, regenerate the API spec and commit it — CI fails on drift:

```bash
uv run python scripts/generate_openapi.py   # writes docs/source/API/openapi.json
```

## Architecture

Everything goes through a **daemon** (FastAPI + uvicorn on port 8000). No client talks to motors directly.

```
Python SDK (ReachyMini)  ─┐
JS SDK (browser, WebRTC) ─┼─> daemon ─> Backend ─> 50 Hz control loop ─> motors
REST / WebSocket clients ─┘
```

**Transports converge on one handler.** REST routes, `/ws/sdk`, and the WebRTC data channel are sibling transports into the same `Backend.process_command()`. `src/reachy_mini/io/protocol.py` is the single source of truth for the wire protocol: every command/message is a pydantic model in a `{"type": ..., ...}` envelope. Adding a command means touching `protocol.py` + the backend handler, and both transports get it.

**Layers:**

- `src/reachy_mini/reachy_mini.py` — the user-facing Python SDK class. Thin: builds protocol commands and sends them over `io/ws_client.py`. Also owns connection discovery (localhost → mDNS → host fallback, see `utils/discovery.py`).
- `src/reachy_mini/daemon/app/` — FastAPI layer. `main.py` (CLI + app assembly), `routers/` (one file per REST area), `services/` (bluetooth, wireless, gpio_shutdown — Linux/wireless only). Routers mostly *forward* to the backend and do not clamp.
- `src/reachy_mini/daemon/backend/` — `abstract.py` defines the shared state + command handling; `robot/`, `mujoco/`, `mockup_sim/` are the three implementations. The robot backend runs a **50 Hz control loop** (`_update()`) that writes the *previous* iteration's IK result to the motors, then reads state and recomputes IK if `ik_required`. Commands only mutate shared state; the loop is what drives hardware. Head tracking blending, speech wobbler offsets, and clamping all happen inside that loop.
- `src/reachy_mini/kinematics/` — three interchangeable IK engines: `analytical_kinematics.py` (default), `nn_kinematics.py` (ONNX), `placo_kinematics.py` (optional extra).
- `src/reachy_mini/media/` — the daemon owns camera and audio via `GstMediaServer` (GStreamer). Clients choose LOCAL (IPC: `unixfdsrc` / `win32ipcvideosrc`), WEBRTC, or NO_MEDIA through `MediaManager`. Never open the hardware device directly from a client.
- `src/reachy_mini/apps/` — the app store: install/run HF Space apps (`sources/hf_space.py`), HF OAuth (`sources/hf_auth.py`), one-app-at-a-time lifecycle (`manager.py`, `daemon/robot_app_lock.py`).
- `src/reachy_mini/daemon/jsonrpc_relay.py` — JSON-RPC over WebRTC/WS: `apps.*` handled locally against `AppManager`; every other namespace relayed to the running app's `/rpc` WebSocket, whose notifications are re-broadcast to all clients.
- `ts/` — the published npm package `@pollen-robotics/reachy-mini-sdk`. `lib/` is the WebRTC runtime SDK, `host/` is the HF Spaces host shell (OAuth + robot picker + iframe bridge, exported as `./host`), `animation/` is client-side motion math that mirrors the daemon's duration scaling.

**Platform variants** shape a lot of the code: Lite (USB, daemon on the laptop), Wireless (onboard CM4, `--wireless-version`, binds 0.0.0.0, extra BLE/WiFi/IMU/GPIO services), and simulation. Guard platform-specific imports and dependencies the way `pyproject.toml`'s extras do.

## Conventions

- Ruff lints with `I` (import sort) and `D` (docstrings) enabled — every module/class/function needs a docstring. `tests/` and `src/reachy_mini/__init__.py` are excluded.
- mypy is `strict` over `src/` and `examples/`.
- `pyproject.toml` dependency pins carry load-bearing comments (e.g. the `huggingface-hub>=1.20.1` floor exists because private internals are imported, guarded by `tests/unit_tests/test_hf_hub_private_api_contract.py`). Read the comment before changing a pin.
- `uv.lock` is checked by CI (`uv-lock-check.yml`); `[tool.uv] exclude-newer = "7 days"` means dependency resolution deliberately lags.
- Commits/PRs: `type(scope): what it does`. One PR = one issue. See [docs/contributing.md](docs/contributing.md) for the full PR/issue rules.
