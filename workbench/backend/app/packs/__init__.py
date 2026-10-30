"""Optional packs (feat/v02-packs): installable bundles of skills, templates and small pages for a field of work.

The core assistant stays generic. A pack only adds shortcuts: its skills go through the same security gate as skills from
GitHub (skills_hub.review_folder), its pages live under /packs/<id>, and its assistant tools only appear once it is added.
Bundles ship inside the app (bundles/<id>/pack.json, skills/*/SKILL.md, templates/*), so adding one needs no download.
"""
