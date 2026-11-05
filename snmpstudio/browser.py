# SPDX-License-Identifier: Apache-2.0
"""SNMP operations and their results inside the MIB browser."""
from datetime import datetime
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QLineEdit, QSpinBox, QPushButton, QTableWidget, QTableWidgetItem, QHeaderView,
    QAbstractItemView, QTextBrowser, QFrame, QSizePolicy)
from .i18n import tr
from .models import Parameter
from .protocol import SET_TYPES, mib_set_type
from .widgets import ComboBox, field, EmptyState, ElidedLabel
from .theme import icon


class BrowserQuery(QWidget):
    requested = Signal(str, str, str, int)
    set_requested = Signal(str,str,str,str)
    cancel_requested = Signal()
    add_requested = Signal(str, str)
    write_requested = Signal(str,str)
    journal_requested = Signal()
    add_device_requested = Signal()
    device_selected = Signal(str)

    def __init__(self, config, index):
        super().__init__()
        self.config=config;self.parent_index=index;self.refs=[];self.busy=False;self.values=[]
        self.object=None;self.default_oid='';self.result_context=None;self.state='';self.observed_values={};self._set_target_oid=''
        layout=QVBoxLayout(self);layout.setSizeConstraint(QVBoxLayout.SizeConstraint.SetMinimumSize);layout.setContentsMargins(0,0,0,0);layout.setSpacing(12)
        self.setup=setup=QFrame();setup.setObjectName('card');setup.setSizePolicy(QSizePolicy.Policy.Preferred,QSizePolicy.Policy.Fixed);setup_layout=QVBoxLayout(setup)
        setup_layout.setSizeConstraint(QVBoxLayout.SizeConstraint.SetMinimumSize)
        setup_layout.setContentsMargins(14,12,14,12);setup_layout.setSpacing(10)
        row=QHBoxLayout();row.setSpacing(12)
        self.device=ComboBox();device_group=self.make_field('device',self.device)
        self.add_device=QPushButton();self.bind(self.add_device,'add_device');self.add_device.setObjectName('ghost')
        self.add_device.clicked.connect(self.add_device_requested.emit)
        row.addWidget(device_group,1)
        self.full_walk_button=QPushButton();self.bind(self.full_walk_button,'walk_device');self.full_walk_button.setFixedSize(185,38)
        self.full_walk_button.clicked.connect(lambda:self.emit_request('WALK_ALL'));row.addWidget(self.full_walk_button,0,Qt.AlignmentFlag.AlignBottom)
        setup_layout.addLayout(row);setup_layout.addWidget(self.add_device,0,Qt.AlignmentFlag.AlignLeft)
        self.operation=ComboBox()
        for text,value in [('GetRequest','GET'),('GetNextRequest','GETNEXT'),('GetBulkRequest','GETBULK'),('SetRequest','SET')]:self.operation.addItem(text,value)
        operation_group=self.make_field('request_type',self.operation);operation_group.setFixedWidth(185)
        row=QHBoxLayout();row.setSpacing(12)
        self.oid=QLineEdit();self.oid.setFixedHeight(38);self.oid.setPlaceholderText('1.3.6.1.2.1.1.1.0');self.oid.returnPressed.connect(self.send)
        row.addWidget(self.make_field('OID',self.oid),1);row.addWidget(operation_group)
        self.max_repetitions=QSpinBox();self.max_repetitions.setRange(1,100);self.max_repetitions.setValue(20);self.max_repetitions.setFixedHeight(38);self.max_repetitions.lineEdit().setTextMargins(10,0,0,0)
        self.bulk_label=QLabel();self.bind(self.bulk_label,'max_repetitions')
        self.bulk_field=field(self.bulk_label,self.max_repetitions);self.bulk_field.setFixedWidth(145);row.addWidget(self.bulk_field)
        setup_layout.addLayout(row)
        self.set_fields=QWidget();set_layout=QHBoxLayout(self.set_fields);set_layout.setContentsMargins(0,0,0,0);set_layout.setSpacing(12);set_layout.setSizeConstraint(QHBoxLayout.SizeConstraint.SetMinimumSize)
        self.set_type=ComboBox();self.set_type.addItems(SET_TYPES)
        type_field=self.make_field('type',self.set_type);type_field.setFixedWidth(185);set_layout.addWidget(type_field)
        self.set_value=QLineEdit();self.set_value.setFixedHeight(38);self.set_value.setClearButtonEnabled(True);self.set_value.returnPressed.connect(self.send)
        set_layout.addWidget(self.make_field('value',self.set_value),1);setup_layout.addWidget(self.set_fields)
        self.set_note=QLabel();self.bind(self.set_note,'set_values_note');self.set_note.setObjectName('muted');self.set_note.setWordWrap(False);setup_layout.addWidget(self.set_note)
        row=QHBoxLayout()
        self.send_button=QPushButton();self.bind(self.send_button,'send_request');self.send_button.setObjectName('primary');self.send_button.clicked.connect(self.send);row.addWidget(self.send_button)
        self.walk_button=QPushButton();self.bind(self.walk_button,'walk');self.walk_button.clicked.connect(self.walk);row.addWidget(self.walk_button)
        row.addStretch();self.cancel_button=QPushButton();self.bind(self.cancel_button,'cancel');self.cancel_button.clicked.connect(self.cancel_requested.emit);row.addWidget(self.cancel_button)
        cancel_policy=self.cancel_button.sizePolicy();cancel_policy.setRetainSizeWhenHidden(True);self.cancel_button.setSizePolicy(cancel_policy)
        setup_layout.addLayout(row);layout.addWidget(setup)

        results=QFrame();results.setObjectName('card');result_layout=QVBoxLayout(results)
        result_layout.setContentsMargins(14,10,14,12);result_layout.setSpacing(8)
        row=QHBoxLayout();label=QLabel();self.bind(label,'request_result');label.setObjectName('sectionTitle');row.addWidget(label)
        row.addStretch();self.status=QLabel();row.addWidget(self.status)
        journal=QPushButton();journal.setFixedSize(34,34);journal.setObjectName('ghost');journal.setIcon(icon('file'))
        journal.clicked.connect(self.journal_requested.emit);journal.setToolTip(self.t('journal'));journal.setAccessibleName(self.t('journal'));self.journal_button=journal;row.addWidget(journal)
        result_layout.addLayout(row)
        self.context=ElidedLabel();self.context.setObjectName('muted');result_layout.addWidget(self.context)
        self.table=QTableWidget(0,5);self.table.setShowGrid(False);self.table.setWordWrap(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows);self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.verticalHeader().hide();self.table.verticalHeader().setDefaultSectionSize(38)
        header=self.table.horizontalHeader();header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive);header.setStretchLastSection(False)
        header.setSectionResizeMode(2,QHeaderView.ResizeMode.ResizeToContents);header.setSectionResizeMode(3,QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0,130);self.table.setColumnWidth(1,205);self.table.setColumnWidth(4,145);self.table.setColumnHidden(4,True)
        self.table.setMinimumHeight(96);result_layout.addWidget(self.table,1);self.empty=EmptyState(self.table)
        self.error_panel=QWidget();error_layout=QHBoxLayout(self.error_panel);error_layout.setContentsMargins(0,0,0,0)
        self.error_text=QTextBrowser();self.error_text.setMinimumHeight(36);self.error_text.setMaximumHeight(72);self.error_text.setOpenExternalLinks(False)
        error_layout.addWidget(self.error_text,1)
        self.close_error=QPushButton('×');self.close_error.setObjectName('ghost');self.close_error.setFixedSize(28,28)
        self.close_error.clicked.connect(self.error_panel.hide);error_layout.addWidget(self.close_error,0,Qt.AlignmentFlag.AlignTop)
        self.error_panel.hide();result_layout.addWidget(self.error_panel)
        self.add_button=QPushButton(self);self.bind(self.add_button,'add_monitor');self.add_button.setIcon(icon('plus'));self.add_button.clicked.connect(self.add_selected)
        self.write_button=QPushButton(self);self.bind(self.write_button,'add_command');self.write_button.setIcon(icon('bolt'));self.write_button.clicked.connect(self.add_to_commands)
        layout.addWidget(results,1)
        self.operation.currentIndexChanged.connect(self.update_actions);self.device.currentIndexChanged.connect(self.device_changed)
        self.oid.textChanged.connect(self.oid_changed);self.table.itemSelectionChanged.connect(self.update_actions)
        self.set_type.currentTextChanged.connect(self.update_actions);self.set_value.textChanged.connect(self.update_actions)
        self.refresh_devices();self.translate();self.update_actions()

    def make_field(self,key,control):
        label=QLabel();self.bind(label,key)
        if key in ('device','OID','request_type'):label.setContentsMargins(10,0,0,0)
        return field(label,control)

    def t(self,key):return tr(key,self.config.language)
    def bind(self,widget,key):self.refs.append((widget,key));widget.setText(self.t(key))

    def translate(self):
        for widget,key in self.refs:widget.setText(self.t(key))
        self.table.setHorizontalHeaderLabels([self.t('name'),'OID',self.t('type'),self.t('value'),self.t('error')])
        self.journal_button.setToolTip(self.t('journal'));self.journal_button.setAccessibleName(self.t('journal'))
        self.device.setPlaceholderText(self.t('no_devices'))
        self.set_value.setPlaceholderText(self.t('set_value_placeholder'))
        self.empty.set_text(self.t('query_empty_title'),self.t('request_empty'))
        self.close_error.setToolTip(self.t('close_error'));self.close_error.setAccessibleName(self.t('close_error'))
        self.render_status()
        if not self.result_context:self.context.setText(self.t('request_empty'))
        else:self.render_context()
        if not self.values:
            if self.state=='error':self.empty.set_text(self.t('no_response'))
            elif self.state=='request_success':self.empty.set_text(self.t('no_values_title'),self.t('no_values'))
        for row,value in enumerate(self.values):
            if not self.parent_index.describe_instance(value.oid):self.table.item(row,0).setText(self.t('not_in_mib'))
            if value.error=='EndOfMibView':self.table.item(row,3).setText(self.t('end_of_view'))
        self.update_actions()

    def refresh_devices(self):
        previous=self.device.currentData();self.device.blockSignals(True);self.device.clear()
        for device in self.config.devices:self.device.addItem(device.name,device.id)
        selected=self.device.findData(previous)
        self.device.setCurrentIndex(selected if selected>=0 else 0 if self.device.count() else -1)
        self.device.blockSignals(False);self.device_changed()

    def device_changed(self,*args):
        if not self.busy and self.result_context and self.result_context[0]!=self.device.currentData():self.clear_result()
        self.update_actions();self.device_selected.emit(self.device.currentData() or '')

    def oid_changed(self,text):
        target=text.strip()
        if target!=self._set_target_oid and not self.busy:
            self.set_value.clear()
            try:oid=self.parent_index.resolve(target)
            except ValueError:oid=''
            obj=self.parent_index.describe_instance(oid) if oid else None
            observed=self.observed_values.get(oid)
            kind=mib_set_type(obj) if obj else ('Integer32' if observed and observed.kind=='Integer' else observed.kind) if observed else 'Integer32'
            self.set_type.setCurrentText(kind if kind in SET_TYPES else 'Integer32')
        self._set_target_oid=target;self.update_actions()

    def target(self):
        value=self.selected_value()
        raw=value.oid if value else self.oid.text().strip()
        try:oid=self.parent_index.resolve(raw)
        except ValueError:return '',None,value
        value=value or self.observed_values.get(oid)
        return oid,self.parent_index.describe_instance(oid),value

    def can_read_oid(self,oid,obj=None,value=None):
        value=value or self.observed_values.get(oid)
        obj=obj or self.parent_index.describe_instance(oid)
        if not oid:return False
        if obj and obj.kind=='OBJECT-TYPE':return (oid!=obj.oid or value is not None and not value.error) and obj.access in ('read-only','read-write','read-create')
        read_response=self.result_context and self.result_context[2]!='SET'
        return bool(value is not None and not value.error and (oid in self.observed_values or read_response))

    def can_write_oid(self,oid,obj=None,value=None):
        obj=obj or self.parent_index.describe_instance(oid)
        return bool(oid and obj and obj.kind=='OBJECT-TYPE' and oid!=obj.oid and obj.access in ('read-write','read-create','write-only') )

    def update_actions(self,*args):
        operation=self.operation.currentData();bulk=operation=='GETBULK';is_set=operation=='SET'
        self.bulk_field.setVisible(bulk);self.set_fields.setVisible(is_set);self.set_note.setVisible(is_set)
        self.send_button.setText(self.t('send' if is_set else 'send_request'))
        device=next((d for d in self.config.devices if d.id==self.device.currentData()),None)
        v1=device is not None and device.version=='1'
        self.operation.model().item(2).setEnabled(not v1)
        if v1 and bulk:
            self.operation.setCurrentIndex(0);return
        ready=bool(device and self.oid.text().strip()) and not self.busy
        has_value=bool(self.set_value.text().strip()) or self.set_type.currentText() in ('OctetString','HexString')
        self.send_button.setEnabled(ready and (not is_set or has_value));self.walk_button.setEnabled(ready)
        self.full_walk_button.setEnabled(bool(device) and not self.busy)
        cancellable=self.busy and not (self.result_context and self.result_context[2]=='SET')
        self.cancel_button.setEnabled(cancellable);self.cancel_button.setVisible(cancellable)
        oid,obj,value=self.target()
        readable=self.can_read_oid(oid,obj,value) and not self.busy
        writable=self.can_write_oid(oid,obj,value) and not self.busy
        self.add_button.setEnabled(readable);self.add_button.setVisible(readable)
        self.write_button.setEnabled(writable);self.write_button.setVisible(writable)
        self.add_device.setVisible(not self.config.devices)
        self.add_device.setEnabled(not self.busy)
        for widget in (self.device,self.operation,self.oid,self.max_repetitions,self.set_type,self.set_value):widget.setEnabled(not self.busy)

    def select_object(self,obj,default_oid,*,preserve_result=False):
        key=lambda x:(x.module,x.name,x.oid) if x else None
        if key(obj)==key(self.object):
            self.object=obj;self.update_actions();return
        self.object=obj;self.default_oid=default_oid
        self.oid.setText(default_oid)
        if not preserve_result:self.clear_result()

    def clear_result(self):
        self.values=[];self.table.setRowCount(0);self.table.setColumnHidden(4,True);self.result_context=None;self.state=''
        self.error_panel.hide();self.context.setText(self.t('request_empty'));self.empty.set_text(self.t('query_empty_title'),self.t('request_empty'));self.render_status()

    def send(self):self.emit_request(self.operation.currentData())
    def walk(self):self.emit_request('WALK')
    def emit_request(self,operation):
        if self.busy:return
        oid=self.oid.text().strip()
        if operation=='SET':
            self.set_requested.emit(self.device.currentData() or '',oid,self.set_type.currentText(),self.set_value.text());return
        if operation=='WALK_ALL':oid='0.0'
        if operation=='WALK' and self.object and oid==self.default_oid:oid=self.object.oid
        self.requested.emit(self.device.currentData() or '',operation,oid,self.max_repetitions.value())

    def begin(self,device,operation,oid):
        self.clear_result();self.result_context=(device.id,device.name,operation,oid,datetime.now().strftime('%H:%M:%S'))
        self.render_context();self.busy=True;self.state='request_running'
        self.render_status();self.update_actions()

    def render_context(self):
        if self.result_context:
            _,name,operation,oid,stamp=self.result_context
            parts=[name,self.t('walk_device'),stamp] if operation=='WALK_ALL' else [name,operation,oid,stamp]
            self.context.setText(' · '.join(parts))

    def finish(self,values,error='',cancelled=False):
        self.busy=False;self.values=values or [];self.table.setRowCount(len(self.values))
        errors=[]
        for row,value in enumerate(self.values):
            obj=self.parent_index.describe_instance(value.oid)
            text=self.t('end_of_view') if value.error=='EndOfMibView' else Parameter().formatted(value.value,value.kind) if not value.error else '—'
            if obj and isinstance(value.value,int) and value.value in obj.enums:text=f'{obj.enums[value.value]} ({value.value})'
            if obj and obj.units and not value.error:text+=' '+obj.units
            name=obj.name if obj and obj.kind=='OBJECT-TYPE' else self.t('not_in_mib')
            for column,content in enumerate([name,value.oid,value.kind,text,value.error if value.error!='EndOfMibView' else '']):
                item=QTableWidgetItem(content);item.setToolTip(content);self.table.setItem(row,column,item)
            if value.error and value.error!='EndOfMibView':errors.append(f'{value.oid}: {value.error}')
        self.table.setColumnHidden(4,not errors)
        if cancelled:
            self.show_error(self.t('request_cancelled'))
        elif error or errors:
            self.state='error';self.show_error(error or '\n'.join(errors))
        else:
            self.state='request_success'
            if not self.values:
                self.context.setText(self.context.toolTip()+' · '+self.t('no_values'));self.empty.set_text(self.t('no_values_title'),self.t('no_values'))
        self.render_status();self.update_actions()

    def render_status(self):
        self.status.setText(self.t(self.state) if self.state else '')
        dark=self.config.theme=='dark'
        color=('#F39492' if dark else '#B5332E') if self.state=='error' else ('#5DD8BA' if dark else '#087D69') if self.state=='request_success' else '#8490A2'
        self.status.setStyleSheet(f'color: {color}; font-weight: 700;')

    def show_error(self,error):
        self.state='error';self.error_text.setPlainText(str(error));self.error_panel.show();self.empty.set_text(self.t('no_response'));self.render_status()

    def selected_value(self):
        row=self.table.currentRow()
        return self.values[row] if 0<=row<len(self.values) else None

    def add_selected(self):
        oid,obj,value=self.target()
        if not self.can_read_oid(oid,obj,value):return
        device=self.result_context[0] if value and self.result_context else self.device.currentData() or ''
        self.add_requested.emit(oid,device)

    def add_to_commands(self):
        oid,obj,value=self.target()
        if not self.can_write_oid(oid,obj,value):return
        device=self.result_context[0] if value and self.result_context else self.device.currentData() or ''
        self.write_requested.emit(oid,device)
