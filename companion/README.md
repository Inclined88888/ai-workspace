# ROOKS Companion

Lets ROOKS use apps, its own browser and folders you choose on this computer. It is optional and off until you pair it.
It opens no port: it connects out to your ROOKS and asks for one action at a time. Read `docs/COMPUTER_USE.md`
for how it works and what it will never do.

```bash
cd ~/ai-workspace/companion
python3 -m rooks_companion pair http://localhost:3100 CODE    # CODE from ROOKS, under Computer
python3 -m rooks_companion permissions                        # macOS: opens Accessibility and Screen Recording settings
python3 -m rooks_companion allow-folder ~/Documents/Invoices  # optional: folders ROOKS may organise (it never deletes)
python3 -m rooks_companion run                                # Ctrl+C to quit
python3 -m rooks_companion stop                               # emergency stop, works without ROOKS; 'resume' to undo
```

Optional: `python3 -m pip install --user playwright && python3 -m playwright install chromium` for ROOKS's browser.

Platforms: macOS desktop control (built from macOS's own tools; not yet tested on a Mac in this release),
Linux X11 (tested on a virtual display), Windows (browser and folders only; desktop control not implemented).
