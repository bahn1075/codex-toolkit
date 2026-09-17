# Toolchain

- Shell entrypoints target Bash 3.2+ on macOS/Linux; installation requires Homebrew and runs as the ordinary user.
- Node.js 22+ with npm; npm packages live under `~/.codex/toolkit/npm`.
- Homebrew Python 3.13 backs an isolated uv environment under `~/.codex/toolkit`; `tomlkit`, Mem0, MCP, and Oracle DB dependencies run there.
- Tool distribution: Homebrew (Codex, Kubernetes MCP), uv (Serena, Headroom), npm (Graft, Context7), Git checkout (Ponytail), Codex marketplace (Superpowers).
- Graft native parsers require a C/C++ compiler and make; on macOS use Xcode Command Line Tools.
- Mem0 depends on Oracle AI Vector Search plus the configured Tailscale model server.