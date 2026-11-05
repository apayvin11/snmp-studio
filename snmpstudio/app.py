# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations
import argparse
from pathlib import Path
import sys
import tempfile
from PySide6.QtCore import QStandardPaths,QTimer,QSettings
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication
from .storage import Store,Secrets
from .window import MainWindow
from . import APP_NAME, __version__


def main(argv=None):
    parser=argparse.ArgumentParser(description=f'{APP_NAME} desktop workstation and MIB browser')
    parser.add_argument('--db',type=Path)
    parser.add_argument('--demo',action='store_true',help='Use an isolated workspace and a real local UDP test agent')
    parser.add_argument('--language',choices=('ru','en'))
    parser.add_argument('--theme',choices=('dark','light'))
    parser.add_argument('--screenshot',type=Path)
    parser.add_argument('--page',choices=('monitor','devices','mib','commands','notifications','journal','settings'),default='mib')
    parser.add_argument('--quit-after',type=float,default=0)
    args=parser.parse_args(argv)
    app=QApplication(sys.argv[:1])
    # Stable IDs keep the existing database and QSettings namespace accessible.
    app.setApplicationName('SNMPStudio')
    app.setOrganizationName('SNMPStudio')
    app.setApplicationDisplayName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setDesktopFileName('snmpstudio')
    app.setStyle('Fusion')
    demo_settings=tempfile.TemporaryDirectory(prefix='snmpstudio-demo-') if args.demo else None
    if args.demo:QSettings.setDefaultFormat(QSettings.Format.IniFormat);QSettings.setPath(QSettings.Format.IniFormat,QSettings.Scope.UserScope,demo_settings.name)
    assets=Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parents[1]))/'assets'
    app.setWindowIcon(QIcon(str(assets/('snmpstudio-512.png' if getattr(sys,'frozen',False) else 'snmpstudio.svg'))))
    path=args.db or (Path(':memory:') if args.demo else Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation))/'configuration.db')
    store=Store(path);config=store.load();secrets=Secrets();agent=None
    if args.demo:
        from .simulator import DemoAgent, demo_configuration
        agent=DemoAgent(simulate=True)
        config=demo_configuration(agent.port,args.language or config.language,args.theme or config.theme)
        device=config.devices[0]
        secrets.session[f'{device.id}:community']='public'
        secrets.session[f'{device.id}:write_community']='private'
    if args.language:config.language=args.language
    if args.theme:config.theme=args.theme
    window=MainWindow(store,config,secrets,demo_agent=agent);window.show();window.navigate(('monitor','devices','mib','commands','notifications','journal','settings').index(args.page))
    if args.demo:window.toggle_polling()
    if args.screenshot:
        def capture():
            args.screenshot.parent.mkdir(parents=True,exist_ok=True)
            window.grab().save(str(args.screenshot))
        QTimer.singleShot(2200,capture)
    if args.quit_after>0:QTimer.singleShot(round(args.quit_after*1000),window.close)
    try:return app.exec()
    finally:
        if agent:agent.close()
        store.close()
        if demo_settings:demo_settings.cleanup()
