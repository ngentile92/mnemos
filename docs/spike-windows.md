# Spike: Mnemos on Windows (feasibility)

**Verdict: feasible for local mode (the Mac app path) with ~1–2 weeks of work. The Docker hub stays Linux/macOS.**

| Piece | Windows status | Work |
|---|---|---|
| `mnemos-local` (Python, SQLite, FastMCP, uvicorn) | Pure Python, no POSIX-only deps | Path/permission fixes: `os.chmod(0o600/0o700)` is a no-op on Windows; use ACLs or accept per-user profile protection. Test in CI (`windows-latest`) |
| Bundled runtime | python-build-standalone ships Windows builds | `bundle_python.ps1` twin of `bundle_python.sh`; `python.exe` path in `hub_program()` |
| Tauri app | Tauri 2 supports Windows (WebView2, MSI/NSIS) | `--bundles nsis`; tray works; replace LaunchAgent with the `HKCU\…\Run` key or `tauri-plugin-autostart` |
| Token storage | `security-framework` is macOS-only | Windows Credential Manager via the `keyring` crate (cfg-gated) |
| Claude Desktop config | `%APPDATA%\Claude\claude_desktop_config.json` (already handled in `claude_config_path`) | Test |
| `.mcpb` | Claude Desktop on Windows supports extensions; uv-type bundle is cross-platform | Test |
| Signing | Unsigned NSIS shows SmartScreen "More info → Run anyway" | Optional: code-signing cert (~US$100–400/yr) or Azure Trusted Signing |
| Docker hub (Cognee/Infisical/Tailscale) | Only via WSL2 + Docker Desktop | Not planned; document WSL2 as "advanced" |

Plan: (1) run `gateway/tests` on `windows-latest` and fix path/chmod issues; (2) bundle Python + NSIS build in CI;
(3) Credential Manager + autostart; (4) a friend test like `scripts/friend_test_mac.sh` in PowerShell.
Risks: antivirus flagging an unsigned bundled Python; long paths in site-packages (enable or keep the tree shallow).
