"""Stand-in for the `certifi` package (used by `requests`), written for Cuivar Pulse.

Instead of shipping its own copy of the certificate authorities, it points
HTTPS at the operating system's certificate store. SteamOS, Bazzite and other
distributions keep theirs up to date with system updates.

FOUND says whether one was found. If not, the plugin explains that it can't
connect securely; it never turns certificate checks off.
"""

import os

__version__ = "system"

# Where Linux distributions keep their combined certificate file.
PATHS = (
    "/etc/ssl/certs/ca-certificates.crt",                 # SteamOS / Arch, Debian, Ubuntu
    "/etc/pki/tls/certs/ca-bundle.crt",                   # Fedora, Bazzite, Nobara
    "/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem",  # Fedora (the file the above points to)
    "/etc/ssl/ca-bundle.pem",                             # openSUSE
    "/etc/ssl/cert.pem",                                  # Alpine and others
)

FOUND = next((path for path in PATHS if os.path.isfile(path)), None)


def where():
    # requests asks for this when it's imported, so always answer with a path;
    # without a real one, HTTPS fails safely (no connection) and the plugin
    # shows why (see FOUND).
    return FOUND or PATHS[0]


def contents():
    with open(where(), encoding="ascii") as f:
        return f.read()
