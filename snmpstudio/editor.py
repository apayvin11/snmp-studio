# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations
from . import APP_NAME
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import re
from PySide6.QtCore import Qt, QRect, QSize, QTimer, Signal, QObject, QStringListModel
from PySide6.QtGui import QColor, QFont, QPainter, QSyntaxHighlighter, QTextCharFormat, QTextCursor, QTextDocument, QKeySequence, QShortcut
from PySide6.QtWidgets import (QWidget, QPlainTextEdit, QTextEdit, QVBoxLayout, QHBoxLayout, QPushButton,
    QLineEdit, QSplitter, QTreeWidget, QTreeWidgetItem, QTabWidget, QTextBrowser, QLabel, QFileDialog,
    QMessageBox, QAbstractItemView, QCompleter, QMenu, QTreeView, QScrollArea, QFrame, QStackedWidget)
from .mib import MibIndex, MibObject, MAX_MIB
from .discovery import DeviceObservations
from .storage import atomic_write
from .mib_format import format_mib
from .i18n import tr
from .browser import BrowserQuery
from .widgets import EmptyState, ElidedLabel, ComboBox
from .constructor import MibConstructor,NewMibDialog
from .models import oid_tuple


class Highlighter(QSyntaxHighlighter):
    def __init__(self, document):
        super().__init__(document)
        self.dark = True

    def highlightBlock(self, text):
        def fmt(color, bold=False):
            f = QTextCharFormat()
            f.setForeground(QColor(color))
            if bold:
                f.setFontWeight(QFont.Weight.DemiBold)
            return f
        keyword = fmt('#56DCC2' if self.dark else '#07856E', True)
        number = fmt('#B9A4F6' if self.dark else '#7251B5')
        quoted = fmt('#E4C38A' if self.dark else '#9B691F')
        comment = fmt('#73869E' if self.dark else '#8490A2')
        for match in re.finditer(r'\b[A-Z][A-Z0-9-]+\b|::=', text):
            self.setFormat(match.start(), len(match[0]), keyword)
        for match in re.finditer(r'\b\d+\b', text):
            self.setFormat(match.start(), len(match[0]), number)
        self.setCurrentBlockState(0)
        pos, in_string = 0, self.previousBlockState() == 1
        while pos < len(text):
            if in_string:
                end = text.find('"', pos)
                if end < 0:
                    self.setFormat(pos, len(text)-pos, quoted)
                    self.setCurrentBlockState(1)
                    return
                self.setFormat(pos, end-pos+1, quoted)
                pos, in_string = end+1, False
            elif text.startswith('--', pos):
                self.setFormat(pos, len(text)-pos, comment)
                return
            elif text[pos] == '"':
                end = text.find('"', pos+1)
                if end < 0:
                    self.setFormat(pos, len(text)-pos, quoted)
                    self.setCurrentBlockState(1)
                    return
                self.setFormat(pos, end-pos+1, quoted)
                pos = end+1
            else:
                pos += 1


class Gutter(QWidget):
    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor
    def sizeHint(self):
        return QSize(self.editor.gutter_width(), 0)
    def paintEvent(self, event):
        self.editor.paint_gutter(event)


class SourceEditor(QPlainTextEdit):
    def __init__(self, text=''):
        super().__init__()
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setFont(QFont('Menlo', 12))
        self.setTabStopDistance(self.fontMetrics().horizontalAdvance(' ') * 4)
        self.gutter = Gutter(self)
        self.completer=QCompleter(self);self.completer.setWidget(self);self.completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive);self.completer.activated.connect(self.insert_completion)
        self.completion_model=QStringListModel(self);self.completer.setModel(self.completion_model)
        self.highlighter = Highlighter(self.document())
        self.blockCountChanged.connect(self.update_margin)
        self.updateRequest.connect(self.update_gutter)
        self.cursorPositionChanged.connect(self.highlight_line)
        self.setPlainText(text)
        self.document().setModified(False)
        self.path = None
        self.original = None
        self.update_margin()
        self.highlight_line()

    def insert_completion(self,text):
        if self.isReadOnly():return
        cursor=self.textCursor();prefix=self.completer.completionPrefix()
        cursor.movePosition(QTextCursor.MoveOperation.Left,QTextCursor.MoveMode.KeepAnchor,len(prefix))
        cursor.insertText(text);self.setTextCursor(cursor)

    def keyPressEvent(self,event):
        if self.completer.popup().isVisible() and event.key() in (Qt.Key.Key_Enter,Qt.Key.Key_Return,Qt.Key.Key_Escape,Qt.Key.Key_Tab,Qt.Key.Key_Backtab):
            event.ignore();return
        complete=event.key()==Qt.Key.Key_Space and bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
        if not complete:super().keyPressEvent(event)
        if self.isReadOnly():return
        cursor=self.textCursor();before=cursor.block().text()[:cursor.positionInBlock()]
        match=re.search(r'[A-Za-z][A-Za-z0-9-]*$',before);prefix=match[0] if match else ''
        if not complete and (len(prefix)<3 or not event.text() or not event.text()[-1].isalnum()):self.completer.popup().hide();return
        self.completer.setCompletionPrefix(prefix)
        if self.completer.completionCount():
            rectangle=self.cursorRect();rectangle.setWidth(max(240,self.completer.popup().sizeHintForColumn(0)+30));self.completer.complete(rectangle)

    def gutter_width(self):
        return 18+self.fontMetrics().horizontalAdvance('9')*max(2,len(str(self.blockCount())))

    def update_margin(self, *args):
        self.setViewportMargins(self.gutter_width(),0,0,0)

    def update_gutter(self, rect, dy):
        if dy:
            self.gutter.scroll(0,dy)
        else:
            self.gutter.update(0,rect.y(),self.gutter.width(),rect.height())
        if rect.contains(self.viewport().rect()):
            self.update_margin()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        cr = self.contentsRect()
        self.gutter.setGeometry(QRect(cr.left(),cr.top(),self.gutter_width(),cr.height()))

    def paint_gutter(self, event):
        painter = QPainter(self.gutter)
        painter.setPen(QColor('#73869E'))
        block = self.firstVisibleBlock()
        top = round(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        while block.isValid() and top <= event.rect().bottom():
            height = round(self.blockBoundingRect(block).height())
            if block.isVisible() and top+height >= event.rect().top():
                painter.drawText(0,top,self.gutter.width()-9,self.fontMetrics().height(),Qt.AlignmentFlag.AlignRight,str(block.blockNumber()+1))
            top += height
            block = block.next()

    def highlight_line(self):
        selection = QTextEdit.ExtraSelection()
        selection.format.setBackground(QColor('#24364F' if self.highlighter.dark else '#EEF3F7'))
        selection.format.setProperty(QTextCharFormat.Property.FullWidthSelection,True)
        selection.cursor = self.textCursor()
        selection.cursor.clearSelection()
        self.setExtraSelections([selection])

    def goto(self, line):
        cursor = QTextCursor(self.document().findBlockByNumber(max(0,line-1)))
        self.setTextCursor(cursor)
        self.centerCursor()
        self.setFocus()


class ValidationBridge(QObject):
    completed = Signal(int,object)


class MibStudio(QWidget):
    changed = Signal()
    validated = Signal()
    add_object = Signal(str,str)
    request_object = Signal(str,str,str,int)
    cancel_request = Signal()
    write_object = Signal(object)
    write_instance = Signal(str,str)
    editor_requested = Signal()

    def __init__(self, config):
        super().__init__()
        self.config, self.index = config, MibIndex()
        self.observations={};self.oid_nodes={}
        self.refs = []
        self.edit_buttons = []
        self.edit_shortcuts = []
        self.generation = 0
        self._dismissed_diagnostic_sources = set()
        self._diagnostic_sources = set()
        self._dismissed_diagnostic_generation = -1
        self._manual_diagnostics = False
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='MIB-parser')
        self.bridge = ValidationBridge()
        self.bridge.completed.connect(self.apply_validation)
        self.parse_timer = QTimer(self)
        self.parse_timer.setSingleShot(True)
        self.parse_timer.setInterval(650)
        self.parse_timer.timeout.connect(self.validate)
        host=QVBoxLayout(self);host.setContentsMargins(0,0,0,0)
        self.browser_scroll=QScrollArea();self.browser_scroll.setWidgetResizable(True);self.browser_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.browser_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body=QWidget();body.setObjectName('workspaceBody');outer=QVBoxLayout(body);outer.setContentsMargins(0,0,0,0)
        outer.setSizeConstraint(QVBoxLayout.SizeConstraint.SetMinimumSize)
        self.browser_scroll.setWidget(body);host.addWidget(self.browser_scroll)
        splitter = QSplitter();splitter.setChildrenCollapsible(False);splitter.setHandleWidth(12)
        outer.addWidget(splitter,1)
        tree_panel=QWidget();tree_panel.setMinimumWidth(260);tree_layout=QVBoxLayout(tree_panel)
        tree_layout.setContentsMargins(0,0,0,0);tree_layout.setSpacing(10)
        bar=QHBoxLayout()
        for key,callback in [('open',self.open_dialog),('validate',self.check_mibs)]:
            button=QPushButton();self.bind(button,key);button.clicked.connect(callback);button.setFixedHeight(38);bar.addWidget(button,1)
            if key=='open':self.open_button=button
            else:self.validate_button=button
        bar.setSpacing(8);tree_layout.addLayout(bar)
        self.search = QLineEdit(); self.search.setFixedHeight(38);self.search.setClearButtonEnabled(True); self.search.textChanged.connect(self.filter_tree)
        tree_layout.addWidget(self.search)
        self.tree = QTreeWidget(); self.tree.setHeaderHidden(True); self.tree.setAlternatingRowColors(False)
        self.tree.currentItemChanged.connect(self.select_object)
        self.tree.itemDoubleClicked.connect(lambda *_: self.select_object(navigate=True))
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu);self.tree.customContextMenuRequested.connect(self.context_menu)
        tree_layout.addWidget(self.tree,1)
        self.tree_empty=EmptyState(self.tree)
        splitter.addWidget(tree_panel)
        details=QSplitter(Qt.Orientation.Vertical);details.setChildrenCollapsible(False)
        self.query=BrowserQuery(config,self.index)
        self.query.requested.connect(self.request_object.emit)
        self.query.cancel_requested.connect(self.cancel_request.emit)
        self.query.add_requested.connect(self.add_object.emit)
        self.query.write_requested.connect(self.write_instance.emit)
        self.query.device_selected.connect(lambda _:self.rebuild_tree())
        self.object_card=QFrame();self.object_card.setObjectName('card');self.object_card.setMinimumHeight(124);self.object_card.setMaximumHeight(240)
        object_layout=QVBoxLayout(self.object_card);object_layout.setContentsMargins(24,10,14,10);object_layout.setSpacing(4)
        self.object_header=QWidget();self.object_header.setFixedHeight(38)
        header=QHBoxLayout(self.object_header);header.setContentsMargins(0,0,0,0);header.setSpacing(8)
        self.object_title=ElidedLabel();self.object_title.setObjectName('sectionTitle');header.addWidget(self.object_title,1)
        header.addWidget(self.query.add_button);header.addWidget(self.query.write_button)
        object_layout.addWidget(self.object_header)
        self.inspector=QTextBrowser();self.inspector.setMinimumWidth(260);self.inspector.setMinimumHeight(60)
        self.inspector.setFrameShape(QFrame.Shape.NoFrame);self.inspector.setOpenExternalLinks(False)
        self.inspector.setObjectName('objectBody');self.inspector.document().setDocumentMargin(0)
        object_layout.addWidget(self.inspector,1)
        details.addWidget(self.object_card);details.addWidget(self.query)
        details.setSizes([170,500]);details.setStretchFactor(0,0);details.setStretchFactor(1,1)
        splitter.addWidget(details);splitter.setSizes([300,800]);splitter.setStretchFactor(0,0);splitter.setStretchFactor(1,1)

        # Source editing has its own page, with the same documents and MIB index.
        self.editor_page = QWidget()
        editor_layout = QVBoxLayout(self.editor_page);editor_layout.setContentsMargins(0,0,0,0)
        editor_bar = QHBoxLayout()
        for key, callback in [('new',self.new_file),('open',self.open_dialog),('save',self.save),('save_as',self.save_as),('validate',self.check_mibs)]:
            b=QPushButton();self.bind(b,key);b.clicked.connect(callback);editor_bar.addWidget(b)
            if key in ('new','save','save_as'):self.edit_buttons.append(b)
        editor_bar.addStretch();self.editor_mode=ComboBox();self.editor_mode.addItems([self.t('builder'),self.t('source')]);self.editor_mode.setCurrentIndex(1);editor_bar.addWidget(self.editor_mode);editor_layout.addLayout(editor_bar)
        centre = QWidget(); self.centre=centre; centre_layout=QVBoxLayout(centre);centre_layout.setContentsMargins(0,0,0,0)
        self.editor_stack=QStackedWidget();centre_layout.addWidget(self.editor_stack)
        source_panel=QWidget();layout=QVBoxLayout(source_panel);layout.setContentsMargins(0,0,0,0)
        self.tabs = QTabWidget(); self.tabs.setTabsClosable(True);self.tabs.tabCloseRequested.connect(self.close_tab)
        self.tabs.currentChanged.connect(self.tab_changed)
        layout.addWidget(self.tabs,1)
        find_bar = QHBoxLayout()
        self.find_text = QLineEdit();self.replace_text = QLineEdit()
        self.find_text.returnPressed.connect(self.find_next)
        find_bar.addWidget(self.find_text);find_bar.addWidget(self.replace_text)
        b=QPushButton();self.bind(b,'find');b.clicked.connect(self.find_next);find_bar.addWidget(b)
        b=QPushButton();self.bind(b,'replace');b.clicked.connect(self.replace_one);find_bar.addWidget(b)
        layout.addLayout(find_bar)
        self.position = QLabel();self.position.setObjectName('muted');layout.addWidget(self.position)
        self.constructor=MibConstructor(self);self.editor_stack.addWidget(self.constructor);self.editor_stack.addWidget(source_panel);self.editor_stack.setCurrentIndex(1)
        self.editor_mode.currentIndexChanged.connect(self.change_editor_mode)
        editor_layout.addWidget(centre,1)
        self.diagnostics = QTreeWidget()
        self.diagnostics.itemActivated.connect(self.goto_diagnostic)
        self.diagnostics_panel,self.close_diagnostics = self.make_diagnostics_panel(self.diagnostics)
        outer.addWidget(self.diagnostics_panel)
        self.editor_diagnostics = QTreeView()
        self.editor_diagnostics.setModel(self.diagnostics.model())
        self.editor_diagnostics.activated.connect(self.goto_editor_diagnostic)
        self.editor_diagnostics_panel,self.close_editor_diagnostics = self.make_diagnostics_panel(self.editor_diagnostics)
        editor_layout.addWidget(self.editor_diagnostics_panel)
        for sequence, callback in [('Ctrl+S',self.save),('Ctrl+Shift+S',self.save_as),('Ctrl+O',self.open_dialog),('Ctrl+N',self.new_file),('Ctrl+F',self.focus_source_search)]:
            shortcut=QShortcut(QKeySequence(sequence),self.editor_page)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(callback);self.edit_shortcuts.append(shortcut)
        shortcut=QShortcut(QKeySequence('Ctrl+O'),self)
        shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        shortcut.activated.connect(self.open_dialog)
        self.translate()
        demo_path=(Path(__file__).parent/'data'/'SNMPSTUDIO-DEMO-MIB.mib').resolve()
        for path in config.mibs[:]:
            if Path(path).resolve()!=demo_path and Path(path).is_file():
                self.open_path(path)
        self.validate()
        self.set_editing_enabled(config.editor_enabled)

    def change_editor_mode(self,index):
        if index==1 and not self.constructor.can_leave():
            self.editor_mode.blockSignals(True);self.editor_mode.setCurrentIndex(0);self.editor_mode.blockSignals(False);return
        self.editor_stack.setCurrentIndex(index)
        if index==0:self.constructor.refresh(force=True)
    def focus_source_search(self):
        self.editor_mode.setCurrentIndex(1);self.find_text.setFocus()

    def make_diagnostics_panel(self, view):
        panel=QWidget();layout=QVBoxLayout(panel)
        layout.setContentsMargins(0,0,0,0);layout.setSpacing(4)
        bar=QHBoxLayout();title=QLabel();self.bind(title,'diagnostics')
        title.setObjectName('muted');bar.addWidget(title);bar.addStretch()
        close=QPushButton('×');close.setObjectName('ghost');close.setFixedSize(28,28)
        close.clicked.connect(self.dismiss_diagnostics);bar.addWidget(close)
        layout.addLayout(bar);view.setMinimumHeight(76);view.setMaximumHeight(125);layout.addWidget(view)
        panel.setMaximumHeight(165);panel.hide()
        return panel,close

    def set_editing_enabled(self,enabled):
        self.editor_page.setEnabled(enabled)
        self.centre.setVisible(enabled)
        for button in self.edit_buttons:button.setVisible(enabled);button.setEnabled(enabled)
        for shortcut in self.edit_shortcuts:shortcut.setEnabled(enabled)
        for editor in self.editors():editor.setReadOnly(not enabled)

    def discard_unsaved(self):
        for i in reversed(range(self.tabs.count())):
            editor=self.tabs.widget(i)
            if editor.document().isModified():
                if editor.original is not None:
                    editor.setPlainText(editor.original.decode('utf-8-sig'));editor.document().setModified(False)
                else:self.tabs.removeTab(i);editor.deleteLater()
        self.validate()

    def t(self,key):return tr(key,self.config.language)
    def bind(self,widget,key):self.refs.append((widget,key));widget.setText(self.t(key))
    def translate(self):
        for widget,key in self.refs:widget.setText(self.t(key))
        self.search.setPlaceholderText(self.t('search'))
        self.tree_empty.set_text(self.t('mib_empty_title'),self.t('mib_empty_detail'))
        self.find_text.setPlaceholderText(self.t('find'));self.replace_text.setPlaceholderText(self.t('replace'))
        self.diagnostics.setHeaderLabels([self.t('state'),self.t('source'),self.t('row'),self.t('diagnostics')])
        for button in (self.close_diagnostics,self.close_editor_diagnostics):
            button.setToolTip(self.t('close_diagnostics'))
            button.setAccessibleName(self.t('close_diagnostics'))
        self.constructor.translate();self.editor_mode.setItemText(0,self.t('builder'));self.editor_mode.setItemText(1,self.t('source'))
        self.rebuild_tree()
        self.query.translate()
        self.tab_changed()
    def editors(self):return [self.tabs.widget(i) for i in range(self.tabs.count())]
    def current(self):return self.tabs.currentWidget()

    def open_dialog(self):
        paths,_=QFileDialog.getOpenFileNames(self,self.t('open'),'','MIB (*.mib *.my *.txt);;All files (*)')
        for path in paths:
            try:self.open_path(path)
            except Exception as exc:QMessageBox.warning(self,self.t('error'),str(exc))

    def open_path(self,path):
        path=Path(path).resolve()
        for i,editor in enumerate(self.editors()):
            if editor.path==path:
                self.tabs.setCurrentIndex(i);return
        if path.stat().st_size>MAX_MIB:raise ValueError('MIB exceeds 4 MiB')
        raw=path.read_bytes();text=raw.decode('utf-8-sig')
        editor=self.add_editor(text,path)
        if editor is None:return
        editor.original=raw
        self._dismissed_diagnostic_sources.discard(str(path))
        if str(path) not in self.config.mibs and 'snmpstudio/data' not in str(path):
            self.config.mibs.append(str(path));self.changed.emit()
        self.parse_timer.start()

    def add_editor(self,text,path=None):
        if not self.constructor.can_leave():return None
        editor=SourceEditor(text);editor.path=path
        editor.setReadOnly(not self.config.editor_enabled)
        editor.highlighter.dark=self.config.theme=='dark';editor.highlighter.rehighlight()
        editor.textChanged.connect(self.edited)
        editor.document().modificationChanged.connect(self.update_titles)
        editor.cursorPositionChanged.connect(self.tab_changed)
        self.tabs.addTab(editor,path.name if path else 'Untitled.mib');self.tabs.setCurrentWidget(editor)
        return editor

    def new_file(self):
        if not self.config.editor_enabled or not self.constructor.can_leave():return
        dialog=NewMibDialog(self.config,self.editor_page)
        if not dialog.exec():return
        self.add_editor(dialog.source_text)
        self.current().document().setModified(True)
        self.validate();self.editor_mode.setCurrentIndex(0);self.constructor.refresh(force=True)

    def edited(self):self.parse_timer.start();self.update_titles();self.constructor.schedule()
    def update_titles(self,*args):
        for i,e in enumerate(self.editors()):
            title=(e.path.name if e.path else 'Untitled.mib')+(' •' if e.document().isModified() else '')
            self.tabs.setTabText(i,title)
            if hasattr(self,'constructor'):
                index=self.constructor.files.findData(e)
                if index>=0:self.constructor.files.setItemText(index,title)
    def tab_changed(self,*args):
        e=self.current()
        if e:self.position.setText(f'{self.t("row")} {e.textCursor().blockNumber()+1}  ·  {e.path or "Untitled.mib"}  ·  UTF-8')
        else:self.position.clear()
        if hasattr(self,'constructor'):self.constructor.schedule()

    def save(self):
        if not self.constructor.can_leave():return False
        return self.save_editor(self.current())
    def save_as(self):
        if not self.constructor.can_leave():return False
        return self.save_editor(self.current(),True)
    def save_editor(self,editor,save_as=False):
        if editor is None:return True
        if not self.config.editor_enabled:return False
        path=editor.path
        # Packaged examples are templates; never overwrite installed resources.
        if save_as or path is None or path.parent==Path(__file__).parent/'data':
            name,_=QFileDialog.getSaveFileName(self,self.t('save_as'),str(Path.home()/(path.name if path else 'NEW-MIB.mib')),'MIB (*.mib);;All files (*)')
            if not name:return False
            path=Path(name).resolve()
            expected=None
        else:expected=editor.original
        try:
            text=editor.toPlainText()
            from .mib import parse_source
            _,_,diagnostics=parse_source(text)
            if diagnostics:
                prompt='\n'.join(item.message for item in diagnostics)+'\n\n'+('Сохранить с ошибками, без форматирования?' if self.config.language=='ru' else 'Save with errors, without formatting?')
                if QMessageBox.question(self,self.t('save'),prompt)!=QMessageBox.StandardButton.Yes:return False
                formatted=text
            else:formatted=format_mib(text)
            atomic_write(path,formatted,expected)
            if formatted!=text:
                previous=editor.textCursor();position=previous.position();anchor=previous.anchor();scroll=editor.verticalScrollBar().value()
                cursor=QTextCursor(editor.document());cursor.beginEditBlock();cursor.select(QTextCursor.SelectionType.Document);cursor.insertText(formatted);cursor.endEditBlock()
                end=editor.document().characterCount()-1;cursor.setPosition(min(anchor,end));cursor.setPosition(min(position,end),QTextCursor.MoveMode.KeepAnchor);editor.setTextCursor(cursor)
                editor.verticalScrollBar().setValue(scroll)
            old_path=editor.path
            editor.path,editor.original=path,path.read_bytes()
            if old_path and old_path!=path and str(old_path) in self.config.mibs:self.config.mibs.remove(str(old_path))
            editor.document().setModified(False)
            if str(path) not in self.config.mibs:self.config.mibs.append(str(path))
            self.changed.emit();self.update_titles();self.validate();return True
        except Exception as exc:
            QMessageBox.warning(self,self.t('error'),str(exc));return False

    def close_tab(self,index):
        if not self.constructor.can_leave():return
        editor=self.tabs.widget(index)
        if not self.confirm_editor(editor):return
        if editor.path and str(editor.path) in self.config.mibs:
            self.config.mibs.remove(str(editor.path));self.changed.emit()
        self.tabs.removeTab(index);editor.deleteLater();self.validate()

    def confirm_editor(self,editor):
        if editor.document().isModified():
            answer=QMessageBox.question(self,APP_NAME,self.t('unsaved'),QMessageBox.StandardButton.Save|QMessageBox.StandardButton.Discard|QMessageBox.StandardButton.Cancel)
            if answer==QMessageBox.StandardButton.Cancel:return False
            if answer==QMessageBox.StandardButton.Save:return self.save_editor(editor)
        return True

    def confirm_close(self):return self.constructor.can_leave() and all(self.confirm_editor(e) for e in self.editors())

    def dismiss_diagnostics(self):
        self._dismissed_diagnostic_sources.update(self._diagnostic_sources)
        self._dismissed_diagnostic_generation = self.generation
        self.diagnostics_panel.hide()
        self.editor_diagnostics_panel.hide()

    def check_mibs(self):
        self.validate(explicit=True)

    def update_diagnostics_visibility(self, sources):
        self._diagnostic_sources = set(sources)
        dismissed_pending = self.generation == self._dismissed_diagnostic_generation
        if dismissed_pending:
            self._dismissed_diagnostic_sources.update(sources)
        unseen = self._diagnostic_sources - self._dismissed_diagnostic_sources
        visible = not dismissed_pending and (bool(unseen) or self._manual_diagnostics)
        was_visible=not self.diagnostics_panel.isHidden()
        self.diagnostics_panel.setVisible(visible)
        self.editor_diagnostics_panel.setVisible(visible)
        if visible and not was_visible:QTimer.singleShot(0,lambda:self.browser_scroll.ensureWidgetVisible(self.diagnostics_panel))

    def validate(self, explicit=False):
        if explicit:
            self._dismissed_diagnostic_sources.clear()
            self._dismissed_diagnostic_generation = -1
        self._manual_diagnostics = explicit
        self.parse_timer.stop();self.generation+=1;generation=self.generation
        sources={str(e.path) if e.path else f'untitled:{i}':e.toPlainText() for i,e in enumerate(self.editors())}
        self.source_editors={str(e.path) if e.path else f'untitled:{i}':e for i,e in enumerate(self.editors())}
        def run():
            try:result=MibIndex().rebuild(sources)
            except Exception as exc:result=exc
            self.bridge.completed.emit(generation,result)
        self.pool.submit(run)

    def apply_validation(self,generation,result):
        if generation!=self.generation:return
        if isinstance(result,Exception):
            self.diagnostics.clear()
            if self._manual_diagnostics or '<validation>' not in self._dismissed_diagnostic_sources:
                self.diagnostics.addTopLevelItem(QTreeWidgetItem(['error','','',str(result)]))
            self.update_diagnostics_visibility({'<validation>'})
            return
        self.index=result;self.query.parent_index=result;self.rebuild_tree();self.diagnostics.clear()
        diagnostic_sources = {path for path,issues in result.diagnostics.items() if issues}
        for path,diagnostics in result.diagnostics.items():
            if path in self._dismissed_diagnostic_sources and not self._manual_diagnostics:
                continue
            for diagnostic in diagnostics:
                item=QTreeWidgetItem([diagnostic.severity,Path(path).name,str(diagnostic.line),diagnostic.message])
                item.setData(0,Qt.ItemDataRole.UserRole,(path,diagnostic.line));self.diagnostics.addTopLevelItem(item)
        if not diagnostic_sources:self.diagnostics.addTopLevelItem(QTreeWidgetItem(['✓','','',self.t('clean')]))
        self.update_diagnostics_visibility(diagnostic_sources)
        for view in (self.diagnostics,self.editor_diagnostics):
            view.resizeColumnToContents(0);view.resizeColumnToContents(1)
        self.filter_tree()
        keywords=['OBJECT-TYPE','OBJECT IDENTIFIER','MODULE-IDENTITY','SYNTAX','MAX-ACCESS','STATUS','DESCRIPTION','UNITS','INDEX','IMPORTS','FROM','BEGIN','END','Integer32','Counter32','Counter64','Gauge32','TimeTicks','OCTET STRING','read-only','read-write','current']
        for editor in self.editors():editor.completion_model.setStringList(sorted(set(keywords+[x.name for x in result.objects])))
        self.validated.emit()
    def record_response(self,device_id,operation,root,values):
        context=self.query.result_context;current_oid=self.query.oid.text()
        self.observations.setdefault(device_id,DeviceObservations()).record(operation,root,values)
        self.rebuild_tree()
        if context:
            self.query.result_context=context
            if current_oid:self.query.oid.setText(current_oid)
            self.query.render_context()

    def rebuild_tree(self):
        selected=self.selected()
        def key(obj):return (obj.module,obj.name,obj.oid,obj.kind) if obj else None
        selected_key=key(selected);expanded={}
        def remember(item):
            obj=item.data(0,Qt.ItemDataRole.UserRole)
            expanded[key(obj) if obj else item.text(0)]=item.isExpanded()
            for i in range(item.childCount()):remember(item.child(i))
        for i in range(self.tree.topLevelItemCount()):remember(self.tree.topLevelItem(i))
        device_id=self.query.device.currentData() or ''
        state=self.observations.get(device_id,DeviceObservations())
        self.query.observed_values=state.values
        self.tree.blockSignals(True);self.tree.clear();selected_item=None;modules={};oid_items={}
        from .theme import themed_color
        def make_item(obj,parent,label,status='',undocumented=False):
            nonlocal selected_item
            tags=[]
            if undocumented:tags.append(self.t('not_in_mib'))
            if status=='missing':tags.append(self.t('not_found'))
            item=QTreeWidgetItem(parent,[(('['+' · '.join(tags)+']  ') if tags else '')+label])
            item.setData(0,Qt.ItemDataRole.UserRole,obj);item.setData(0,Qt.ItemDataRole.UserRole+1,status);item.setData(0,Qt.ItemDataRole.UserRole+2,undocumented)
            item.setExpanded(expanded.get(key(obj),True))
            tooltip=f'{obj.module+"::" if obj.module else ""}{obj.name}\nOID {obj.oid}\n{obj.description}'
            if status=='missing':tooltip+='\n'+self.t('not_found_detail')
            if undocumented:tooltip+='\n'+self.t('not_in_mib_detail')
            item.setToolTip(0,tooltip)
            if status=='missing' or undocumented:item.setForeground(0,QColor(themed_color('#9B7620',self.config.theme)))
            if selected_key==key(obj):selected_item=item
            return item
        for obj in sorted(self.index.objects,key=lambda x:(x.module,tuple(map(int,x.oid.split('.'))) if x.oid else (),x.name)):
            if obj.module not in modules:
                modules[obj.module]=QTreeWidgetItem(self.tree,[obj.module]);modules[obj.module].setExpanded(expanded.get(obj.module,True))
            parent=modules[obj.module]
            if obj.oid:
                prefix=obj.oid.rsplit('.',1)[0]
                while prefix:
                    if prefix in oid_items:parent=oid_items[prefix];break
                    prefix=prefix.rsplit('.',1)[0] if '.' in prefix else ''
            item=make_item(obj,parent,obj.name+(f'  ·  {obj.oid.rsplit(chr(46),1)[-1]}' if obj.oid else ''),state.object_status(obj,self.index))
            if obj.oid:oid_items[obj.oid]=item
        device_group=None
        for oid,value in sorted(state.values.items(),key=lambda pair:tuple(map(int,pair[0].split('.')))):
            documented=self.index.describe_instance(oid)
            known=bool(documented and documented.kind=='OBJECT-TYPE')
            if known and oid in (documented.oid,self.index.instance_oid(documented)):continue
            prefix=oid;parent=None
            while prefix:
                if prefix in oid_items:parent=oid_items[prefix];break
                prefix=prefix.rsplit('.',1)[0] if '.' in prefix else ''
            if parent is None:
                if device_group is None:
                    device_group=QTreeWidgetItem(self.tree,[self.t('device_oids')]);device_group.setExpanded(True)
                parent=device_group
            name=documented.name+'.'+oid[len(documented.oid)+1:] if known else oid
            obj=MibObject(documented.module if known else '',name,'DEVICE-INSTANCE' if known else 'DEVICE-OID',oid=oid,
                syntax=value.kind,access=documented.access if known else '',units=documented.units if known else '',
                description=documented.description if known else self.t('not_in_mib_detail'))
            item=make_item(obj,parent,(name+'  ·  '+oid) if known else oid,'missing' if oid in state.missing else 'present',not known)
            oid_items[oid]=item
        if selected_item:self.tree.setCurrentItem(selected_item)
        elif self.tree.topLevelItemCount():
            first=self.tree.topLevelItem(0)
            if first.childCount():self.tree.setCurrentItem(first.child(0))
        self.oid_nodes=oid_items
        self.tree.blockSignals(False);self.filter_tree();self.tree_empty.refresh();self.select_object(navigate=False)

    def advance_next(self,oid,requested_oid):
        if oid_tuple(oid)<=oid_tuple(requested_oid):
            self.query.show_error(self.t('non_increasing_oid'));return
        item=self.oid_nodes.get(oid)
        if item is None:
            obj=self.index.describe_instance(oid)
            if obj:item=self.oid_nodes.get(obj.oid)
        if item is not None:
            if item.isHidden():self.search.clear()
            blocked=self.tree.blockSignals(True)
            self.tree.setCurrentItem(item)
            parent=item.parent()
            while parent is not None:
                parent.setExpanded(True);parent=parent.parent()
            self.tree.blockSignals(blocked)
            self.select_object(navigate=False,preserve_result=True)
            self.tree.scrollToItem(item)
        self.query.oid.setText(oid);self.query.update_actions()

    def context_menu(self,position):
        item=self.tree.itemAt(position)
        if not item:return
        self.tree.setCurrentItem(item);obj=item.data(0,Qt.ItemDataRole.UserRole);menu=QMenu(self)
        if obj:
            from PySide6.QtWidgets import QApplication
            action=menu.addAction('Копировать OID' if self.config.language=='ru' else 'Copy OID');action.triggered.connect(lambda:QApplication.clipboard().setText(obj.oid))
            action=menu.addAction('Копировать символ' if self.config.language=='ru' else 'Copy symbol');action.triggered.connect(lambda:QApplication.clipboard().setText(obj.module+'::'+obj.name))
            if self.config.editor_enabled:
                action=menu.addAction(self.t('open_in_editor'));action.triggered.connect(lambda:self.select_object(navigate=True))
            menu.addSeparator();oid=self.query.oid.text().strip()
            action=menu.addAction(self.t('add_monitor'));action.setEnabled(self.query.can_read_oid(oid));action.triggered.connect(self.add_selected)
            action=menu.addAction(self.t('add_command'));action.setEnabled(self.query.can_write_oid(oid));action.triggered.connect(lambda:self.write_instance.emit(oid,self.query.device.currentData() or ''))
        else:
            action=menu.addAction('Закрыть MIB' if self.config.language=='ru' else 'Close MIB')
            def close_module():
                for i,editor in enumerate(self.editors()):
                    if re.search(r'\b'+re.escape(item.text(0))+r'\s+DEFINITIONS',editor.toPlainText()):self.close_tab(i);break
            action.triggered.connect(close_module)
        menu.exec(self.tree.viewport().mapToGlobal(position))

    def filter_tree(self,*args):
        query=self.search.text().casefold()
        def visit(item):
            obj=item.data(0,Qt.ItemDataRole.UserRole)
            show=not query or bool(obj and query in f'{item.text(0)} {obj.name} {obj.oid} {obj.description} {obj.module}'.casefold())
            for j in range(item.childCount()):show=visit(item.child(j)) or show
            item.setHidden(not show)
            return show
        for i in range(self.tree.topLevelItemCount()):visit(self.tree.topLevelItem(i))

    def selected(self):
        item=self.tree.currentItem();return item.data(0,Qt.ItemDataRole.UserRole) if item else None
    def select_object(self,*args,navigate=False,preserve_result=False):
        obj=self.selected()
        if not obj:
            self.object_title.setText(self.t('details'))
            self.inspector.setPlainText(self.t('no_object'))
            self.query.select_object(None,'');return
        self.object_title.setText(obj.name)
        self.query.select_object(obj,self.index.instance_oid(obj),preserve_result=preserve_result)
        from html import escape
        from .theme import themed_color
        muted=themed_color('#697D95',self.config.theme)
        access='Доступ' if self.config.language=='ru' else 'Access'
        observed=self.observations.get(self.query.device.currentData() or '',DeviceObservations())
        status=('missing' if obj.oid in observed.missing else 'present') if obj.kind.startswith('DEVICE-') else observed.object_status(obj,self.index)
        badge=self.t('not_found' if status=='missing' else 'found_on_device') if status else ''
        module=obj.module or self.t('device_oids')
        self.inspector.setHtml(f'<p style="margin:0 0 8px 0;color:{muted};font-size:11px">{escape(module)}{(' · '+escape(badge)) if badge else ''}</p>'
            f'<table width="100%" cellspacing="0" cellpadding="0"><tr><td width="58%"><b>OID</b> {escape(obj.oid or "—")}</td><td><b>{self.t("type")}</b> {escape(obj.syntax or obj.kind)}</td></tr>'
            f'<tr><td><b>{access}</b> {escape(obj.access or "—")}</td><td><b>{self.t("unit")}</b> {escape(obj.units or "—")}</td></tr></table>'
            f'<p style="margin:7px 0 0 0">{escape(obj.description).replace(chr(10),"<br>")}</p>'
            f'<p style="margin:4px 0;color:{muted}">{escape(str(obj.enums)) if obj.enums else ""}</p>')
        if not navigate or not self.config.editor_enabled:return
        self.editor_mode.setCurrentIndex(1)
        for path,editor in self.source_editors.items():
            if any(x.module==obj.module for x in self.index.objects if x.name==obj.name):
                # Module headers identify the owning source without executing it.
                if re.search(r'\b'+re.escape(obj.module)+r'\s+DEFINITIONS',editor.toPlainText()):
                    self.tabs.setCurrentWidget(editor);self.editor_requested.emit();editor.goto(obj.line);break
    def goto_diagnostic(self,item,column=0):
        self.open_diagnostic_source(item.data(0,Qt.ItemDataRole.UserRole))
    def goto_editor_diagnostic(self,index):
        self.open_diagnostic_source(index.siblingAtColumn(0).data(Qt.ItemDataRole.UserRole))
    def open_diagnostic_source(self,value):
        self.editor_mode.setCurrentIndex(1)
        if self.config.editor_enabled and value and value[0] in self.source_editors:
            editor=self.source_editors[value[0]];self.tabs.setCurrentWidget(editor)
            self.editor_requested.emit();editor.goto(value[1])
    def add_selected(self):
        if self.selected() and self.selected().oid:
            self.add_object.emit(self.query.oid.text().strip(),self.query.device.currentData() or '')
    def find_next(self):
        editor=self.current();text=self.find_text.text()
        if editor and text and not editor.find(text):
            editor.moveCursor(QTextCursor.MoveOperation.Start);editor.find(text)
    def replace_one(self):
        editor=self.current()
        if not editor or not self.find_text.text():return
        if editor.textCursor().selectedText()==self.find_text.text():editor.textCursor().insertText(self.replace_text.text())
        self.find_next()
    def set_theme(self):
        self.rebuild_tree()
        self.query.render_status()
        for editor in self.editors():
            editor.highlighter.dark=self.config.theme=='dark';editor.highlighter.rehighlight();editor.highlight_line()
    def shutdown(self):
        self.parse_timer.stop();self.generation+=1;self.pool.shutdown(wait=True,cancel_futures=True)
