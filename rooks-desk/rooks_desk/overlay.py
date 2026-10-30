"""The on-screen sign that Rooks Desk is in control: a thin cherry-red frame around every screen edge and a small bar
in the bottom-right corner saying so, with the current step and a Stop button. Runs as its own process (tkinter), so
it keeps drawing even while the agent is busy. Clicking Stop prints STOP, which the agent's kill switch reads.

The frame and bar are click-through where the OS allows and stay out of the window list the model sees."""

from __future__ import annotations

import subprocess
import sys
import threading

TITLE = "Rooks Desk overlay"
RED = "#B3132B"
INK = "#1C1B1A"
PAPER = "#FBF8F3"


class Overlay:
    """Parent side: start the overlay process, send it status lines, hear its Stop."""

    def __init__(self, on_stop, hotkey_label: str):
        self.proc = None
        self.on_stop = on_stop
        self.note = ""
        try:
            self.proc = subprocess.Popen([sys.executable, "-m", "rooks_desk.overlay", hotkey_label], stdin=subprocess.PIPE,
                                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1)
        except OSError as e:
            self.note = str(e)
            return
        threading.Thread(target=self._read, daemon=True, name="overlay-read").start()

    def _read(self):
        for line in self.proc.stdout:  # type: ignore[union-attr]
            if line.strip() == "STOP":
                self.on_stop("Stop pressed on the on-screen bar")
            elif line.startswith("NOTK"):
                self.note = line.strip()

    def status(self, text: str):
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.stdin.write(text.replace("\n", " ")[:120] + "\n")  # type: ignore[union-attr]
                self.proc.stdin.flush()  # type: ignore[union-attr]
            except (BrokenPipeError, OSError, ValueError):
                pass

    @property
    def running(self) -> bool:
        return bool(self.proc and self.proc.poll() is None)

    def close(self):
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.stdin.close()  # type: ignore[union-attr]
                self.proc.wait(2)
            except Exception:  # noqa: BLE001
                self.proc.kill()


def _main(hotkey_label: str) -> int:  # pragma: no cover - GUI
    try:
        import tkinter as tk
    except Exception:  # noqa: BLE001
        print("NOTK tkinter is not available; no on-screen bar", flush=True)
        for _ in sys.stdin:
            pass
        return 0
    root = tk.Tk()
    root.withdraw()
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    edges = []
    t = 4
    for (x, y, w, h) in ((0, 0, sw, t), (0, sh - t, sw, t), (0, 0, t, sh), (sw - t, 0, t, sh)):
        e = tk.Toplevel(root)
        e.title(TITLE)
        e.overrideredirect(True)
        e.geometry(f"{w}x{h}+{x}+{y}")
        e.configure(bg=RED)
        e.attributes("-topmost", True)
        _click_through(e)
        edges.append(e)
    bar = tk.Toplevel(root)
    bar.title(TITLE)
    bar.overrideredirect(True)
    bar.attributes("-topmost", True)
    bar.configure(bg=INK)
    frm = tk.Frame(bar, bg=INK, padx=12, pady=8)
    frm.pack()
    tk.Label(frm, text="●", fg=RED, bg=INK, font=("Helvetica", 12, "bold")).grid(row=0, column=0, rowspan=2, padx=(0, 8))
    tk.Label(frm, text=f"Rooks Desk is using your computer  ·  {hotkey_label} to stop", fg=PAPER, bg=INK,
             font=("Helvetica", 11, "bold")).grid(row=0, column=1, sticky="w")
    step = tk.StringVar(value="Starting…")
    tk.Label(frm, textvariable=step, fg="#C9C2B8", bg=INK, font=("Helvetica", 10), width=52, anchor="w").grid(row=1, column=1, sticky="w")

    def stop():
        print("STOP", flush=True)
        step.set("Stopping…")

    tk.Button(frm, text="Stop", command=stop, bg=RED, fg="white", activebackground="#8E0F22", activeforeground="white",
              relief="flat", font=("Helvetica", 11, "bold"), padx=14).grid(row=0, column=2, rowspan=2, padx=(12, 0))
    def place():
        bar.update_idletasks()
        bw, bh = bar.winfo_reqwidth(), bar.winfo_reqheight()
        bar.geometry(f"{bw}x{bh}+{sw - bw - 16}+{sh - bh - 48}")  # bottom-right, clear of the top-left kill corner

    place()
    root.after(200, place)

    def reader():
        for line in sys.stdin:
            root.after(0, step.set, line.strip())
        root.after(0, root.destroy)

    threading.Thread(target=reader, daemon=True).start()

    def keep_on_top():
        for w in (*edges, bar):
            try:
                w.lift()
            except tk.TclError:
                return
        root.after(1500, keep_on_top)

    keep_on_top()
    root.mainloop()
    return 0


def _click_through(win):  # pragma: no cover - GUI
    try:
        if sys.platform == "win32":
            import ctypes
            hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
            ex = ctypes.windll.user32.GetWindowLongW(hwnd, -20)
            ctypes.windll.user32.SetWindowLongW(hwnd, -20, ex | 0x80000 | 0x20)  # WS_EX_LAYERED | WS_EX_TRANSPARENT
        elif sys.platform == "darwin":
            win.attributes("-transparent", False)
    except Exception:  # noqa: BLE001
        pass


if __name__ == "__main__":  # pragma: no cover
    sys.exit(_main(sys.argv[1] if len(sys.argv) > 1 else "Ctrl+Alt+Esc"))
