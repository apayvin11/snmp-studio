# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations
from collections import OrderedDict
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import time
from PySide6.QtCore import Qt,QTimer,QSettings
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QMainWindow,QWidget,QVBoxLayout,QHBoxLayout,QFrame,QLabel,QPushButton,
    QStackedWidget,QTableWidget,QTableWidgetItem,QHeaderView,QAbstractItemView,QLineEdit,QComboBox,
    QCheckBox,QSpinBox,QSplitter,QTreeWidget,QTreeWidgetItem,QPlainTextEdit,QFileDialog,QMessageBox,
    QApplication,QSizePolicy)
from . import APP_NAME, __version__
from .models import Parameter,uid
from .storage import Store
from .notifications import decode_value,notification_identity
from .protocol import Value,mib_set_type,validate_set_row
from .worker import Worker
from .dialogs import DeviceDialog,ParameterDialog,CommandDialog
from .editor import MibStudio
from .inspector import Inspector
from .theme import apply_theme,icon
from .i18n import tr
from .widgets import ComboBox as CommandComboBox, field, EmptyState, ElidedLabel


class MainWindow(QMainWindow):
    def __init__(self,store,config,secrets,demo_agent=None):
        super().__init__();self.store,self.config,self.secrets=store,config,secrets
        self.demo_agent=demo_agent
        self.setWindowTitle(APP_NAME);self.resize(1460,930);self.setMinimumSize(1200,760)
        self.refs=[];self.header_tables=[];self.values={};self.due={};self.pending=set();self.polling=False
        self.packets=OrderedDict();self.packet_bytes=0;self.mib_request_cancelled=False;self.mib_request_context=None;self.listener_running=False
        self.worker=Worker();self.worker.result.connect(self.on_result);self.worker.packet.connect(self.on_packet);self.worker.notification.connect(self.on_notification);self.worker.start()
        root=QWidget();self.setCentralWidget(root);shell=QHBoxLayout(root);shell.setContentsMargins(0,0,0,0);shell.setSpacing(0)
        sidebar=QFrame();sidebar.setObjectName('sidebar');sidebar.setFixedWidth(218);side=QVBoxLayout(sidebar);side.setContentsMargins(20,28,18,22);side.setSpacing(8)
        brand=QLabel(APP_NAME);brand.setObjectName('brand');side.addWidget(brand)
        sub=QLabel('NETWORK WORKSPACE');sub.setObjectName('brandSub');side.addWidget(sub);side.addSpacing(35)
        self.stack=QStackedWidget();self.nav=[]
        for i,(key,glyph) in enumerate([('monitor','grid'),('devices','devices'),('mib','network'),('commands','bolt'),('notifications','activity'),('journal','file'),('settings','settings')]):
            button=QPushButton();button.setObjectName('nav');button.setCheckable(True);button.setIcon(icon(glyph,'#91A1BA'));self.bind(button,key)
            button.clicked.connect(lambda checked=False,index=i:self.navigate(index));side.addWidget(button);self.nav.append(button)
        side.addStretch();self.status=QLabel();self.status.setObjectName('sidebarLabel');self.status.setWordWrap(True);self.status.setMinimumHeight(36);side.addWidget(self.status)
        foot=QLabel(f'v{__version__}   ·   Apache 2.0');foot.setObjectName('sidebarMuted');side.addWidget(foot);shell.addWidget(sidebar)
        content=QWidget();content.setObjectName('content');layout=QVBoxLayout(content);layout.setContentsMargins(30,26,30,20);layout.setSpacing(18)
        top=QHBoxLayout();self.title=QLabel();self.title.setObjectName('title');top.addWidget(self.title);top.addStretch()
        self.language=CommandComboBox();self.language.setFixedWidth(132);self.language.addItem('Русский','ru');self.language.addItem('English','en');self.language.setCurrentIndex(self.language.findData(config.language));self.language.currentIndexChanged.connect(self.change_language);top.addWidget(self.language)
        self.theme=QPushButton();self.theme.setFixedSize(38,38);self.theme.clicked.connect(self.toggle_theme);top.addWidget(self.theme);layout.addLayout(top)
        layout.addWidget(self.stack,1);shell.addWidget(content,1)
        self.build_monitor();self.build_devices()
        self.mib=MibStudio(config);self.mib.changed.connect(self.save_config);self.mib.add_object.connect(self.add_mib_parameter);self.mib.request_object.connect(self.query_mib);self.mib.cancel_request.connect(self.cancel_mib_query);self.mib.write_object.connect(self.command_from_mib);self.mib.write_instance.connect(self.command_oid_from_mib);self.mib.query.journal_requested.connect(lambda:self.navigate(5));self.mib.query.add_device_requested.connect(self.add_device);self.mib.query.set_requested.connect(self.set_mib);self.stack.addWidget(self.mib)
        self.build_commands();self.build_notifications();self.build_journal();self.build_settings()
        self.mib.validated.connect(self.refresh_notifications)
        # Keep existing page indices stable, and place the editor next to the browser in the menu.
        self.editor_page_index=self.stack.addWidget(self.mib.editor_page)
        self.editor_nav=self.bind(QPushButton(),'mib_editor')
        self.editor_nav.setObjectName('nav');self.editor_nav.setCheckable(True)
        self.editor_nav.setIcon(icon('code','#91A1BA'))
        self.editor_nav.clicked.connect(lambda:self.navigate(self.editor_page_index))
        self.nav.append(self.editor_nav);side.insertWidget(side.indexOf(self.nav[2])+1,self.editor_nav)
        self.mib.editor_requested.connect(lambda:self.navigate(self.editor_page_index))
        self.refresh_devices();self.render_monitor();self.translate();self.update_theme();self.navigate(2)
        if self.demo_agent is not None and self.demo_agent.running:self.prepare_demo_device()
        self.timer=QTimer(self);self.timer.setInterval(200);self.timer.timeout.connect(self.tick);self.timer.start()
        settings=QSettings();geometry=settings.value('geometry')
        if geometry:self.restoreGeometry(geometry)

    def t(self,key):return tr(key,self.config.language)
    def bind(self,widget,key):
        self.refs.append((widget,key))
        if widget.property('iconOnly'):
            widget.setToolTip(self.t(key));widget.setAccessibleName(self.t(key))
        else:widget.setText(self.t(key))
        return widget
    def icon_button(self,key,glyph,callback):
        button=QPushButton();button.setProperty('iconOnly',True);button.setObjectName('ghost')
        button.setFixedSize(36,36);button.setIcon(icon(glyph));button.clicked.connect(callback)
        return self.bind(button,key)
    def labeled_field(self,key,control):return field(self.bind(QLabel(),key),control)
    def button(self,key,callback,primary=False):
        widget=self.bind(QPushButton(),key);widget.clicked.connect(callback)
        if primary:widget.setObjectName('primary')
        return widget
    def page(self):
        widget=QWidget();layout=QVBoxLayout(widget);layout.setContentsMargins(0,0,0,0);layout.setSpacing(14);self.stack.addWidget(widget);return widget,layout
    def table(self,headers):
        table=QTableWidget(0,len(headers));table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows);table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers);table.verticalHeader().hide();table.verticalHeader().setDefaultSectionSize(43)
        table.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter);table.horizontalHeader().setStretchLastSection(True);table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        table.setObjectName('tableCard');self.header_tables.append((table,headers));table.setHorizontalHeaderLabels([self.t(h) for h in headers]);return table
    def navigate(self,index):
        if index==self.editor_page_index and not self.config.editor_enabled:index=2
        changed=self.stack.currentIndex()!=index
        self.stack.setCurrentIndex(index)
        for i,b in enumerate(self.nav):b.setChecked(i==index)
        self.title.setText(self.nav[index].text())
        if changed:
            if index==self.editor_page_index and self.mib.current():self.mib.current().setFocus()
            elif index==2:self.mib.tree.setFocus()
    def translate(self):
        for widget,key in self.refs:
            if widget.property('iconOnly'):widget.setToolTip(self.t(key));widget.setAccessibleName(self.t(key))
            else:widget.setText(self.t(key))
        for table,headers in self.header_tables:table.setHorizontalHeaderLabels([self.t(h) for h in headers])
        self.monitor_search.setPlaceholderText(self.t('search'));self.log_search.setPlaceholderText(self.t('search'))
        self.poll_button.setText(self.t('stop' if self.polling else 'start'))
        self.logs.setHeaderLabels(['Время','TX/RX','Устройство','Операция','Байт','Обмен'] if self.config.language=='ru' else ['Time','TX/RX','Device','Operation','Bytes','Transaction'])
        self.command_search.setPlaceholderText(self.t('command_search'))
        self.saved_empty.set_text(self.t('saved_commands_empty'),self.t('saved_commands_help'))
        self.render_commands()
        self.command_empty.set_text(self.t('command_empty_title'),self.t('command_result_empty'))
        self.render_command_state();self.translate_notifications()
        self.mib.translate();self.inspector.translate();self.theme.setToolTip(self.t('change_theme'));self.theme.setAccessibleName(self.t('change_theme'))
        self.navigate(max(0,self.stack.currentIndex()));self.refresh_devices();self.render_monitor();self.update_editor_setting();self.update_demo_controls()
    def update_theme(self):
        apply_theme(QApplication.instance(),self.config.theme)
        self.theme.setIcon(icon('sun' if self.config.theme=='dark' else 'moon','#91A1BA'));self.mib.set_theme();self.render_command_state()
        if self.inspector.packet:self.inspector.show_packet(self.inspector.packet,self.reveal.isChecked())
    def toggle_theme(self):self.config.theme='light' if self.config.theme=='dark' else 'dark';self.update_theme();self.save_config()
    def change_language(self):
        self.config.language=self.language.currentData();self.translate();self.save_config()
    def save_config(self):
        try:self.store.save(self.config)
        except Exception as exc:self.error(str(exc))
    def error(self,text):QMessageBox.warning(self,self.t('error'),text)
    def credentials(self,device):return {key:self.secrets.get(device.id,key) for key in ('community','write_community','auth_password','priv_password')}
    def selected_id(self,table):
        row=table.currentRow();item=table.item(row,0) if row>=0 else None;return item.data(Qt.ItemDataRole.UserRole) if item else None

    def build_monitor(self):
        _,layout=self.page();bar=QHBoxLayout();self.monitor_search=QLineEdit();self.monitor_search.setClearButtonEnabled(True);self.monitor_search.textChanged.connect(self.render_monitor);bar.addWidget(self.monitor_search,1)
        bar.addWidget(self.button('add_parameter',self.add_parameter));self.poll_button=self.button('start',self.toggle_polling,True);bar.addWidget(self.poll_button);bar.addWidget(self.button('refresh',lambda:self.tick(True)));layout.addLayout(bar)
        self.metrics=QLabel();self.metrics.setObjectName('muted');layout.addWidget(self.metrics)
        self.monitor=self.table(['name','device','OID','value','unit','state','updated']);self.monitor.setColumnWidth(0,200);self.monitor.setColumnWidth(1,150);self.monitor.setColumnWidth(2,260);self.monitor.setColumnWidth(3,220);self.monitor.horizontalHeader().setSectionResizeMode(0,QHeaderView.ResizeMode.Stretch);self.monitor.horizontalHeader().setSectionResizeMode(3,QHeaderView.ResizeMode.Stretch);self.monitor.setColumnWidth(1,130);self.monitor.setColumnWidth(2,220);self.monitor.setColumnWidth(4,85);self.monitor.setColumnWidth(5,110);self.monitor.setColumnWidth(6,100);self.monitor.cellDoubleClicked.connect(lambda *_:self.edit_parameter());layout.addWidget(self.monitor,1)
        bar=QHBoxLayout();bar.addWidget(self.button('edit',self.edit_parameter));bar.addWidget(self.button('delete',self.delete_parameter));bar.addStretch();layout.addLayout(bar)
    def build_devices(self):
        _,layout=self.page();bar=QHBoxLayout();bar.addWidget(self.button('add_device',self.add_device,True));bar.addWidget(self.button('edit',self.edit_device));bar.addWidget(self.button('delete',self.delete_device));bar.addStretch();layout.addLayout(bar)
        self.devices=self.table(['name','host','SNMP','period','state']);self.devices.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter);self.devices.horizontalHeader().setStyleSheet('QHeaderView::section { padding-left: 11px; }');self.devices.setColumnWidth(0,220);self.devices.setColumnWidth(1,300);self.devices.cellDoubleClicked.connect(lambda *_:self.edit_device());layout.addWidget(self.devices,1)
    def refresh_devices(self):
        self.devices.setRowCount(len(self.config.devices))
        for row,d in enumerate(self.config.devices):
            for col,text in enumerate([d.name,f'{d.host}:{d.port}',d.version,str(d.period),self.t('enabled' if d.enabled else 'disabled')]):
                item=QTableWidgetItem(text);item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter);item.setData(Qt.ItemDataRole.UserRole,d.id);self.devices.setItem(row,col,item)
        self.mib.query.refresh_devices()
        self.render_commands()
    def add_device(self):
        dialog=DeviceDialog(self.config,self.secrets,parent=self)
        if dialog.exec():self.config.devices.append(dialog.device);self.refresh_devices();self.save_config()
    def edit_device(self):
        identifier=self.selected_id(self.devices);d=next((d for d in self.config.devices if d.id==identifier),None)
        if not d:return
        if f'poll:{d.id}' in self.pending:self.error('Дождитесь завершения текущего запроса' if self.config.language=='ru' else 'Wait for the current request to finish');return
        dialog=DeviceDialog(self.config,self.secrets,d,self)
        if dialog.exec():self.config.devices[self.config.devices.index(d)]=dialog.device;self.refresh_devices();self.render_monitor();self.save_config()
    def delete_device(self):
        identifier=self.selected_id(self.devices)
        if identifier and QMessageBox.question(self,APP_NAME,self.t('remove_confirm'))==QMessageBox.StandardButton.Yes:
            self.config.devices=[d for d in self.config.devices if d.id!=identifier];self.config.parameters=[p for p in self.config.parameters if p.device!=identifier];self.refresh_devices();self.render_monitor();self.save_config()
    def add_parameter(self,checked=False,obj=None,parameter=None):
        if not self.config.devices:self.add_device()
        if not self.config.devices:return
        dialog=ParameterDialog(self.config,self.mib.index,parameter,obj,self)
        if dialog.exec():self.config.parameters.append(dialog.parameter);self.render_monitor();self.save_config()
    def edit_parameter(self):
        identifier=self.selected_id(self.monitor);p=next((p for p in self.config.parameters if p.id==identifier),None)
        if not p:return
        dialog=ParameterDialog(self.config,self.mib.index,p,parent=self)
        if dialog.exec():self.config.parameters[self.config.parameters.index(p)]=dialog.parameter;self.values.pop(p.id,None);self.render_monitor();self.save_config()
    def delete_parameter(self):
        identifier=self.selected_id(self.monitor)
        if identifier and QMessageBox.question(self,APP_NAME,self.t('remove_confirm'))==QMessageBox.StandardButton.Yes:
            self.config.parameters=[p for p in self.config.parameters if p.id!=identifier];self.render_monitor();self.save_config()
    def render_monitor(self,*args):
        query=self.monitor_search.text().casefold();selected=self.selected_id(self.monitor);devices={d.id:d for d in self.config.devices};params=[p for p in self.config.parameters if not query or query in f'{p.name} {p.oid} {devices.get(p.device).name if p.device in devices else ""}'.casefold()]
        self.monitor.setRowCount(len(params));now=time.time()
        for row,p in enumerate(params):
            device=devices.get(p.device);saved=self.values.get(p.id,{});value=saved.get('value');error=saved.get('error','');stamp=saved.get('time',0)
            state=self.t('waiting');text='—'
            if value:
                text=p.formatted(value.value,value.kind)
                obj=self.mib.index.describe_instance(p.oid)
                if obj and isinstance(value.value,int) and value.value in obj.enums:text=f'{obj.enums[value.value]} ({value.value})'
                state=self.t('ok')
                if now-stamp>max(3,(p.period or device.period)*3):state=self.t('stale')
            if error:state=error
            if not p.enabled or device and not device.enabled:state=self.t('disabled')
            texts=[p.name,device.name if device else '—',p.oid,text,p.unit,state,time.strftime('%H:%M:%S',time.localtime(stamp)) if stamp else '—']
            for col,content in enumerate(texts):
                item=QTableWidgetItem(content);item.setData(Qt.ItemDataRole.UserRole,p.id);item.setToolTip(content)
                if col==5:item.setForeground(QColor('#F39492' if error else '#11A88A'))
                self.monitor.setItem(row,col,item)
            if p.id==selected:self.monitor.selectRow(row)
        self.metrics.setText(f'{len(params)} / {len(self.config.parameters)} OID  ·  {len(self.config.devices)} '+('устройств' if self.config.language=='ru' else 'devices'))
    def toggle_polling(self):self.polling=not self.polling;self.poll_button.setText(self.t('stop' if self.polling else 'start'));self.poll_button.setIcon(icon('pause' if self.polling else 'play','#FFFFFF'));self.due.clear();self.tick()
    def tick(self,force=False):
        now=time.monotonic()
        if self.polling or force:
            for device in self.config.devices:
                job=f'poll:{device.id}'
                if not device.enabled or job in self.pending:continue
                parameters=[p for p in self.config.parameters if p.device==device.id and p.enabled and (force or self.due.get(p.id,0)<=now)]
                if parameters:
                    for p in parameters:self.due[p.id]=now+(p.period or device.period)
                    self.pending.add(job);self.worker.submit(job,self.worker.read(replace(device),self.credentials(device),[replace(p) for p in parameters]))
        self.status.setText(('●  '+self.t('start') if not self.polling else '●  '+('Опрос выполняется' if self.config.language=='ru' else 'Polling active'))+f'\n{len(self.packets)} packets')
        if int(now)!=getattr(self,'last_render',-1):self.last_render=int(now);self.render_monitor()
    def query_mib(self,device_id,operation,oid,max_repetitions=20):
        if 'mib-query' in self.pending:return
        query=self.mib.query;query.clear_result()
        device=next((d for d in self.config.devices if d.id==device_id),None)
        if device is None:query.show_error(self.t('choose_device'));return
        if operation=='GETBULK' and device.version=='1':query.show_error(self.t('bulk_v1'));return
        try:oid=self.mib.index.resolve(oid)
        except Exception as exc:query.show_error(str(exc));return
        self.mib_request_cancelled=False;self.pending.add('mib-query')
        query.begin(device,operation,oid);self.mib_request_context=query.result_context;self.mib.tree.setEnabled(False)
        self.worker.submit('mib-query',self.worker.query(replace(device),self.credentials(device),operation,oid,max_repetitions))

    def set_mib(self,device_id,oid,kind,value):
        if 'mib-query' in self.pending:return
        query=self.mib.query
        device=next((d for d in self.config.devices if d.id==device_id),None)
        if device is None:query.show_error(self.t('choose_device'));return
        try:row=validate_set_row(self.mib.index,oid,kind,value)
        except Exception as exc:
            query.clear_result();query.show_error(str(exc));return
        shown=repr(value) if kind in ('OctetString','HexString') else value
        question=QMessageBox(self);question.setWindowTitle(self.t('send'));question.setIcon(QMessageBox.Icon.Question)
        question.setTextFormat(Qt.TextFormat.PlainText)
        question.setText(f'{device.name} · {device.host}:{device.port}\n\n{self.t("confirm_set")}\n\n{row["oid"]} · {kind} = {shown}')
        question.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        question.button(QMessageBox.StandardButton.Yes).setText(self.t('send'));question.button(QMessageBox.StandardButton.No).setText(self.t('cancel'))
        question.setDefaultButton(QMessageBox.StandardButton.No);question.setEscapeButton(QMessageBox.StandardButton.No)
        decision=question.exec();question.deleteLater()
        if decision!=QMessageBox.StandardButton.Yes:return
        self.mib_request_cancelled=False;self.pending.add('mib-query')
        query.begin(device,'SET',row['oid']);self.mib_request_context=query.result_context;self.mib.tree.setEnabled(False)
        self.worker.submit('mib-query',self.worker.write(replace(device),self.credentials(device),[row]))

    def cancel_mib_query(self):
        if self.mib_request_context and self.mib_request_context[2]=='SET':return
        if 'mib-query' in self.pending:
            self.mib_request_cancelled=True;self.mib.query.cancel_button.setEnabled(False);self.worker.cancel('mib-query')

    def add_mib_parameter(self,oid,device_id):
        query=self.mib.query
        if not self.config.devices:self.add_device()
        if not self.config.devices:return
        try:oid=self.mib.index.resolve(oid)
        except Exception as exc:query.show_error(str(exc));return
        obj=self.mib.index.describe(oid)
        if obj and oid==obj.oid and oid not in query.observed_values:
            query.show_error(self.t('choose_instance'));return
        if device_id and not any(d.id==device_id for d in self.config.devices):
            query.show_error(self.t('choose_device'));return
        device_id=device_id or self.config.devices[0].id
        obj=self.mib.index.describe_instance(oid)
        parameter=Parameter(device=device_id,name=obj.name if obj else oid,oid=oid,unit=obj.units if obj else '')
        dialog=ParameterDialog(self.config,self.mib.index,parameter=parameter,parent=self,lock_oid=True)
        dialog.setWindowTitle(self.t('add_monitor'))
        if dialog.exec():self.config.parameters.append(dialog.parameter);self.render_monitor();self.save_config()

    def build_commands(self):
        _,layout=self.page();self.command_state=''
        # Assign stable identities to older records without changing their values or grouping.
        for command in self.config.commands:command.setdefault('id',uid())
        bar=QHBoxLayout();self.command_search=QLineEdit();self.command_search.setClearButtonEnabled(True)
        self.command_search.textChanged.connect(self.render_commands);bar.addWidget(self.command_search,1)
        self.add_command_button=self.button('add',self.add_saved_command);self.add_command_button.setIcon(icon('plus'));bar.addWidget(self.add_command_button);layout.addLayout(bar)
        card=QFrame();card.setObjectName('card');body=QVBoxLayout(card);body.setContentsMargins(16,12,16,12);body.setSpacing(10)
        bar=QHBoxLayout();title=self.bind(QLabel(),'saved_commands');title.setObjectName('sectionTitle');bar.addWidget(title);bar.addStretch()
        self.edit_command_button=self.button('edit',self.edit_saved_command);bar.addWidget(self.edit_command_button)
        self.delete_saved_command_button=self.icon_button('delete_saved_command','trash',self.delete_saved_command);bar.addWidget(self.delete_saved_command_button);body.addLayout(bar)
        self.command_table=self.table(['name','OID','device','type','value']);self.command_table.setObjectName('commandTable');self.command_table.setShowGrid(False)
        self.command_table.setFrameShape(QFrame.Shape.NoFrame);self.command_table.setWordWrap(False)
        header=self.command_table.horizontalHeader();header.setStretchLastSection(False)
        header.setSectionResizeMode(0,QHeaderView.ResizeMode.Stretch);header.setSectionResizeMode(4,QHeaderView.ResizeMode.Interactive)
        self.command_table.setColumnWidth(1,235);self.command_table.setColumnWidth(2,140);self.command_table.setColumnWidth(3,115);self.command_table.setColumnWidth(4,140)
        self.command_table.setMinimumHeight(140);self.saved_empty=EmptyState(self.command_table)
        self.command_table.itemSelectionChanged.connect(self.update_command_actions)
        self.command_table.cellDoubleClicked.connect(lambda *_:self.edit_saved_command());body.addWidget(self.command_table,1)
        bar=QHBoxLayout();self.command_hint=self.bind(QLabel(),'saved_commands_help');self.command_hint.setObjectName('muted');self.command_hint.setWordWrap(True);bar.addWidget(self.command_hint,1)
        self.send_button=self.button('send',self.send_command,True);self.send_button.setMinimumWidth(170);bar.addWidget(self.send_button);body.addLayout(bar);layout.addWidget(card,3)
        result_card=QFrame();result_card.setObjectName('card');result_layout=QVBoxLayout(result_card)
        result_layout.setContentsMargins(16,12,16,12);result_layout.setSpacing(8)
        bar=QHBoxLayout();title=self.bind(QLabel(),'command_result');title.setObjectName('sectionTitle');bar.addWidget(title)
        self.command_status=QLabel();bar.addWidget(self.command_status);bar.addStretch()
        self.copy_command_result=self.icon_button('copy','copy',lambda:QApplication.clipboard().setText(self.command_result.toPlainText()));bar.addWidget(self.copy_command_result)
        bar.addWidget(self.icon_button('journal','file',lambda:self.navigate(5)))
        self.clear_command_result_button=self.icon_button('clear','close',self.clear_command_result);bar.addWidget(self.clear_command_result_button);result_layout.addLayout(bar)
        self.command_context=QLabel();self.command_context.setObjectName('muted');self.command_context.setWordWrap(True);self.command_context.hide();result_layout.addWidget(self.command_context)
        self.command_result=QPlainTextEdit();self.command_result.setReadOnly(True);self.command_result.setMinimumHeight(80)
        self.command_empty=EmptyState(self.command_result,lambda:not self.command_result.toPlainText())
        self.command_result.textChanged.connect(self.update_command_result_actions)
        result_layout.addWidget(self.command_result,1);layout.addWidget(result_card,2)
        self.render_commands();self.update_command_result_actions()

    def selected_command(self):
        if not self.command_table.selectionModel().selectedRows():return None
        identifier=self.selected_id(self.command_table)
        return next((command for command in self.config.commands if command.get('id')==identifier),None)

    def render_commands(self, *args, selected=None):
        if not hasattr(self,'command_table'):return
        current=self.selected_command()
        selected=selected or (current['id'] if current else None)
        term=self.command_search.text().strip().casefold()
        devices={device.id:device.name for device in self.config.devices}
        entries=[]
        for command in self.config.commands:
            command.setdefault('id',uid())
            rows=command['rows'];device=devices.get(command.get('device'),self.t('device_missing'))
            values=[command['name'],'\n'.join(row['oid'] for row in rows),device,'\n'.join(row['type'] for row in rows),'\n'.join(repr(row['value']) if not row['value'] else row['value'] for row in rows)]
            if not term or term in ' '.join(values).casefold():entries.append((command,values))
        self.command_table.blockSignals(True);self.command_table.setRowCount(0)
        for number,(command,values) in enumerate(entries):
            self.command_table.insertRow(number)
            for column,text in enumerate(values):
                item=QTableWidgetItem(text);item.setToolTip(text);item.setData(Qt.ItemDataRole.UserRole,command['id']);self.command_table.setItem(number,column,item)
            self.command_table.setRowHeight(number,max(43,20*len(command['rows'])+16))
            if command['id']==selected:self.command_table.selectRow(number)
        self.command_table.blockSignals(False)
        if term:self.saved_empty.set_text(self.t('command_no_matches'),self.t('command_change_search'))
        else:self.saved_empty.set_text(self.t('saved_commands_empty'),self.t('saved_commands_help'))
        self.saved_empty.refresh();self.update_command_actions()

    def update_command_actions(self,*args):
        if not hasattr(self,'send_button'):return
        pending='set' in self.pending;command=self.selected_command()
        device=next((device for device in self.config.devices if command and device.id==command.get('device')),None)
        self.send_button.setEnabled(bool(command and device) and not pending)
        self.edit_command_button.setEnabled(bool(command) and not pending)
        self.delete_saved_command_button.setEnabled(bool(command) and not pending)
        self.command_table.setEnabled(not pending);self.add_command_button.setEnabled(not pending);self.command_search.setEnabled(not pending)
        if hasattr(self,'command_result'):self.update_command_result_actions()

    def render_command_state(self):
        self.command_status.setText(self.t(self.command_state) if self.command_state else '')
        dark=self.config.theme=='dark'
        color=('#F39492' if dark else '#B5332E') if self.command_state=='error' else ('#5DD8BA' if dark else '#087D69') if self.command_state=='request_success' else '#8490A2'
        self.command_status.setStyleSheet(f'color: {color}; font-weight: 700;')

    def update_command_result_actions(self):
        available=bool(self.command_result.toPlainText()) and 'set' not in self.pending
        self.copy_command_result.setEnabled(available);self.clear_command_result_button.setEnabled(available);self.command_empty.refresh()

    def clear_command_result(self):
        if 'set' in self.pending:return
        self.command_state='';self.command_context.hide();self.command_result.clear();self.render_command_state()

    def add_saved_command(self):
        if 'set' in self.pending:return
        if not self.config.devices:self.add_device()
        if not self.config.devices:return
        selected=self.selected_command()
        device_id=selected.get('device') if selected else self.mib.query.device.currentData()
        if not any(device.id==device_id for device in self.config.devices):device_id=self.config.devices[0].id
        command={'id':uid(),'name':'','device':device_id,'rows':[{'oid':'','type':'Integer32','value':''}],'verify':False}
        dialog=CommandDialog(self.config,self.mib.index,command,self,on_save=self.commit_saved_command,creating=True,lock_oid=False)
        dialog.exec()

    def command_oid_from_mib(self,oid,device_id):
        if device_id and not any(d.id==device_id for d in self.config.devices):
            self.mib.query.show_error(self.t('choose_device'));return
        try:oid=self.mib.index.resolve(oid)
        except ValueError:return
        obj=self.mib.index.describe_instance(oid)
        if obj and obj.access in ('read-write','read-create','write-only'):self.command_from_mib(obj,oid,device_id)

    def command_from_mib(self,obj,oid=None,device_id=None):
        if 'set' in self.pending:return
        if not self.config.devices:self.add_device()
        if not self.config.devices:return
        oid=oid or self.mib.index.instance_oid(obj);query=self.mib.query
        device_id=device_id or query.device.currentData()
        if obj.access not in ('read-write','read-create','write-only') or oid==obj.oid:return
        kind=mib_set_type(obj);value=str(next(iter(obj.enums))) if obj.enums else ''
        current=query.selected_value() or query.observed_values.get(oid)
        if current and current.oid==oid and not current.error:
            if isinstance(current.value,bytes):
                try:value=current.value.decode('utf-8')
                except UnicodeDecodeError:kind='HexString';value=current.value.hex(' ')
            else:value=str(current.value)
        if query.operation.currentData()=='SET' and query.oid.text().strip()==oid:
            kind=query.set_type.currentText();value=query.set_value.text()
        command={'id':uid(),'name':obj.name,'device':device_id,'rows':[{'oid':oid,'type':kind,'value':value}],'verify':False}
        dialog=CommandDialog(self.config,self.mib.index,command,self,on_save=self.commit_saved_command,creating=True)
        if dialog.exec():self.navigate(3)

    def commit_saved_command(self,command):
        commands=deepcopy(self.config.commands)
        existing=next((i for i,item in enumerate(commands) if item.get('id')==command['id']),None)
        if existing is None:commands.append(deepcopy(command))
        else:commands[existing]=deepcopy(command)
        self.store.save(replace(self.config,commands=commands))
        self.config.commands=commands
        self.command_search.clear();self.render_commands(selected=command['id'])

    def edit_saved_command(self):
        if 'set' in self.pending:return
        command=self.selected_command()
        if command is None:return
        dialog=CommandDialog(self.config,self.mib.index,command,self,on_save=self.commit_saved_command)
        dialog.exec()

    def command_rows(self,command=None):
        command=command or self.selected_command()
        if not command or not command['rows']:raise ValueError(self.t('command_no_oid'))
        return [validate_set_row(self.mib.index,row['oid'],row['type'],row['value']) for row in command['rows']]

    def send_command(self):
        if 'set' in self.pending:return
        command=self.selected_command()
        if not command:return
        device=next((device for device in self.config.devices if device.id==command.get('device')),None)
        if not device:return
        try:rows=self.command_rows(command)
        except Exception as exc:
            self.command_state='error';self.command_result.setPlainText(str(exc));self.render_command_state();return
        body='\n'.join(f'{row["oid"]} · {row["type"]} = {row["value"]!r}' for row in rows)
        if not self.confirm_command('send',f'{command["name"]} · {device.name} · {device.host}:{device.port}\n\n{self.t("confirm_set")}\n\n{body}'):return
        self.pending.add('set');self.command_state='request_running';self.command_context.setText(f'{command["name"]} · {device.name} · {device.host}:{device.port}');self.command_context.show()
        self.update_command_actions();self.command_result.setPlainText(self.t('sending_set'));self.render_command_state()
        self.worker.submit('set',self.worker.write(replace(device),self.credentials(device),rows,command.get('verify',False)))

    def confirm_command(self,title,text):
        question=QMessageBox(self);question.setIcon(QMessageBox.Icon.Question);question.setWindowTitle(self.t(title));question.setTextFormat(Qt.TextFormat.PlainText);question.setText(text)
        question.setStandardButtons(QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No)
        question.button(QMessageBox.StandardButton.Yes).setText(self.t(title));question.button(QMessageBox.StandardButton.No).setText(self.t('cancel'))
        question.setDefaultButton(QMessageBox.StandardButton.No);question.setEscapeButton(QMessageBox.StandardButton.No)
        decision=question.exec();question.deleteLater();return decision==QMessageBox.StandardButton.Yes

    def delete_saved_command(self):
        if 'set' in self.pending:return
        command=self.selected_command()
        if command is None:return
        if not self.confirm_command('delete',self.t('delete_saved_command_confirm').format(name=command['name'])):return
        commands=[item for item in self.config.commands if item.get('id')!=command['id']]
        try:self.store.save(replace(self.config,commands=commands))
        except Exception as exc:self.error(str(exc));return
        self.config.commands=commands;self.render_commands();self.command_table.clearSelection();self.update_command_actions()

    def build_notifications(self):
        _,layout=self.page();self.listener_pending=False;self.listener_error='';self.listener_endpoint=''
        card=QFrame();card.setObjectName('card');body=QVBoxLayout(card);body.setContentsMargins(16,14,16,14);body.setSpacing(12)
        bar=QHBoxLayout();title=self.bind(QLabel(),'notification_receiver');title.setObjectName('sectionTitle');bar.addWidget(title);bar.addStretch()
        self.listener_status=QLabel();bar.addWidget(self.listener_status);body.addLayout(bar)
        bar=QHBoxLayout();bar.setSpacing(12)
        self.listen_host=QLineEdit('127.0.0.1');self.listen_host.setFixedHeight(38)
        self.listen_port=QSpinBox();self.listen_port.setRange(1,65535);self.listen_port.setValue(10162);self.listen_port.setFixedSize(126,38)
        self.listen_port.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.listen_port.lineEdit().setTextMargins(10,0,0,0)
        bar.addWidget(self.labeled_field('host',self.listen_host),1);bar.addWidget(self.labeled_field('udp_port',self.listen_port))
        self.listen_button=self.button('listen',self.toggle_listener,True);self.listen_button.setFixedHeight(38);bar.addWidget(self.listen_button,0,Qt.AlignmentFlag.AlignBottom);body.addLayout(bar)
        hint=QHBoxLayout();note=self.bind(QLabel(),'notification_help');note.setObjectName('muted');note.setWordWrap(True);hint.addWidget(note,1)
        hint.addWidget(self.button('devices',lambda:self.navigate(1)));body.addLayout(hint)
        self.demo_notification_panel=QWidget();demo=QHBoxLayout(self.demo_notification_panel);demo.setContentsMargins(0,0,0,0);label=self.bind(QLabel(),'demo_notification');label.setObjectName('muted');demo.addWidget(label)
        self.demo_notification_kind=CommandComboBox();self.demo_notification_kind.addItems(['TRAP','INFORM']);self.demo_notification_kind.setFixedWidth(120);demo.addWidget(self.demo_notification_kind)
        self.demo_notification_button=self.button('send_demo_notification',self.send_demo_notification);demo.addWidget(self.demo_notification_button)
        self.demo_notification_status=ElidedLabel();self.demo_notification_status.setObjectName('muted');demo.addWidget(self.demo_notification_status,1)
        demo.addWidget(self.button('settings',lambda:self.navigate(6)));body.addWidget(self.demo_notification_panel);layout.addWidget(card)
        history=QFrame();history.setObjectName('card');history_layout=QVBoxLayout(history);history_layout.setContentsMargins(16,12,16,12)
        bar=QHBoxLayout();title=self.bind(QLabel(),'notification_history');title.setObjectName('sectionTitle');bar.addWidget(title);bar.addStretch()
        self.clear_notifications_button=self.icon_button('clear','trash',self.clear_notifications);bar.addWidget(self.clear_notifications_button);history_layout.addLayout(bar)
        self.notifications=self.table(['received_at','type','name','OID','value','host']);self.notifications.setColumnWidth(0,95);self.notifications.setColumnWidth(1,75);self.notifications.setColumnWidth(2,155);self.notifications.setColumnWidth(3,220);self.notifications.setColumnWidth(4,180);self.notifications.setColumnWidth(5,135)
        self.notifications.horizontalHeader().setStretchLastSection(False);self.notifications.horizontalHeader().setSectionResizeMode(4,QHeaderView.ResizeMode.Stretch)
        self.notifications.itemSelectionChanged.connect(self.show_notification_details)
        self.notification_empty=EmptyState(self.notifications);history_layout.addWidget(self.notifications,1)
        self.notification_details=QLabel();self.notification_details.setTextFormat(Qt.TextFormat.PlainText);self.notification_details.setWordWrap(True);self.notification_details.setMaximumHeight(90);self.notification_details.setObjectName('muted');history_layout.addWidget(self.notification_details)
        history_layout.addWidget(self.button('journal',self.notification_journal),0,Qt.AlignmentFlag.AlignRight);layout.addWidget(history,1)
        self.update_listener_controls();self.update_notification_actions()

    def translate_notifications(self):
        self.notification_empty.set_text(self.t('notification_empty'),self.t('notification_empty_detail'))
        self.update_listener_controls();self.refresh_notifications()

    def update_listener_controls(self):
        self.listen_button.setText(self.t('stop' if self.listener_running else 'listen'))
        self.listen_button.setEnabled(not self.listener_pending)
        for widget in (self.listen_host,self.listen_port):widget.setEnabled(not self.listener_running and not self.listener_pending)
        state='listener_stopping' if self.listener_pending and self.listener_running else 'listener_starting' if self.listener_pending else 'listener_stopped'
        self.listener_status.setText(self.listener_error or (self.listener_endpoint if self.listener_running else self.t(state)))
        self.listener_status.setToolTip(self.listener_status.text())
        self.listener_status.setObjectName('muted');self.update_demo_notification_controls()

    def update_notification_actions(self):
        self.clear_notifications_button.setEnabled(self.notifications.rowCount()>0);self.notification_empty.refresh()

    def clear_notifications(self):
        self.notifications.setRowCount(0);self.update_notification_actions();self.show_notification_details()

    def toggle_listener(self):
        if self.listener_pending:return
        self.listener_pending=True;self.listener_error='';self.update_listener_controls()
        profiles=[(replace(d),self.credentials(d)) for d in self.config.devices]
        self.worker.submit('listener',self.worker.listen(self.listen_host.text().strip(),0 if self.listener_running else self.listen_port.value(),profiles))

    def update_demo_notification_controls(self):
        if not hasattr(self,'demo_notification_button'):return
        running=self.demo_agent is not None and self.demo_agent.running
        self.demo_notification_panel.setVisible(running)
        if not running:self.demo_notification_status.setText('')
        ready=running and self.listener_running and not self.listener_pending and 'demo-notify' not in self.pending
        self.demo_notification_button.setEnabled(ready);self.demo_notification_kind.setEnabled('demo-notify' not in self.pending)
        self.demo_notification_button.setToolTip(self.t('demo_notification_help'))

    def send_demo_notification(self):
        if self.demo_agent is None or not self.listener_running or self.listener_pending or 'demo-notify' in self.pending:return
        host=self.listen_host.text().strip()
        if host in ('','0.0.0.0'):host='127.0.0.1'
        elif host=='::':host='::1'
        self.pending.add('demo-notify');self.demo_notification_status.setText(self.t('request_running'));self.update_demo_notification_controls();self.update_demo_controls()
        self.worker.submit('demo-notify',self.worker.demo_notification(self.demo_agent,host,self.listen_port.value(),self.demo_notification_kind.currentText()))

    def add_notification_row(self,data,value):
        row=self.notifications.rowCount();self.notifications.insertRow(row)
        name,text,description=decode_value(self.mib.index,value,self.t('not_in_mib'))
        contents=[time.strftime('%H:%M:%S',time.localtime(data['time'])),data.get('type','—'),name,value.oid,text,data['address']]
        for column,content in enumerate(contents):
            item=QTableWidgetItem(content);item.setToolTip(description+'\n'+content if description else content)
            if column==0:item.setData(Qt.ItemDataRole.UserRole,(data,value))
            self.notifications.setItem(row,column,item)

    def on_notification(self,data):
        for value in data['values']:self.add_notification_row(data,value)
        while self.notifications.rowCount()>10000:self.notifications.removeRow(0)
        if self.notifications.currentRow()<0 and self.notifications.rowCount():self.notifications.selectRow(0)
        self.update_notification_actions()

    def refresh_notifications(self):
        records=[self.notifications.item(row,0).data(Qt.ItemDataRole.UserRole) for row in range(self.notifications.rowCount())]
        selected=self.notifications.currentRow();self.notifications.blockSignals(True);self.notifications.setRowCount(0)
        for data,value in records:self.add_notification_row(data,value)
        if 0<=selected<self.notifications.rowCount():self.notifications.selectRow(selected)
        self.notifications.blockSignals(False);self.show_notification_details()

    def show_notification_details(self):
        row=self.notifications.currentRow();item=self.notifications.item(row,0) if row>=0 else None
        if item is None:self.notification_details.setText(self.t('notification_detail_empty'));return
        data,value=item.data(Qt.ItemDataRole.UserRole);oid,name,description=notification_identity(self.mib.index,data,self.t('not_in_mib'))
        variable_description=decode_value(self.mib.index,value,self.t('not_in_mib'))[2]
        text=' · '.join(part for part in (data.get('type',''),name,oid) if part)
        if data.get('receiver')=='demo':text+=' · '+self.t('demo_notification_received')
        if description:text+='\n'+description
        if variable_description:text+='\n'+variable_description
        self.notification_details.setText(text);self.notification_details.setToolTip(text)

    def notification_journal(self):
        row=self.notifications.currentRow();item=self.notifications.item(row,0) if row>=0 else None
        transaction=item.data(Qt.ItemDataRole.UserRole)[0].get('transaction') if item else None
        self.navigate(5)
        if transaction:
            for index in range(self.logs.topLevelItemCount()):
                candidate=self.logs.topLevelItem(index);packet=self.packets.get(candidate.data(0,Qt.ItemDataRole.UserRole))
                if packet and packet.transaction==transaction:self.logs.setCurrentItem(candidate);self.logs.scrollToItem(candidate);break

    def build_journal(self):
        _,layout=self.page();bar=QHBoxLayout();self.log_search=QLineEdit();self.log_search.textChanged.connect(self.filter_logs);bar.addWidget(self.log_search,1);bar.addWidget(self.button('export',self.export_logs));bar.addWidget(self.button('clear',self.clear_logs));layout.addLayout(bar)
        self.reveal=self.bind(QCheckBox(),'reveal');self.reveal.toggled.connect(self.select_packet);layout.addWidget(self.reveal)
        split=QSplitter(Qt.Orientation.Vertical);self.logs=QTreeWidget();self.logs.setHeaderLabels(['Time','TX/RX','Device','Operation','Bytes','Transaction']);self.logs.currentItemChanged.connect(self.select_packet);self.logs.setRootIsDecorated(False);split.addWidget(self.logs)
        self.inspector=Inspector(self.config);split.addWidget(self.inspector);split.setSizes([260,440]);layout.addWidget(split,1)
    def on_packet(self,packet):
        if packet.id in self.packets:
            self.packets[packet.id]=packet
            current=self.logs.currentItem()
            if current and current.data(0,Qt.ItemDataRole.UserRole)==packet.id:self.select_packet()
            return
        self.packets[packet.id]=packet;self.packet_bytes+=len(packet.raw)
        d=next((d for d in self.config.devices if d.id==packet.device),None)
        item=QTreeWidgetItem([time.strftime('%H:%M:%S',time.localtime(packet.timestamp)),packet.direction,d.name if d else packet.device,packet.operation,str(len(packet.raw)),packet.transaction[:10]])
        item.setData(0,Qt.ItemDataRole.UserRole,packet.id);item.setToolTip(2,packet.address);self.logs.insertTopLevelItem(0,item)
        if self.log_search.text():self.filter_logs()
        while len(self.packets)>10000 or self.packet_bytes>100*1024*1024:
            _,old=self.packets.popitem(last=False);self.packet_bytes-=len(old.raw);self.logs.takeTopLevelItem(self.logs.topLevelItemCount()-1)
    def filter_logs(self,*args):
        query=self.log_search.text().casefold()
        for i in range(self.logs.topLevelItemCount()):
            item=self.logs.topLevelItem(i);packet=self.packets.get(item.data(0,Qt.ItemDataRole.UserRole));text=' '.join(item.text(c) for c in range(6))+' '+(packet.decoded if packet else '')
            item.setHidden(bool(query) and query not in text.casefold())
    def select_packet(self,*args):
        item=self.logs.currentItem();packet=self.packets.get(item.data(0,Qt.ItemDataRole.UserRole)) if item else None
        if packet:self.inspector.show_packet(packet,self.reveal.isChecked())
    def clear_logs(self):self.packets.clear();self.packet_bytes=0;self.logs.clear();self.inspector.tree.clear();self.inspector.wire.clear();self.inspector.decoded.clear();self.inspector.packet=None
    def export_logs(self):
        path,_=QFileDialog.getSaveFileName(self,self.t('export'),'snmp-traffic.json','JSON (*.json)')
        if not path:return
        from .ber import safe_hex
        try:
            # Exports always mask community, independently of the display toggle.
            records=[{'time':p.timestamp,'direction':p.direction,'address':p.address,'device':p.device,'operation':p.operation,'transaction':p.transaction,'hex':safe_hex(p.raw),'decoded':p.decoded} for p in self.packets.values()]
            from .storage import atomic_write
            atomic_write(path,json.dumps(records,ensure_ascii=False,indent=2))
        except Exception as exc:self.error(str(exc))

    def build_settings(self):
        _,layout=self.page();note=self.bind(QLabel(),'no_secrets');note.setWordWrap(True);layout.addWidget(note)
        bar=QHBoxLayout();bar.addWidget(self.button('export',self.export_config));bar.addWidget(self.button('import',self.import_config));bar.addStretch();layout.addLayout(bar)
        demo_card=QFrame();demo_card.setObjectName('card')
        demo_layout=QVBoxLayout(demo_card);demo_layout.setContentsMargins(18,16,18,16);demo_layout.setSpacing(12)
        demo_title=self.bind(QLabel(),'demo_agent');demo_title.setObjectName('sectionTitle');demo_layout.addWidget(demo_title)
        demo_info=self.bind(QLabel(),'demo_agent_info');demo_info.setWordWrap(True);demo_info.setObjectName('muted');demo_layout.addWidget(demo_info)
        demo_bar=QHBoxLayout();demo_bar.addWidget(QLabel('UDP'))
        self.demo_port=QSpinBox();self.demo_port.setRange(0,65535);self.demo_port.setValue(self.demo_agent.port if self.demo_agent else 1161);self.demo_port.lineEdit().setTextMargins(10,0,0,0);demo_bar.addWidget(self.demo_port)
        self.demo_dynamic=self.bind(QCheckBox(),'demo_simulate');self.demo_dynamic.setChecked(self.demo_agent.simulate if self.demo_agent else True);demo_bar.addWidget(self.demo_dynamic)
        demo_bar.addStretch()
        self.demo_toggle=self.button('start_demo_agent',self.toggle_demo_agent,True);demo_bar.addWidget(self.demo_toggle)
        self.demo_add=self.button('add_demo_monitor',self.add_demo_to_monitor);demo_bar.addWidget(self.demo_add)
        demo_layout.addLayout(demo_bar)
        self.demo_status=QLabel();self.demo_status.setObjectName('muted');demo_layout.addWidget(self.demo_status)
        layout.addWidget(demo_card)
        self.editor_enabled=QCheckBox();self.editor_enabled.setChecked(self.config.editor_enabled);self.editor_enabled.toggled.connect(self.change_editor_setting);layout.addWidget(self.editor_enabled)
        self.editor_note=QLabel();self.editor_note.setWordWrap(True);self.editor_note.setObjectName('muted');layout.addWidget(self.editor_note)
        details=QLabel(f'{APP_NAME} {__version__}\nSNMPv1 · SNMPv2c · SNMPv3\nApache License 2.0');details.setObjectName('muted');layout.addWidget(details);layout.addStretch()
    def update_demo_controls(self):
        running=self.demo_agent is not None and self.demo_agent.running
        self.demo_toggle.setText(self.t('stop_demo_agent' if running else 'start_demo_agent'));self.demo_toggle.setEnabled('demo-notify' not in self.pending)
        self.update_demo_notification_controls()
        self.demo_port.setEnabled(not running)
        self.demo_port.setSpecialValueText(self.t('automatic'))
        self.demo_dynamic.setEnabled(not running)
        self.demo_add.setEnabled(running)
        self.demo_status.setText((self.t('demo_listening')+' '+self.demo_agent.endpoint) if running else self.t('demo_stopped'))

    def toggle_demo_agent(self):
        if self.demo_agent is not None:
            self.demo_agent.close()
            self.demo_agent=None
        else:
            from .simulator import DemoAgent
            try:
                self.demo_agent=DemoAgent(port=self.demo_port.value(),simulate=self.demo_dynamic.isChecked())
                self.demo_port.setValue(self.demo_agent.port)
                self.prepare_demo_device()
            except (OSError,ValueError) as exc:
                self.error(str(exc))
        self.update_demo_controls()

    def prepare_demo_device(self):
        if self.demo_agent is None or not self.demo_agent.running:return None
        from .simulator import DEMO_DEVICE_ID,DEMO_MIB,demo_configuration
        self.demo_agent.notification_callback=self.worker.notification.emit;self.demo_agent.packet_callback=self.worker.packet.emit
        device=next((d for d in self.config.devices if d.id==DEMO_DEVICE_ID),None)
        if device is None:
            device=demo_configuration(self.demo_agent.port,self.config.language,self.config.theme).devices[0]
            self.config.devices.append(device)
        device.host=self.demo_agent.host;device.port=self.demo_agent.port;device.version='2c';device.enabled=True
        self.secrets.session[f'{device.id}:community']=self.demo_agent.read_community
        self.secrets.session[f'{device.id}:write_community']=self.demo_agent.write_community
        self.mib.open_path(DEMO_MIB)
        self.refresh_devices()
        self.mib.query.device.setCurrentIndex(self.mib.query.device.findData(device.id))
        self.render_monitor();self.save_config()
        return device

    def add_demo_to_monitor(self):
        device=self.prepare_demo_device()
        if device is None:return
        from .simulator import demo_configuration
        template=demo_configuration(self.demo_agent.port,self.config.language,self.config.theme)
        for parameter in template.parameters:
            if not any(p.device==device.id and p.oid==parameter.oid for p in self.config.parameters):
                parameter.device=device.id
                self.config.parameters.append(parameter)
        self.render_monitor();self.save_config();self.navigate(0)
        # Only the local demo device is read; existing devices keep their polling state.
        job=f'poll:{device.id}'
        if job not in self.pending:
            parameters=[replace(p) for p in self.config.parameters if p.device==device.id and p.enabled]
            self.pending.add(job)
            self.worker.submit(job,self.worker.read(replace(device),self.credentials(device),parameters))

    def update_editor_setting(self):
        self.editor_enabled.setText('Включить редактор MIB' if self.config.language=='ru' else 'Enable MIB editor')
        self.editor_note.setText('По умолчанию доступен просмотр MIB. Включите редактор, чтобы в левом меню появился отдельный пункт «MIB-редактор» для изменения исходного текста.' if self.config.language=='ru' else 'MIB browsing is available by default. Enable the editor to add a separate MIB editor page to the left menu for editing source files.')
        self.mib.set_editing_enabled(self.config.editor_enabled)
        self.editor_nav.setVisible(self.config.editor_enabled)
        if not self.config.editor_enabled and self.stack.currentIndex()==self.editor_page_index:self.navigate(2)
    def change_editor_setting(self,enabled):
        if not enabled and not self.mib.confirm_close():
            self.editor_enabled.blockSignals(True);self.editor_enabled.setChecked(True);self.editor_enabled.blockSignals(False);return
        if not enabled:self.mib.discard_unsaved()
        self.config.editor_enabled=enabled;self.update_editor_setting();self.save_config()
    def export_config(self):
        path,_=QFileDialog.getSaveFileName(self,self.t('export'),'snmpstudio.json','JSON (*.json)')
        if path:
            try:self.store.export(self.config,path)
            except Exception as exc:self.error(str(exc))
    def import_config(self):
        if self.pending:self.error(self.t('wait_request'));return
        path,_=QFileDialog.getOpenFileName(self,self.t('import'),'','JSON (*.json)')
        if not path:return
        try:
            imported=Store.read_import(path)
            if QMessageBox.question(self,self.t('import'),f'{len(imported.devices)} devices, {len(imported.parameters)} OID\n'+('Заменить конфигурацию?' if self.config.language=='ru' else 'Replace configuration?'))!=QMessageBox.StandardButton.Yes:return
            if not self.mib.confirm_close():return
            self.store.backup(Path(path).with_suffix('.before-import.db'));self.polling=False
            # Preserve the shared configuration object used by all views.
            self.config.__dict__.update(imported.__dict__);self.values.clear();self.due.clear()
            for i in reversed(range(self.mib.tabs.count())):self.mib.tabs.removeTab(i)
            for source in self.config.mibs:
                if Path(source).is_file():self.mib.open_path(source)
            self.mib.validate();self.language.blockSignals(True);self.language.setCurrentIndex(self.language.findData(self.config.language));self.language.blockSignals(False)
            self.editor_enabled.blockSignals(True);self.editor_enabled.setChecked(self.config.editor_enabled);self.editor_enabled.blockSignals(False)
            self.command_search.clear();self.command_table.clearSelection();self.clear_command_result()
            self.render_commands()
            self.refresh_devices();self.translate();self.update_theme();self.save_config()
        except Exception as exc:self.error(str(exc))

    def on_result(self,job,result,error):
        self.pending.discard(job)
        if job.startswith('poll:'):
            if error:return
            for identifier,value in result.items():
                saved=self.values.setdefault(identifier,{})
                if isinstance(value,Value) and not value.error:saved.update(value=value,time=time.time(),error='')
                else:saved['error']=value.error if isinstance(value,Value) else str(value or 'Missing response OID')
            self.render_monitor()
        elif job=='mib-query':
            self.mib.tree.setEnabled(True)
            context=self.mib_request_context
            if context:self.mib.query.result_context=context;self.mib.query.render_context()
            is_set=bool(context and context[2]=='SET')
            values=(result or {}).get('response',[]) if is_set else (result or [])
            if context and not is_set and not error and not self.mib_request_cancelled:
                self.mib.record_response(context[0],context[2],context[3],values)
            self.mib.query.finish(values,error,cancelled=self.mib_request_cancelled)
            if context and context[2]=='GETNEXT' and context[0]==self.mib.query.device.currentData() and not self.mib_request_cancelled and self.mib.query.state=='request_success':
                next_value=next((value for value in (result or []) if not value.error),None)
                if next_value:self.mib.advance_next(next_value.oid,context[3])
            self.mib_request_context=None
        elif job=='set':
            self.update_command_actions()
            failures=[value.error for values in (result or {}).values() for value in values if value.error]
            self.command_state='error' if error or failures else 'request_success'
            if error:self.command_result.setPlainText(error)
            else:self.command_result.setPlainText('\n\n'.join(self.t(key)+'\n'+'\n'.join(f'{v.oid} · {v.kind} = {v.error or Parameter().formatted(v.value,v.kind)}' for v in values) for key,values in result.items()))
            self.render_command_state();self.update_command_result_actions()
        elif job=='demo-notify':
            self.demo_notification_status.setText((self.t('error')+': '+error) if error else self.t('inform_acknowledged') if result.get('acknowledged') else self.t('trap_sent'))
            self.update_demo_notification_controls();self.update_demo_controls()
        elif job=='listener':
            self.listener_pending=False;self.listener_error=error
            self.listener_running=not error and result!='Stopped'
            self.listener_endpoint=result if self.listener_running else ''
            self.update_listener_controls()

    def closeEvent(self,event):
        if not self.mib.confirm_close():event.ignore();return
        self.timer.stop();self.polling=False;
        if self.demo_agent is not None:self.demo_agent.close();self.demo_agent=None
        self.mib.shutdown();self.worker.stop();self.save_config();QSettings().setValue('geometry',self.saveGeometry());event.accept()
