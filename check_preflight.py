"""Exercise preflight without installing packages or changing the host."""
import os
import pathlib
import subprocess
import unittest


ROOT = pathlib.Path(__file__).resolve().parent


class PreflightTests(unittest.TestCase):
    def run_preflight(self, uv=0, tools=0, manager="apt-get", fail=0, broken=0,
                      system="Linux", sudo=1, missing=""):
        script = r'''
set -Eeuo pipefail
SCRIPT_DIR=$PWD
source ./toolkit.sh
HOME=/tmp/toolkit-preflight-user
CT_HOME=$HOME/.codex
unset CODEX_HOME
uname() { printf '%s\n' "$SYSTEM"; }
id() { printf '1000\n'; }
pgrep() { return 1; }
command() {
  if [ "${1:-}" != -v ]; then builtin command "$@"; return; fi
  case "$2" in
    uv) [ "$HAS_UV" = 1 ] || return 1; printf 'uv\n' ;;
    make|cc|c++|gcc|g++)
      [ "$HAS_TOOLS" = 1 ] || return 1
      case "$MISSING:$2" in make:make|c:cc|c:gcc|cpp:c++|cpp:g++) return 1 ;; esac
      printf '%s\n' "$2" ;;
    apt-get|dnf|yum|pacman|zypper|apk) [ "$2" = "$MANAGER" ] || return 1; printf '%s\n' "$2" ;;
    sudo) [ "$HAS_SUDO" = 1 ] || return 1; printf 'sudo\n' ;;
    brew|curl|npm|node|git|python3|bun) printf '%s\n' "$2" ;;
    *) builtin command "$@" ;;
  esac
}
brew() {
  if [ "$1" = --prefix ]; then printf '/tmp/toolkit-brew\n'; return; fi
  printf 'BREW %s\n' "$*"
  [ "$FAIL_INSTALL" = 0 ] || return 1
  [ "$BROKEN_INSTALL" = 1 ] || HAS_UV=1
}
node() { return 0; }
sudo() { printf 'SUDO %s\n' "$*"; "$@"; }
install_system() {
  printf 'SYSTEM %s\n' "$*"
  [ "$FAIL_INSTALL" = 0 ] || return 1
  if [ "$BROKEN_INSTALL" != 1 ]; then HAS_TOOLS=1; MISSING=''; fi
}
apt-get() { install_system "$@"; }
dnf() { install_system "$@"; }
yum() { install_system "$@"; }
pacman() { install_system "$@"; }
zypper() { install_system "$@"; }
apk() { install_system "$@"; }
preflight
printf 'READY uv=%s tools=%s\n' "$HAS_UV" "$HAS_TOOLS"
'''
        env = dict(os.environ, HAS_UV=str(uv), HAS_TOOLS=str(tools),
                   MANAGER=manager, FAIL_INSTALL=str(fail), BROKEN_INSTALL=str(broken),
                   SYSTEM=system, HAS_SUDO=str(sudo), MISSING=missing)
        return subprocess.run(["bash", "-c", script], cwd=ROOT, env=env,
                              text=True, capture_output=True)

    def test_both_missing_are_installed(self):
        result = self.run_preflight()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("BREW install uv", result.stdout)
        self.assertIn("install -y build-essential", result.stdout)
        self.assertIn("READY uv=1 tools=1", result.stdout)

    def test_existing_tools_skip_installation(self):
        result = self.run_preflight(uv=1, tools=1)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("BREW install", result.stdout)
        self.assertNotIn("SUDO", result.stdout)

    def test_each_dependency_is_checked_independently(self):
        for uv, tools in [(0, 1), (1, 0)]:
            with self.subTest(uv=uv, tools=tools):
                result = self.run_preflight(uv=uv, tools=tools)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("READY uv=1 tools=1", result.stdout)

    def test_install_failure_stops_preflight(self):
        for uv, tools in [(0, 1), (1, 0)]:
            result = self.run_preflight(uv=uv, tools=tools, fail=1)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("READY", result.stdout)

    def test_successful_installer_must_provide_commands(self):
        for uv, tools in [(0, 1), (1, 0)]:
            result = self.run_preflight(uv=uv, tools=tools, broken=1)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("READY", result.stdout)

    def test_other_linux_package_managers(self):
        for manager in ["dnf", "yum", "pacman", "zypper", "apk"]:
            with self.subTest(manager=manager):
                result = self.run_preflight(uv=1, manager=manager)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("READY uv=1 tools=1", result.stdout)

    def test_unsupported_manager_stops_with_guidance(self):
        result = self.run_preflight(uv=1, manager="unknown")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("build", result.stderr.lower())

    def test_partial_build_toolchain_is_repaired(self):
        for missing in ["make", "c", "cpp"]:
            with self.subTest(missing=missing):
                result = self.run_preflight(uv=1, tools=1, missing=missing)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("install -y build-essential", result.stdout)

    def test_missing_sudo_stops_before_system_installation(self):
        result = self.run_preflight(uv=1, sudo=0)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("sudo", result.stderr)
        self.assertNotIn("SYSTEM", result.stdout)

    def test_macos_missing_tools_explains_command_line_tools(self):
        result = self.run_preflight(uv=1, system="Darwin")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("xcode-select --install", result.stderr)
        self.assertNotIn("SUDO", result.stdout)

    def test_macos_existing_toolchain_can_install_uv(self):
        result = self.run_preflight(tools=1, system="Darwin")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("BREW install uv", result.stdout)
        self.assertNotIn("SUDO", result.stdout)


if __name__ == "__main__":
    unittest.main()
