# SPDX-License-Identifier: Apache-2.0
from html import escape
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget,QVBoxLayout,QTabWidget,QTreeWidget,QTreeWidgetItem,QTextBrowser,QPlainTextEdit,QSplitter
from .ber import inspect_packet,safe_hex,TAGS
from .i18n import tr

LABELS={'Version':'Версия','Header':'Заголовок','USM security':'Защита USM','Scoped PDU':'Область PDU','Message ID':'ID сообщения','Max size':'Макс. размер','Flags':'Флаги','Security model':'Модель защиты','Engine ID':'ID движка','Engine boots':'Запуски движка','Engine time':'Время движка','Username':'Пользователь','Authentication':'Аутентификация','Privacy parameters':'Параметры шифрования','Encrypted scoped PDU':'Зашифрованные данные PDU','Context engine ID':'ID контекста','Context name':'Имя контекста','Request ID':'ID запроса','Error status':'Статус ошибки','Error index':'Индекс ошибки','VarBind list':'Список OID и значений','Non-repeaters':'Неповторяемые','Max repetitions':'Макс. повторений'}


class Inspector(QWidget):
    def __init__(self,config):
        super().__init__();self.config=config;self.packet=None;self.reveal=False
        layout=QVBoxLayout(self);layout.setContentsMargins(0,0,0,0)
        self.tabs=QTabWidget();layout.addWidget(self.tabs)
        self.tree=QTreeWidget();self.tree.setHeaderLabels(['Field','Value','Offset'])
        self.tree.currentItemChanged.connect(self.highlight)
        self.wire=QTextBrowser();self.wire.setOpenLinks(False)
        self.decoded=QPlainTextEdit();self.decoded.setReadOnly(True)
        self.tabs.addTab(self.tree,'');self.tabs.addTab(self.wire,'');self.tabs.addTab(self.decoded,'');self.translate()
    def translate(self):
        self.tabs.setTabText(0,tr('decoded',self.config.language));self.tabs.setTabText(1,tr('wire',self.config.language));self.tabs.setTabText(2,tr('plaintext',self.config.language))
        self.tree.setHeaderLabels([tr('name',self.config.language),tr('value',self.config.language),'Offset'])
        if self.packet:self.show_packet(self.packet,self.reveal)
    def label(self,text):return LABELS.get(text,text) if self.config.language=='ru' else text
    def show_packet(self,packet,reveal=False):
        self.packet,self.reveal=packet,reveal;self.tree.clear()
        try:
            self.root=inspect_packet(packet.raw)
            def add(node,parent):
                value='••••' if node.sensitive and not reveal else node.value
                item=QTreeWidgetItem(parent,[self.label(node.label),value,f'{node.start}…{node.end-1}'])
                item.setData(0,Qt.ItemDataRole.UserRole,node)
                for child in node.children:add(child,item)
                item.setExpanded(True)
            add(self.root,self.tree)
            self.tree.resizeColumnToContents(0);self.render()
        except ValueError as exc:
            self.root=None;self.tree.addTopLevelItem(QTreeWidgetItem(['BER error',str(exc)]));self.wire.setPlainText(safe_hex(packet.raw,reveal))
        title='Результат расшифровки / декодирования PDU (не исходный пакет)' if self.config.language=='ru' else 'Decrypted / decoded PDU (not the original wire message)'
        self.decoded.setPlainText(title+'\n\n'+(packet.decoded or '—')+'\n\n'+packet.plaintext.hex(' ').upper())
    def highlight(self,*args):
        if self.packet and self.root:self.render(self.tree.currentItem().data(0,Qt.ItemDataRole.UserRole) if self.tree.currentItem() else None)
    def render(self,selected=None):
        if not self.packet or not self.root:return
        raw=self.packet.raw;parts=[]
        dark=self.config.theme=='dark';fg='#DDE7F4' if dark else '#1B293C';muted='#91A1BA' if dark else '#697D95';border='#34475F' if dark else '#DFE5ED';accent='#173B3B' if dark else '#E9F7F1'
        def visit(node,level=0):
            chosen=selected and node.start==selected.start and node.end==selected.end
            style=f'background:{accent};' if chosen else ''
            title=escape(self.label(node.label))
            parts.append(f'<table width="100%" cellspacing="0" cellpadding="6" style="{style}border-bottom:1px solid {border};margin-left:{level*8}px"><tr><td colspan="3"><b>{title}</b> <span style="color:{muted}"> · @{node.start}</span></td></tr><tr style="color:{muted}"><td width="12%">{tr("type",self.config.language)}</td><td width="12%">{tr("length",self.config.language)}</td><td>{tr("value",self.config.language)}</td></tr>')
            value='•• '*(node.end-node.value_start) if node.sensitive and not self.reveal else raw[node.value_start:node.end].hex(' ').upper()
            if node.children:value=f'[{len(node.children)} fields]'
            parts.append(f'<tr style="font-family:monospace"><td>{raw[node.start]:02X}</td><td>{raw[node.start+1:node.value_start].hex(" ").upper()}</td><td>{escape(value)}</td></tr></table>')
            for child in node.children:visit(child,level+1)
        visit(self.root)
        dump=[]
        octets=safe_hex(raw,self.reveal).split()
        for start in range(0,len(raw),16):
            chunk=raw[start:start+16];ascii_text=''.join('•' if start+i<len(octets) and octets[start+i]=='••' else chr(byte) if 32<=byte<127 else '.' for i,byte in enumerate(chunk));dump.append(f'{start:04X}  '+ ' '.join(octets[start:start+16]).ljust(47)+'  '+ascii_text)
        parts.insert(0,'<pre>'+escape('\n'.join(dump))+'</pre>')
        self.wire.setHtml(f'<html><body style="color:{fg};font-size:12px"><p style="font-family:monospace">{escape(safe_hex(raw,self.reveal))}</p>'+''.join(parts)+'</body></html>')
