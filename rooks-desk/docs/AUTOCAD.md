# AutoCAD → 3D render → email: what works, honestly

The goal: "use my AutoCAD, build this, then 3D render it and email me, no stops". Here is what can be automated, on
which platform, and the recipe Rooks Desk uses. Checked October 2026.

## Ways to drive AutoCAD

| Route | Platform | 3D + render | Notes |
|---|---|---|---|
| **COM / ActiveX** (`AutoCAD.Application`, e.g. `.25.1` for 2025) | Windows, full AutoCAD | Yes: `SendCommand "-RENDER ..."`, solids via `AddBox`/`AddExtrudedSolid`… | The most capable route. Not in AutoCAD LT (no COM, no 3D solids, no render). |
| **AutoLISP / script (.scr)** | Windows and Mac | Mac AutoCAD has 3D modelling, but rendering is limited | Load from the command line (`APPLOAD`, `SCRIPT`), which Rooks Desk can type. |
| **accoreconsole.exe** (headless core) | Windows | Drawing yes, **render no** (no graphics system) | Good for batch DWG edits and plotting to PDF. |
| **ezdxf** (Python, no AutoCAD needed) | Any | Writes 2D/3D DXF entities, no rendering | Good for generating geometry that AutoCAD then opens. |
| **APS / Design Automation for AutoCAD** (Autodesk cloud) | Cloud | Runs accoreconsole in the cloud: no render | Needs an Autodesk app and paid cloud credits. |
| **UI control** (Rooks Desk) | Mac, Windows | Whatever the user can click | The slowest and most fragile route. It's the only one on Mac. |

## AutoCAD MCP servers (Rooks Desk can use them: `rooks-desk mcp-servers add ...`)

- **U-C4N/Autocad-MCP** (`pip install autocad-mcp-pro`): COM on Windows or an ezdxf fallback anywhere. Has a `[pdf]`
  extra for PDF export, and 3D tools when `ENABLE_3D` is set. Add it with
  `rooks-desk mcp-servers add autocad -- uvx autocad-mcp-pro`.
- **beiming183-cloud/AutoCAD-MCP**: COM, with 3D solids and preview images.
- **Slacker-LLC/autocad-mcp**: COM, 2D only, can take a screenshot of the drawing.
- **moisesbritez92/autocad-mcp**: accoreconsole plus a .NET plugin that listens on port 12345, headless.
- **codesknight/AutoCAD-MCP**: COM against `AutoCAD.Application.25.1`.

None of them renders by itself. Rendering is always AutoCAD's `RENDER` command in a full, visible AutoCAD session,
or a separate renderer.

## The recipe

**Windows, full AutoCAD 2021+ (the one that really works end to end):**

1. Rooks Desk opens AutoCAD (or the AutoCAD MCP server attaches through COM).
2. **Build.** The model writes the geometry as COM calls through the MCP server, or as an AutoLISP or `.scr` script
   that Rooks Desk types after `SCRIPT`. 3D work uses solids such as `BOX`, `EXTRUDE`, `REVOLVE` and `UNION`.
3. **Render.** Set a view (`-VIEW _SWISO`), a visual style and lights or sun, apply materials if needed, then run
   `-RENDER` with the output-to-file options, or `RENDERPRESETS` plus `RENDERWINDOW` and save. AutoCAD writes a
   PNG.
4. Save the DWG (`QSAVE`) and optionally export a PDF (`-EXPORT`/`PLOT`).
5. **Email.** Rooks Desk sends the PNG and DWG through the app's Mail account (`/v1/desk/send_email`). **It asks you
   before sending** unless you pre-approved `message` for that run. "No stops" means you approve the categories up
   front: `rooks-desk run --approve shell,file_write,message "..."`. Logins and dangerous commands still ask.

**Mac AutoCAD.** There's no COM. Rooks Desk drives the UI and the command line by typing (AutoLISP works). Mac
AutoCAD's rendering is limited, so export the model (`STLOUT`, or `EXPORT` to FBX/STL), render it with
`blender -b model.blend -P render.py` (free, headless), and email the PNG.

**AutoCAD LT, or no AutoCAD:** generate a DXF with ezdxf and export STL. Blender renders it; it's free and
headless. You get a real render, but not one made "in AutoCAD".

## What wasn't tested

This box is Linux with no AutoCAD licence, so the AutoCAD steps above come from Autodesk's COM and command
documentation and the MCP servers' READMEs, not from a run. The parts that **were** tested here:

- the Rooks Desk loop on real apps;
- calling another MCP server;
- the email endpoint with attachments, against a fake SMTP server.
