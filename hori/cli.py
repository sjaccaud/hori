"""HORI CLI — command-line interface for HORI utilities.

Usage:
  hori init      Detect hardware and create config (setup wizard)
  hori detect    Detect hardware and recommend a model tier
"""
import sys


def main():
    args = sys.argv[1:] if len(sys.argv) > 1 else []

    if not args or args[0] in ("-h", "--help", "help"):
        print("HORI — local-first agent runtime with a safety spine")
        print()
        print("Usage: hori <command>")
        print()
        print("Commands:")
        print("  init      Detect hardware and create config (setup wizard)")
        print("  detect    Detect hardware and recommend a model tier")
        print()
        sys.exit(0)

    cmd = args[0]
    rest = args[1:]

    if cmd == "init":
        from hori.init import run_init
        force = "--force" in rest
        quiet = "--quiet" in rest
        success = run_init(force=force, quiet=quiet)
        sys.exit(0 if success else 1)

    elif cmd == "detect":
        from hori.detect import detect, format_report
        result = detect()
        print(format_report(result))

    else:
        print(f"Unknown command: {cmd}")
        print("Run 'hori --help' for available commands.")
        sys.exit(1)


if __name__ == "__main__":
    main()
