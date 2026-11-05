# SPDX-License-Identifier: Apache-2.0
"""Visual editor for declarations, sharing the source editor's undo/save lifecycle."""
from datetime import datetime,timezone
from PySide6.QtCore import Qt,QTimer
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QFormLayout,QTreeWidget,QTreeWidgetItem,
    QLineEdit,QPlainTextEdit,QLabel,QPushButton,QScrollArea,QSplitter,QMessageBox,QFrame)
from .widgets import ComboBox
from .mib import parts
from .mib_builder import BuilderDocument,KINDS,SYNTAXES
from .i18n import tr


class MibConstructor(QWidget):
    def __init__(self,studio):
        super().__init__();self.studio=studio;self.config=studio.config;self.refs=[];self.loading=False;self.dirty=False
        self.editor=None;self.snapshot='';self.document=None;self.selected_name=None
        outer=QVBoxLayout(self);outer.setContentsMargins(0,0,0,0);outer.setSpacing(10)
        row=QHBoxLayout();self.files=ComboBox();self.files.currentIndexChanged.connect(self.file_changed);row.addWidget(self.files,1)
        self.reload=self.button('builder_reload',lambda:self.refresh(force=True));row.addWidget(self.reload)
        outer.addLayout(row)
        self.message=QLabel();self.message.setTextFormat(Qt.TextFormat.PlainText);self.message.setWordWrap(True);self.message.setObjectName('muted');outer.addWidget(self.message)
        split=QSplitter();split.setChildrenCollapsible(False);outer.addWidget(split,1)
        left=QWidget();left_layout=QVBoxLayout(left);left_layout.setContentsMargins(0,0,0,0)
        self.tree=QTreeWidget();self.tree.setHeaderHidden(True);self.tree.currentItemChanged.connect(self.select);left_layout.addWidget(self.tree,1)
        self.kind=ComboBox()
        for kind in KINDS:self.kind.addItem(kind,kind)
        left_layout.addWidget(self.kind)
        self.add=self.button('builder_add',self.new_object);left_layout.addWidget(self.add);split.addWidget(left)
        scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setFrameShape(QFrame.Shape.NoFrame)
        body=QWidget();self.form=QFormLayout(body);self.form.setSpacing(10);self.fields={}
        for key,label in [('name','name'),('parent','builder_parent'),('number','builder_number'),('syntax','type'),('constraints','builder_constraints'),('access','builder_access'),('status','state'),('unit','unit'),('objects','builder_objects'),('updated','builder_updated'),('organization','builder_organization'),('contact','builder_contact'),('description','builder_description')]:
            if key in ('syntax','access','status'):
                widget=ComboBox();widget.setEditable(key=='syntax')
                widget.addItems(SYNTAXES if key=='syntax' else ('not-accessible','accessible-for-notify','read-only','read-write','read-create','write-only') if key=='access' else ('current','deprecated','obsolete','mandatory','optional'))
                widget.currentTextChanged.connect(self.mark_dirty)
            elif key=='description':
                widget=QPlainTextEdit();widget.setObjectName('builderDescription');widget.setMinimumHeight(90);widget.setMaximumHeight(140);widget.textChanged.connect(self.mark_dirty)
            else:
                widget=QLineEdit();widget.textChanged.connect(self.mark_dirty)
            self.fields[key]=widget;label_widget=QLabel();self.bind(label_widget,label);label_widget.setBuddy(widget);self.form.addRow(label_widget,widget)
        self.fields['parent'].setPlaceholderText('sensors / enterprises / 1 3 6 1 4 1')
        self.fields['constraints'].setPlaceholderText('(-500..1500) / (SIZE (0..255)) / { off(0), on(1) }')
        self.fields['objects'].setPlaceholderText('temperature, operatingMode')
        scroll.setWidget(body);split.addWidget(scroll);split.setSizes([260,620])
        bar=QHBoxLayout();self.delete_button=self.button('delete',self.remove);bar.addWidget(self.delete_button);bar.addStretch()
        self.apply_button=self.button('builder_apply',self.apply);self.apply_button.setObjectName('primary');bar.addWidget(self.apply_button);outer.addLayout(bar)
        self.translate();self.timer=QTimer(self);self.timer.setSingleShot(True);self.timer.setInterval(700);self.timer.timeout.connect(self.refresh)

    def t(self,key):return tr(key,self.config.language)
    def bind(self,widget,key):self.refs.append((widget,key));widget.setText(self.t(key))
    def button(self,key,callback):
        button=QPushButton();self.bind(button,key);button.clicked.connect(callback);return button
    def translate(self):
        for widget,key in self.refs:widget.setText(self.t(key))
        for index,key in enumerate(('builder_branch','builder_parameter','builder_notification','builder_identity','builder_module_identity')):self.kind.setItemText(index,self.t(key))
        for key in ('builder_help','builder_empty','builder_unsupported','builder_stale'):
            if self.message.text() in (tr(key,'ru'),tr(key,'en')):self.message.setText(self.t(key));break
    def mark_dirty(self,*args):
        if not self.loading:self.dirty=True;self.apply_button.setEnabled(self.document is not None)
    def tell(self,text):self.message.setText(text)
    def schedule(self):
        if self.isVisible():self.timer.start()
    def can_leave(self):
        if not self.dirty:return True
        answer=QMessageBox.question(self,self.t('mib_editor'),self.t('builder_unsaved'),QMessageBox.StandardButton.Save|QMessageBox.StandardButton.Discard|QMessageBox.StandardButton.Cancel,QMessageBox.StandardButton.Cancel)
        if answer==QMessageBox.StandardButton.Save:return self.apply()
        if answer==QMessageBox.StandardButton.Discard:self.dirty=False;return True
        return False
    def file_changed(self,index):
        if self.loading:return
        if not self.can_leave():
            self.files.blockSignals(True);self.files.setCurrentIndex(self.files.findData(self.editor));self.files.blockSignals(False);return
        self.studio.tabs.setCurrentIndex(index);self.refresh(force=True)
    def refresh(self,*args,force=False):
        editor=self.studio.current()
        if self.dirty:
            if not force:
                if editor is not self.editor or editor and editor.toPlainText()!=self.snapshot:self.tell(self.t('builder_stale'))
                return
            if not self.can_leave():return
        text=editor.toPlainText() if editor else ''
        if not force and editor is self.editor and text==self.snapshot:return
        previous=self.selected_name;self.editor=editor;self.snapshot=text;self.loading=True;self.dirty=False
        self.files.blockSignals(True);self.files.clear()
        for i,source in enumerate(self.studio.editors()):self.files.addItem(self.studio.tabs.tabText(i),source)
        self.files.setCurrentIndex(self.studio.tabs.currentIndex());self.files.blockSignals(False)
        self.tree.blockSignals(True);self.tree.clear();self.document=None
        try:
            context={str(i):e.toPlainText() for i,e in enumerate(self.studio.editors()) if e is not editor}
            self.document=BuilderDocument(text,context) if editor else None
            self.tell(self.t('builder_help') if editor else self.t('builder_empty'))
            items={}
            if self.document:
                from .mib import MibIndex
                index=MibIndex().rebuild({**context,'source':text});objects={o.name:o for o in index.objects if o.module==self.document.module}
                for node in self.document.nodes:
                    obj=objects.get(node.obj.name,node.obj);item=QTreeWidgetItem([obj.name+' · '+obj.kind]);item.setData(0,Qt.ItemDataRole.UserRole,obj.name);item.setToolTip(0,obj.oid or obj.kind);items[obj.name]=item
                    parents=[p for p in objects.values() if p.oid and obj.oid.startswith(p.oid+'.') and p.name in items]
                    if parents:items[max(parents,key=lambda p:len(p.oid)).name].addChild(item)
                    else:self.tree.addTopLevelItem(item)
                self.tree.expandAll()
                if previous in items:self.tree.setCurrentItem(items[previous])
                elif items:self.tree.setCurrentItem(next(iter(items.values())))
        except Exception as exc:self.tell(self.t('builder_invalid')+'\n'+str(exc))
        self.tree.blockSignals(False);self.loading=False
        self.add.setEnabled(self.document is not None);self.kind.setEnabled(self.document is not None)
        self.select()

    def select(self,*args):
        if self.loading:return
        item=self.tree.currentItem();name=item.data(0,Qt.ItemDataRole.UserRole) if item else None
        if self.dirty and name!=self.selected_name:
            if not self.can_leave():
                self.tree.blockSignals(True)
                for found in self.tree.findItems(self.selected_name+' · ',Qt.MatchFlag.MatchStartsWith|Qt.MatchFlag.MatchRecursive):self.tree.setCurrentItem(found);break
                self.tree.blockSignals(False);return
        self.selected_name=name
        if self.document and name:
            node=self.document.node(name);self.fill(self.document.fields(node),node.obj.kind in KINDS)
        else:self.fill({},False)
        self.delete_button.setEnabled(bool(self.document and name and self.document.node(name).obj.kind in KINDS))

    def fill(self,values,enabled=True):
        self.loading=True;self.current_kind=values.get('kind',self.kind.currentData())
        for key,widget in self.fields.items():
            text=values.get(key,'')
            if isinstance(widget,ComboBox):widget.setCurrentText(text)
            elif isinstance(widget,QPlainTextEdit):widget.setPlainText(text)
            else:widget.setText(text)
            visible=key in ('name','parent','number') or key=='description' and self.current_kind!='OBJECT IDENTIFIER' or key in ('syntax','constraints','access','unit') and self.current_kind=='OBJECT-TYPE' or key=='status' and self.current_kind in ('OBJECT-TYPE','OBJECT-IDENTITY','NOTIFICATION-TYPE') or key=='objects' and self.current_kind=='NOTIFICATION-TYPE' or key in ('updated','organization','contact') and self.current_kind=='MODULE-IDENTITY'
            widget.setVisible(visible);self.form.labelForField(widget).setVisible(visible);widget.setEnabled(enabled)
        self.fields['name'].setReadOnly(False);self.loading=False;self.dirty=False;self.apply_button.setEnabled(False)
        if values and not enabled:self.tell(self.t('builder_unsupported'))

    def new_object(self):
        if self.document is None or not self.can_leave():return
        parent=self.selected_name or (self.document.objects[0].name if self.document.objects else 'enterprises')
        self.selected_name=None;self.tree.clearSelection();self.fields['name'].setReadOnly(False)
        used={self.document.fields(node)['number'] for node in self.document.nodes if node.obj.kind in KINDS and self.document.fields(node)['parent']==parent}
        number=1
        while str(number) in used:number+=1
        self.fill({'kind':self.kind.currentData(),'parent':parent,'number':str(number),'syntax':'Integer32','access':'read-only','status':'mandatory' if self.document.v1 else 'current','updated':datetime.now(timezone.utc).strftime('%Y%m%d%H%MZ')})
        self.dirty=True;self.apply_button.setEnabled(True);self.fields['name'].setFocus()

    def values(self):
        result={'kind':self.current_kind}
        for key,widget in self.fields.items():result[key]=widget.currentText() if isinstance(widget,ComboBox) else widget.toPlainText() if isinstance(widget,QPlainTextEdit) else widget.text()
        return result
    def install(self,text,name=None):
        if self.editor is not self.studio.current() or self.editor.toPlainText()!=self.snapshot:raise ValueError(self.t('builder_stale'))
        cursor=QTextCursor(self.editor.document());old=self.snapshot
        # One replacement of the changed region is one undo operation, retaining the rest of the document.
        start=0
        while start<min(len(old),len(text)) and old[start]==text[start]:start+=1
        end_old=len(old);end_new=len(text)
        while end_old>start and end_new>start and old[end_old-1]==text[end_new-1]:end_old-=1;end_new-=1
        pos=lambda i:len(old[:i].encode('utf-16-le'))//2
        cursor.beginEditBlock();cursor.setPosition(pos(start));cursor.setPosition(pos(end_old),QTextCursor.MoveMode.KeepAnchor);cursor.insertText(text[start:end_new]);cursor.endEditBlock()
        self.dirty=False;self.selected_name=name;self.refresh(force=True);self.studio.validate()
    def apply(self):
        if not self.document or not self.dirty:return True
        try:
            values=self.values();text=self.document.change(values,self.selected_name);self.install(text,values['name']);return True
        except Exception as exc:self.tell(str(exc));return False
    def remove(self):
        if not self.document or not self.selected_name:return
        if not self.can_leave():return
        if QMessageBox.question(self,self.t('delete'),self.t('remove_confirm'),QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,QMessageBox.StandardButton.No)!=QMessageBox.StandardButton.Yes:return
        try:self.install(self.document.delete(self.selected_name))
        except Exception as exc:self.tell(str(exc))


from .dialogs import Form
class NewMibDialog(Form):
    def __init__(self,config,parent=None):
        super().__init__(config.language,tr('builder_new',config.language),parent)
        self.resize(580,540);self.source_text=''
        self.module_name=self.line('builder_module','NEW-MIB');self.root_name=self.line('name','example')
        self.parent_oid=self.line('builder_parent','enterprises');self.number=self.line('builder_number','55555')
        self.organization=self.line('builder_organization');self.contact=self.line('builder_contact');self.description=self.line('builder_description')
        self.error=QLabel();self.error.setWordWrap(True);self.error.setTextFormat(Qt.TextFormat.PlainText);self.form.addRow(self.error)
    def accept(self):
        import re
        try:
            module=self.module_name.text().strip()
            if not re.fullmatch(r'[A-Z][A-Za-z0-9-]*',module) or module.endswith('-') or '--' in module:raise ValueError('Expected a module name such as MY-DEVICE-MIB.')
            doc=BuilderDocument(module+' DEFINITIONS ::= BEGIN\n\nEND\n')
            fields={'kind':'MODULE-IDENTITY','name':self.root_name.text(),'parent':self.parent_oid.text(),'number':self.number.text(),
                'updated':datetime.now(timezone.utc).strftime('%Y%m%d%H%MZ'),'organization':self.organization.text(),'contact':self.contact.text(),'description':self.description.text()}
            self.source_text=doc.change(fields);super().accept()
        except Exception as exc:self.error.setText(str(exc))
