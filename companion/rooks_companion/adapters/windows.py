"""Windows adapter: the contract only. Not implemented, and reported as unsupported rather than half working.

Planned mapping onto Microsoft UI Automation (UIA), the Windows accessibility API:
  permissions()   UIA needs no prompt for the user's own session; elevated (admin) windows cannot be driven from a
                  normal process (UIPI) and must be reported as such, never worked around.
  windows()       IUIAutomation.GetRootElement + FindAll(TreeScope_Children, ControlType Window), Name, ProcessId.
  active_app()    GetForegroundWindow -> process image name.
  elements(app)   Walk the focused window with a control view TreeWalker, depth and count bounded like macOS;
                  role = ControlType, label = Name, value = ValuePattern.Value (never for IsPassword elements),
                  path = RuntimeId (stable while the element lives).
  press(el)       InvokePattern.Invoke(), else TogglePattern / SelectionItemPattern, else click the bounding rectangle centre.
  type_text()     ValuePattern.SetValue when the element supports it, else SendInput unicode key events.
  key(combo)      SendInput with virtual key codes (Ctrl, Alt, Shift, Win).
  scroll()        ScrollPattern.Scroll, else SendInput mouse wheel.
  screenshot()    BitBlt / Windows.Graphics.Capture of the virtual screen, PNG encoded.
Likely implementation: comtypes for UIA (MIT) and ctypes for SendInput, with no other dependencies.
"""

from __future__ import annotations

from .base import Adapter, NotSupported


class WindowsAdapter(Adapter):
    name = "windows"
    os = "Windows"

    def permissions(self) -> dict:
        return {"accessibility": None, "screen_recording": None,
                "notes": "Windows desktop control is not implemented yet. ROOKS's browser and allowed folders work on Windows."}

    def _unsupported(self, *args, **kwargs):
        raise NotSupported("Desktop control on Windows is not available yet.")

    windows = active_app = elements = open_app = focus = click_at = type_text = key = scroll = _unsupported  # type: ignore[assignment]
