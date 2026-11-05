# SPDX-License-Identifier: Apache-2.0
from . import APP_NAME
from PySide6.QtCore import Qt
from dataclasses import replace
from copy import deepcopy
from PySide6.QtWidgets import (QDialog,QVBoxLayout,QFormLayout,QDialogButtonBox,QLineEdit,QComboBox,
    QDoubleSpinBox,QSpinBox,QCheckBox,QMessageBox,QScrollArea,QWidget,QLabel,QHBoxLayout)
from .models import Device,Parameter,uid
from .protocol import SET_TYPES,validate_set_row
from .i18n import tr
from .widgets import ComboBox


class Form(QDialog):
    def __init__(self,language,title,parent=None):
        super().__init__(parent);self.language=language;self.setWindowTitle(title);self.resize(540,650)
        self.outer=QVBoxLayout(self);scroll=QScrollArea();scroll.setWidgetResizable(True)
        body=QWidget();self.form=QFormLayout(body);self.form.setSpacing(12);scroll.setWidget(body);self.outer.addWidget(scroll)
        self.buttons=buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(self.t('apply'))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(self.t('cancel'))
        buttons.accepted.connect(self.accept);buttons.rejected.connect(self.reject);self.outer.addWidget(buttons)
    def t(self,key):return tr(key,self.language)
    def line(self,key,value='',password=False):
        widget=QLineEdit(str(value));widget.setEchoMode(QLineEdit.EchoMode.Password if password else QLineEdit.EchoMode.Normal)
        self.form.addRow(self.t(key),widget);return widget
    def combo(self,key,values,current):
        widget=ComboBox();widget.addItems(values);widget.setCurrentText(current);self.form.addRow(self.t(key),widget);return widget
    def number(self,key,value,low,high,integer=False):
        widget=QSpinBox() if integer else QDoubleSpinBox();widget.setRange(low,high);widget.setValue(value);widget.lineEdit().setTextMargins(10,0,0,0)
        if not integer:widget.setDecimals(2)
        self.form.addRow(self.t(key),widget);return widget


class DeviceDialog(Form):
    def __init__(self,config,secrets,device=None,parent=None):
        super().__init__(config.language,tr('edit' if device else 'add_device',config.language),parent)
        self.device=replace(device) if device else Device();d=self.device;self.secret_store=secrets
        self.name=self.line('name',d.name);self.host=self.line('host',d.host);self.port=self.number('UDP port',d.port,1,65535,True)
        self.version=self.combo('SNMP',['1','2c','3'],d.version)
        self.community=self.line('Community',secrets.get(d.id,'community'),True)
        self.write_community=self.line('write_community',secrets.get(d.id,'write_community'),True)
        self.username=self.line('username',d.username);self.security=self.combo('security',['noAuthNoPriv','authNoPriv','authPriv'],d.security)
        self.auth=self.combo('auth',['SHA256','SHA512','SHA1','MD5'],d.auth)
        self.privacy=self.combo('privacy',['AES128','DES'],d.privacy)
        self.auth_password=self.line('auth_password',secrets.get(d.id,'auth_password'),True)
        self.priv_password=self.line('priv_password',secrets.get(d.id,'priv_password'),True)
        self.context=self.line('contextName',d.context)
        self.context_engine_id=self.line('Context Engine ID (HEX)',d.context_engine_id)
        self.engine_id=self.line('Trap Engine ID (HEX)',d.engine_id)
        self.period=self.number('period',d.period,.1,86400);self.timeout=self.number('timeout',d.timeout,.1,60)
        self.retries=self.number('retries',d.retries,0,5,True);self.batch=self.number('batch',d.batch,1,128,True)
        self.enabled=QCheckBox(self.t('enabled'));self.enabled.setChecked(d.enabled);self.form.addRow(self.enabled)
        self.persist=QCheckBox(self.t('persist'));self.form.addRow(self.persist)
        self.version.currentTextChanged.connect(self.update_fields);self.security.currentTextChanged.connect(self.update_fields);self.update_fields()
    def update_fields(self,*args):
        v3=self.version.currentText()=='3';auth=v3 and self.security.currentText()!='noAuthNoPriv';privacy=v3 and self.security.currentText()=='authPriv'
        for widget,show in [(self.community,not v3),(self.write_community,not v3),(self.username,v3),(self.security,v3),(self.auth,auth),(self.auth_password,auth),(self.privacy,privacy),(self.priv_password,privacy),(self.context,v3),(self.context_engine_id,v3),(self.engine_id,v3)]:
            widget.setVisible(show);self.form.labelForField(widget).setVisible(show)
    def accept(self):
        d=self.device
        try:
            for key in ('name','host','username','context','context_engine_id','engine_id'):setattr(d,key,getattr(self,key).text().strip())
            for key in ('version','security','auth','privacy'):setattr(d,key,getattr(self,key).currentText())
            for key in ('port','period','timeout','retries','batch'):setattr(d,key,getattr(self,key).value())
            d.enabled=self.enabled.isChecked();d.validate()
            failed=False
            for key in ('community','write_community','auth_password','priv_password'):
                if not self.secret_store.set(d.id,key,getattr(self,key).text(),self.persist.isChecked()):failed=True
            if failed:QMessageBox.information(self,APP_NAME,'Системное хранилище недоступно. Секреты сохранены только до закрытия приложения.' if self.language=='ru' else 'System keyring unavailable. Credentials are available only for this session.')
            super().accept()
        except Exception as exc:QMessageBox.warning(self,self.t('error'),str(exc))


class ParameterDialog(Form):
    def __init__(self,config,index,parameter=None,obj=None,parent=None,*,lock_oid=False):
        super().__init__(config.language,tr('edit' if parameter else 'add_parameter',config.language),parent)
        self.parameter=replace(parameter) if parameter else Parameter();self.index=index
        p=self.parameter
        self.device=ComboBox()
        for d in config.devices:self.device.addItem(d.name,d.id)
        self.device.setCurrentIndex(max(0,self.device.findData(p.device)));self.form.addRow(self.t('device'),self.device)
        self.name=self.line('name',obj.name if obj else p.name)
        self.oid=self.line('OID',index.instance_oid(obj) if obj else p.oid)
        self.oid.setReadOnly(lock_oid);self.fixed_oid=self.oid.text() if lock_oid else None
        self.unit=self.line('unit',obj.units if obj else p.unit)
        self.factor=self.line('factor',p.factor)
        self.decimal_shift=self.number('Сдвиг запятой' if self.language=='ru' else 'Decimal shift',0,-12,12,True)
        from decimal import Decimal
        for shift in range(-12,13):
            if Decimal(p.factor)==Decimal(10)**(-shift):self.decimal_shift.setValue(shift);break
        self.decimal_shift.valueChanged.connect(lambda value:self.factor.setText(str(Decimal(10)**(-value))))
        self.offset=self.line('offset',p.offset)
        self.decimals=self.number('decimals',p.decimals,0,12,True)
        self.period=self.number('period',p.period,0,86400);self.period.setSpecialValueText('Устройства' if self.language=='ru' else 'Device default')
        self.display=self.combo('display',['auto','text','hex','duration'],p.display)
        self.enabled=QCheckBox(self.t('enabled'));self.enabled.setChecked(p.enabled);self.form.addRow(self.enabled)
    def accept(self):
        try:
            p=self.parameter;p.device=self.device.currentData()
            if not p.device:raise ValueError('Add a device first')
            for key in ('name','unit','factor','offset'):setattr(p,key,getattr(self,key).text().strip())
            p.oid=self.index.resolve(self.fixed_oid if self.fixed_oid is not None else self.oid.text());p.period=self.period.value();p.decimals=self.decimals.value();p.display=self.display.currentText();p.enabled=self.enabled.isChecked();p.validate();super().accept()
        except Exception as exc:QMessageBox.warning(self,self.t('error'),str(exc))


class CommandDialog(Form):
    """Create or edit a saved SET, with optional fixed instance OIDs."""
    def __init__(self, config, index, command, parent=None, *, on_save=None, creating=False, lock_oid=True):
        super().__init__(config.language, tr(('add_command' if lock_oid else 'new_command') if creating else 'edit_command', config.language), parent)
        self.resize(580, 470)
        self.command=deepcopy(command);self.index=index;self.on_save=on_save
        self.command.setdefault('id',uid())
        self.name=self.line('command_name',self.command['name']);self.name.setMaxLength(200)
        self.device=ComboBox();self.device.setPlaceholderText(self.t('choose_device'))
        for device in config.devices:self.device.addItem(device.name,device.id)
        self.device.setCurrentIndex(self.device.findData(self.command.get('device')))
        self.form.addRow(self.t('device'),self.device)
        self.row_fields=[];self.fixed_oids=[]
        for row in self.command['rows']:
            oid=self.line('OID',row['oid']);oid.setReadOnly(lock_oid);oid.setClearButtonEnabled(not lock_oid)
            oid.setPlaceholderText('1.3.6.1.2.1.1.5.0');self.fixed_oids.append(row['oid'] if lock_oid else None)
            kind=self.combo('type',SET_TYPES,row['type'])
            value=self.line('value',row['value']);value.setPlaceholderText(self.t('set_value_placeholder'))
            value.setClearButtonEnabled(True);self.row_fields.append((oid,kind,value))
        if self.row_fields:self.oid,self.type,self.value=self.row_fields[0]
        self.verify=QCheckBox(self.t('verify'));self.verify.setChecked(self.command.get('verify',False));self.form.addRow(self.verify)
        note=QLabel(self.t('set_values_note'));note.setObjectName('muted');note.setWordWrap(True);self.form.addRow(note)
        self.error_panel=QWidget();bar=QHBoxLayout(self.error_panel);bar.setContentsMargins(0,0,0,0)
        self.error_text=QLabel();self.error_text.setTextFormat(Qt.TextFormat.PlainText)
        self.error_text.setWordWrap(True);bar.addWidget(self.error_text,1)
        from PySide6.QtWidgets import QPushButton
        close=QPushButton('×');close.setObjectName('ghost');close.setFixedSize(28,28);close.setToolTip(self.t('close_error'));close.setAccessibleName(self.t('close_error'));close.clicked.connect(self.error_panel.hide);bar.addWidget(close)
        self.error_panel.hide();self.outer.insertWidget(1,self.error_panel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText(self.t('save_command'))
        self.name.setFocus();self.name.selectAll()

    def accept(self):
        try:
            name=self.name.text().strip()
            if not name:raise ValueError(self.t('command_name_required'))
            if not self.device.currentData():raise ValueError(self.t('choose_device'))
            rows=[validate_set_row(self.index,fixed if fixed is not None else oid.text(),kind.currentText(),value.text())
                for fixed,(oid,kind,value) in zip(self.fixed_oids,self.row_fields)]
            if not rows:raise ValueError(self.t('command_no_oid'))
            command={**self.command,'name':name,'device':self.device.currentData(),'rows':rows,'verify':self.verify.isChecked()}
            if self.on_save:self.on_save(command)
            self.command=command;super().accept()
        except Exception as exc:
            self.error_text.setText(str(exc));self.error_panel.show()
