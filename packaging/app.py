"""Entry point for the standalone builds (PyInstaller).

Double-clicking the executable starts the local web UI and opens it in the browser.
Run from a terminal with arguments, it behaves like the `auto-paper-download` command.
"""
import sys

from auto_paper_download.__main__ import main

if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print("Auto Paper Harvester: starting the local page in your browser.")
        print("Keep this window open while you use it; close it to quit.\n")
        args = ["ui"]
    try:
        main(args)
    except KeyboardInterrupt:
        pass
