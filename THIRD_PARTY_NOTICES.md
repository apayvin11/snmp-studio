# Third-party components

SNMP Studio source code is licensed under Apache License 2.0. Dependencies retain their own licenses:

| Component | License | Upstream |
|---|---|---|
| PySide6 / Shiboken6 / Qt | LGPL-3.0 / GPL / commercial, depending on module | https://www.qt.io/qt-for-python |
| PySNMP | BSD-2-Clause | https://github.com/lextudio/pysnmp |
| PySMI | BSD-2-Clause | https://github.com/lextudio/pysmi |
| pyasn1 | BSD-2-Clause | https://github.com/pyasn1/pyasn1 |
| cryptography | Apache-2.0 OR BSD-3-Clause | https://github.com/pyca/cryptography |
| keyring | MIT | https://github.com/jaraco/keyring |
| Lark | MIT | https://github.com/lark-parser/lark |
| Jinja2 / MarkupSafe | BSD-3-Clause | https://palletsprojects.com/ |
| requests | Apache-2.0 | https://requests.readthedocs.io/ |
| PyInstaller | GPL-2.0-or-later with bootloader exception | https://pyinstaller.org/ |

Frozen distributions keep Qt as shared libraries. The original dependency license texts are included under `licenses/third-party` and inside bundled packages. Corresponding Qt/PySide source and build instructions are available from https://code.qt.io/ and https://download.qt.io/official_releases/QtForPython/. Users may replace the shared Qt/PySide libraries with compatible builds. SNMP Studio does not prohibit reverse engineering for debugging changes to LGPL components. Additional transitive component notices are collected from the installed distribution metadata during packaging.
