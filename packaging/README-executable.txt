Auto Paper Harvester - standalone version
=========================================

No Python needed. Double-click "auto-paper-harvester" (Windows: auto-paper-harvester.exe).
A small window opens and your browser shows the local page. Keep the window open while
you use the page; close it to quit.

First run on macOS
  macOS blocks apps from unidentified developers. Double-click once, click "Done",
  then open System Settings > Privacy & Security, scroll down and click "Open Anyway".
  Or, in Terminal:  xattr -d com.apple.quarantine /path/to/auto-paper-harvester

First run on Windows
  If SmartScreen says "Windows protected your PC", click "More info" > "Run anyway".

Linux
  chmod +x auto-paper-harvester && ./auto-paper-harvester

Settings and API keys are saved in ~/.auto-paper-download/.env (only readable by you).
PDFs go to ~/Downloads/papers unless you choose another folder on the page.

The executable also works as the command-line tool:
  auto-paper-harvester get 10.5194/npg-20-705-2013 -o papers
  auto-paper-harvester doctor

Your-own-Chrome step still needs Node.js:  npm i -g @playwright/cli
Docs, source and issues: https://github.com/jxtse/auto-paper-harvester
License: MIT (see LICENSE)
