"""Rooks Browser: ai-workspace's AI browser agent (phase 3).

Local first and model agnostic. It drives the Chrome or Edge already on your computer through Playwright (CDP), reads
pages as an accessibility list (text models) plus screenshots (vision models), asks before anything sensitive, and
writes every visited URL to a receipt in Workbench's run-log format.
"""

__version__ = "0.1.0"
