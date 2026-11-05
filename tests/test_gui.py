# SPDX-License-Identifier: Apache-2.0
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication
from snmpstudio.models import Configuration
from snmpstudio.storage import Store,Secrets
from snmpstudio.window import MainWindow


class GuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])
        cls.app.setOrganizationName('SNMPStudioTests');cls.app.setApplicationName('SNMPStudioTests')
    def setUp(self):
        self.store=Store(':memory:');self.config=Configuration();self.window=MainWindow(self.store,self.config,Secrets());self.window.show();self.app.processEvents()
    def tearDown(self):
        self.window.mib.constructor.dirty=False
        for editor in self.window.mib.editors():editor.document().setModified(False)
        self.window.close();self.app.processEvents();self.store.close()
    def open_test_mib(self):
        from snmpstudio.simulator import DEMO_MIB
        self.window.mib.open_path(DEMO_MIB)

    def test_normal_startup_has_no_demo_mib_until_agent_starts(self):
        self.validate_mib_view()
        self.assertIsNone(self.window.demo_agent)
        self.assertEqual(self.window.mib.tabs.count(),0)
        self.assertEqual(self.window.mib.tree.topLevelItemCount(),0)
        self.assertEqual(self.window.mib.query.oid.text(),'')
        self.assertFalse(self.window.mib.query.send_button.isEnabled())
        self.window.demo_port.setValue(0);self.window.demo_toggle.click()
        self.validate_mib_view()
        self.assertEqual(self.window.mib.tabs.count(),1)
        self.assertTrue(any(obj.module=='SNMPSTUDIO-DEMO-MIB' for obj in self.window.mib.index.objects))

    def test_startup_restores_user_mib_and_defers_configured_builtin_demo(self):
        from snmpstudio.simulator import DEMO_MIB
        with TemporaryDirectory() as tmp:
            path=Path(tmp)/'custom.mib'
            path.write_text('CUSTOM-MIB DEFINITIONS ::= BEGIN\nIMPORTS enterprises FROM SNMPv2-SMI;\ncustom OBJECT IDENTIFIER ::= { enterprises 57777 }\nEND\n')
            store=Store(':memory:');window=MainWindow(store,Configuration(mibs=[str(path),str(DEMO_MIB)]),Secrets())
            try:
                self.assertIsNone(window.demo_agent)
                self.assertEqual([e.path for e in window.mib.editors()],[path.resolve()])
                window.demo_port.setValue(0);window.demo_toggle.click()
                self.assertEqual([e.path for e in window.mib.editors()],[path.resolve(),DEMO_MIB.resolve()])
                self.assertEqual(len(window.config.devices),1)
                self.assertEqual(window.mib.query.device.currentData(),window.config.devices[0].id)
            finally:
                window.close();store.close()

    def test_editor_hidden_by_default(self):
        self.open_test_mib()
        self.window.navigate(2);self.app.processEvents();self.assertTrue(self.window.mib.centre.isHidden());self.assertTrue(self.window.mib.current().isReadOnly())
        self.assertTrue(all(not button.isVisible() for button in self.window.mib.edit_buttons))
    def test_setting_enables_editor_and_persists(self):
        self.open_test_mib()
        self.window.editor_enabled.setChecked(True);self.app.processEvents();self.assertFalse(self.window.mib.centre.isHidden());self.assertFalse(self.window.mib.current().isReadOnly());self.assertTrue(self.store.load().editor_enabled)
    def test_editor_has_conditional_menu_and_separate_page(self):
        self.open_test_mib()
        mib=self.window.mib
        self.assertTrue(self.window.editor_nav.isHidden())
        self.window.navigate(self.window.editor_page_index)
        self.assertIs(self.window.stack.currentWidget(),mib)
        self.window.navigate(6)
        self.window.editor_enabled.setChecked(True);self.app.processEvents()
        self.assertTrue(self.window.editor_nav.isVisible())
        self.assertEqual(self.window.stack.currentIndex(),6)
        menu=self.window.editor_nav.parentWidget().layout()
        self.assertEqual(menu.indexOf(self.window.editor_nav),menu.indexOf(self.window.nav[2])+1)
        self.window.editor_nav.click();self.app.processEvents()
        self.assertIs(self.window.stack.currentWidget(),mib.editor_page)
        self.assertTrue(mib.centre.isVisible())
        self.assertFalse(mib.isAncestorOf(mib.centre))
        self.assertEqual(self.window.title.text(),'MIB-редактор')
        editor=mib.current();editor.appendPlainText('-- keep draft');text=editor.toPlainText()
        self.window.nav[2].click();self.app.processEvents()
        self.assertIs(self.window.stack.currentWidget(),mib)
        self.assertFalse(mib.centre.isVisible())
        self.window.editor_nav.click()
        self.assertIs(mib.current(),editor)
        self.assertEqual(editor.toPlainText(),text)
        self.assertTrue(editor.document().isModified())
        self.window.language.setCurrentIndex(1)
        self.assertEqual(self.window.editor_nav.text(),'MIB editor')
        self.assertEqual(self.window.title.text(),'MIB editor')
        self.assertTrue(mib.position.text().startswith('Line '))

    def test_disabling_editor_hides_menu_and_returns_to_browser(self):
        self.open_test_mib()
        self.window.editor_enabled.setChecked(True);self.window.editor_nav.click()
        self.window.editor_enabled.setChecked(False);self.app.processEvents()
        self.assertTrue(self.window.editor_nav.isHidden())
        self.assertIs(self.window.stack.currentWidget(),self.window.mib)
        self.assertTrue(self.window.nav[2].isChecked())
        self.assertFalse(self.store.load().editor_enabled)
        self.assertTrue(self.window.mib.current().isReadOnly())

    def test_cancel_disabling_editor_keeps_page_and_unsaved_source(self):
        self.open_test_mib()
        from unittest.mock import patch
        from PySide6.QtWidgets import QMessageBox
        self.window.editor_enabled.setChecked(True);self.window.editor_nav.click()
        editor=self.window.mib.current();editor.appendPlainText('-- keep draft')
        with patch.object(QMessageBox,'question',return_value=QMessageBox.StandardButton.Cancel):
            self.window.editor_enabled.setChecked(False)
        self.assertTrue(self.window.editor_enabled.isChecked())
        self.assertTrue(self.window.editor_nav.isVisible())
        self.assertIs(self.window.stack.currentWidget(),self.window.mib.editor_page)
        self.assertTrue(editor.document().isModified())
        self.assertTrue(self.store.load().editor_enabled)

    def test_browser_opens_definition_only_on_explicit_editor_action(self):
        self.open_test_mib()
        from PySide6.QtCore import Qt
        self.validate_mib_view()
        mib=self.window.mib
        item=mib.tree.topLevelItem(0).child(0)
        obj=item.data(0,Qt.ItemDataRole.UserRole)
        self.assertIsNotNone(obj)
        mib.tree.setCurrentItem(item)
        mib.tree.itemDoubleClicked.emit(item,0)
        self.assertIs(self.window.stack.currentWidget(),mib)
        self.window.editor_enabled.setChecked(True)
        mib.select_object()
        self.assertIs(self.window.stack.currentWidget(),mib)
        mib.tree.itemDoubleClicked.emit(item,0);self.app.processEvents()
        self.assertIs(self.window.stack.currentWidget(),mib.editor_page)
        self.assertEqual(mib.current().textCursor().blockNumber()+1,obj.line)

    def test_diagnostics_are_shared_and_open_source_in_editor(self):
        with TemporaryDirectory() as tmp:
            file=Path(tmp)/'broken.mib';file.write_text('BROKEN-MIB DEFINITIONS ::= BEGIN\ninvalid !\nEND\n')
            mib=self.window.mib;mib.open_path(file);self.validate_mib_view()
            item=mib.diagnostics.topLevelItem(0)
            mib.goto_diagnostic(item)
            self.assertIs(self.window.stack.currentWidget(),mib)
            self.window.editor_enabled.setChecked(True)
            mib.goto_diagnostic(item);self.app.processEvents()
            self.assertIs(self.window.stack.currentWidget(),mib.editor_page)
            self.assertEqual(mib.current().path,file.resolve())
            self.assertIs(mib.editor_diagnostics.model(),mib.diagnostics.model())
            self.assertTrue(mib.editor_diagnostics_panel.isVisible())
            mib.close_editor_diagnostics.click()
            self.assertTrue(mib.editor_diagnostics_panel.isHidden())
            self.assertTrue(mib.diagnostics_panel.isHidden())
            self.validate_mib_view(explicit=True)
            index=mib.editor_diagnostics.model().index(0,3)
            mib.editor_diagnostics.activated.emit(index)
            self.assertEqual(mib.current().path,file.resolve())
            self.window.nav[2].click()
            self.assertTrue(mib.diagnostics_panel.isVisible())

    def test_disabled_shortcut_cannot_create_document(self):
        before=self.window.mib.tabs.count();self.window.mib.new_file();self.assertEqual(self.window.mib.tabs.count(),before)
    def test_language_and_theme_keep_unsaved_source(self):
        self.open_test_mib()
        self.window.editor_enabled.setChecked(True);editor=self.window.mib.current();editor.appendPlainText('-- unsaved');text=editor.toPlainText();self.window.language.setCurrentIndex(1);self.window.toggle_theme();self.assertEqual(editor.toPlainText(),text);self.assertTrue(editor.document().isModified())
    def test_editor_save_reparses(self):
        self.window.editor_enabled.setChecked(True)
        with TemporaryDirectory() as tmp:
            file=Path(tmp)/'test.mib';file.write_text('TEST-MIB DEFINITIONS ::= BEGIN\nIMPORTS enterprises FROM SNMPv2-SMI;\nx OBJECT IDENTIFIER ::= { enterprises 55555 }\nEND\n')
            self.window.mib.open_path(file);editor=self.window.mib.current();editor.setPlainText(editor.toPlainText().replace('55555','55556'))
            self.assertTrue(self.window.mib.save());self.assertIn('55556',file.read_text())
    def test_save_as_replaces_workspace_path_without_duplicate_module(self):
        from unittest.mock import patch
        self.window.editor_enabled.setChecked(True)
        with TemporaryDirectory() as tmp:
            original=Path(tmp)/'original.mib';copy=Path(tmp)/'copy.mib'
            original.write_text('TEST-MIB DEFINITIONS ::= BEGIN\nIMPORTS enterprises FROM SNMPv2-SMI;\nx OBJECT IDENTIFIER ::= { enterprises 55555 }\nEND\n')
            self.window.mib.open_path(original)
            with patch('snmpstudio.editor.QFileDialog.getSaveFileName',return_value=(str(copy),'MIB')):
                self.assertTrue(self.window.mib.save_as())
            self.assertEqual(self.config.mibs,[str(copy.resolve())])
    def test_validation_keeps_cursor_position_while_editing(self):
        self.open_test_mib()
        from snmpstudio.mib import MibIndex
        from PySide6.QtGui import QTextCursor
        self.window.editor_enabled.setChecked(True);mib=self.window.mib;editor=mib.current()
        editor.moveCursor(QTextCursor.MoveOperation.End);position=editor.textCursor().position()
        sources={str(e.path) if e.path else f'untitled:{i}':e.toPlainText() for i,e in enumerate(mib.editors())}
        mib.apply_validation(mib.generation,MibIndex().rebuild(sources))
        self.assertEqual(editor.textCursor().position(),position)
    def test_demo_agent_start_add_monitor_without_duplicates_and_stop(self):
        self.window.demo_port.setValue(0)
        self.window.toggle_demo_agent()
        agent=self.window.demo_agent
        self.assertTrue(agent.running)
        self.assertIn(str(agent.port),self.window.demo_status.text())
        self.window.add_demo_to_monitor()
        self.window.add_demo_to_monitor()
        self.assertEqual(len(self.config.devices),1)
        self.assertEqual(len(self.config.parameters),5)
        self.assertEqual(self.window.secrets.get(self.config.devices[0].id,'write_community'),'private')
        self.assertFalse(self.window.polling)
        deadline=time.monotonic()+3
        while len(self.window.values)<5 and time.monotonic()<deadline:
            self.app.processEvents();time.sleep(.01)
        self.assertEqual(len(self.window.values),5)
        self.assertTrue(all(not value.get('error') for value in self.window.values.values()))
        self.window.toggle_demo_agent()
        self.assertIsNone(self.window.demo_agent)
        self.assertFalse(agent.running)
        self.assertTrue(self.window.demo_port.isEnabled())
    def test_demo_start_opens_mib_alongside_existing_files_without_duplicates(self):
        from snmpstudio.simulator import DEMO_MIB
        mib=self.window.mib
        for i in reversed(range(mib.tabs.count())):mib.close_tab(i)
        with TemporaryDirectory() as tmp:
            path=Path(tmp)/'custom.mib'
            path.write_text('CUSTOM-MIB DEFINITIONS ::= BEGIN\nIMPORTS enterprises FROM SNMPv2-SMI;\ncustom OBJECT IDENTIFIER ::= { enterprises 57777 }\nEND\n')
            mib.open_path(path);self.validate_mib_view()
            self.assertFalse(any(obj.module=='SNMPSTUDIO-DEMO-MIB' for obj in mib.index.objects))
            self.window.editor_enabled.setChecked(True)
            custom=mib.current();custom.appendPlainText('-- unsaved custom source');text=custom.toPlainText()
            self.window.navigate(6);self.window.demo_port.setValue(0);self.window.demo_toggle.click()
            self.assertTrue(self.window.demo_agent.running)
            self.validate_mib_view()
            self.assertTrue(any(obj.module=='SNMPSTUDIO-DEMO-MIB' for obj in mib.index.objects))
            self.assertTrue(any(mib.tree.topLevelItem(i).text(0)=='SNMPSTUDIO-DEMO-MIB' for i in range(mib.tree.topLevelItemCount())))
            self.assertEqual(custom.toPlainText(),text);self.assertTrue(custom.document().isModified())
            self.assertEqual(self.window.stack.currentIndex(),6)
            self.window.demo_toggle.click();self.window.demo_toggle.click();self.app.processEvents()
            self.assertEqual(sum(e.path==DEMO_MIB.resolve() for e in mib.editors()),1)
            self.assertEqual(mib.tabs.count(),2)
            self.assertIn(custom,mib.editors())

    def test_demo_start_configures_selects_and_reuses_device_with_communities(self):
        from dataclasses import asdict
        from unittest.mock import patch
        from snmpstudio.simulator import DemoAgent,DEMO_DEVICE_ID
        from snmpstudio.models import Device
        from snmpstudio.dialogs import DeviceDialog
        agent=DemoAgent();self.addCleanup(agent.close)
        existing=Device(name='Existing profile',port=agent.port,period=42)
        self.config.devices.append(existing)
        self.window.secrets.session[f'{existing.id}:community']='existing-community'
        self.window.refresh_devices();before=asdict(existing)
        with patch('snmpstudio.simulator.DemoAgent',return_value=agent):
            self.window.demo_toggle.click()
        device=next(d for d in self.config.devices if d.id==DEMO_DEVICE_ID)
        self.assertEqual(len(self.config.devices),2)
        self.assertEqual(self.window.mib.query.device.currentData(),device.id)
        self.assertEqual(device.host,agent.host);self.assertEqual(device.port,agent.port)
        self.assertEqual(device.version,'2c');self.assertTrue(device.enabled)
        self.assertEqual(self.window.secrets.get(device.id,'community'),'public')
        self.assertEqual(self.window.secrets.get(device.id,'write_community'),'private')
        dialog=DeviceDialog(self.config,self.window.secrets,device,self.window)
        self.assertEqual(dialog.community.text(),'public');self.assertEqual(dialog.write_community.text(),'private')
        dialog.reject();dialog.deleteLater()
        self.assertEqual(self.config.parameters,[]);self.assertFalse(self.window.polling)
        self.validate_mib_view();self.select_mib_object('temperature')
        self.window.mib.query.send_button.click();self.wait_for_browser_request()
        self.assertEqual(self.window.mib.query.status.text(),'Успешно')
        self.assertEqual(self.window.mib.query.values[0].value,253)
        self.window.demo_toggle.click()
        device.host='192.0.2.55';device.port=9;device.version='1';device.enabled=False
        self.window.secrets.session[f'{device.id}:community']='old'
        self.window.secrets.session[f'{device.id}:write_community']='old'
        self.window.mib.query.device.setCurrentIndex(self.window.mib.query.device.findData(existing.id))
        self.window.demo_port.setValue(0);self.window.demo_toggle.click()
        self.assertEqual(len(self.config.devices),2)
        self.assertEqual(self.window.mib.query.device.currentData(),device.id)
        self.assertEqual(device.host,'127.0.0.1');self.assertEqual(device.port,self.window.demo_agent.port)
        self.assertEqual(device.version,'2c');self.assertTrue(device.enabled)
        self.assertEqual(self.window.secrets.get(device.id,'community'),'public')
        self.assertEqual(self.window.secrets.get(device.id,'write_community'),'private')
        restored=next(d for d in self.store.load().devices if d.id==device.id)
        self.assertEqual(restored,device)
        self.assertEqual(asdict(existing),before)
        self.assertEqual(self.window.secrets.get(existing.id,'community'),'existing-community')
        self.assertEqual(self.config.parameters,[]);self.assertFalse(self.window.polling)

    def test_failed_demo_start_does_not_create_device_or_change_selection(self):
        from unittest.mock import patch
        from snmpstudio.models import Device
        existing=Device();self.config.devices.append(existing);self.window.refresh_devices()
        with patch('snmpstudio.simulator.DemoAgent',side_effect=OSError('Address already in use')), patch.object(self.window,'error') as error:
            self.window.demo_toggle.click();error.assert_called_once_with('Address already in use')
        self.assertIsNone(self.window.demo_agent)
        self.assertEqual(self.config.devices,[existing])
        self.assertEqual(self.window.mib.query.device.currentData(),existing.id)

    def test_demo_does_not_repurpose_existing_device_at_same_endpoint(self):
        from dataclasses import asdict
        from snmpstudio.models import Device,Parameter
        self.window.demo_port.setValue(0);self.window.toggle_demo_agent()
        existing=Device(name='Existing profile',port=self.window.demo_agent.port,period=42)
        parameter=Parameter(device=existing.id,name='Existing parameter',oid='1.3.6.1.2.1.1.1.0')
        self.config.devices.append(existing);self.config.parameters.append(parameter)
        self.window.secrets.session[f'{existing.id}:community']='existing-community'
        before=asdict(existing)
        self.window.add_demo_to_monitor();self.window.add_demo_to_monitor()
        self.assertEqual(len(self.config.devices),2)
        self.assertEqual(len(self.config.parameters),6)
        self.assertEqual(asdict(existing),before)
        self.assertEqual(self.window.secrets.get(existing.id,'community'),'existing-community')
        self.assertFalse(self.window.polling)
    def save_test_command(self, name, value, *, device=None, oid='1.3.6.1.4.1.55555.1.2.0', verify=False):
        from snmpstudio.models import uid
        command={'id':uid(),'name':name,'device':device or self.window.mib.query.device.currentData(),
            'rows':[{'oid':oid,'type':'Integer32','value':value}],'verify':verify}
        self.window.commit_saved_command(command)
        return command

    def test_delete_saved_command_by_selection_persists_and_clears_selection(self):
        from unittest.mock import patch
        from PySide6.QtWidgets import QMessageBox
        first=self.save_test_command('Same name','220')
        second=self.save_test_command('Same name','270')
        self.assertEqual(self.window.selected_command(),second)
        with patch.object(QMessageBox,'exec',return_value=QMessageBox.StandardButton.Yes), patch.object(self.window.worker,'submit') as submit:
            self.window.delete_saved_command_button.click();submit.assert_not_called()
        self.assertEqual(self.config.commands,[first]);self.assertEqual(self.store.load().commands,[first])
        self.assertEqual(self.window.command_table.rowCount(),1)
        self.window.refresh_devices()
        self.assertIsNone(self.window.selected_command());self.assertFalse(self.window.send_button.isEnabled())
        self.assertFalse(self.window.delete_saved_command_button.isEnabled())
        self.window.command_table.selectRow(0)
        with patch.object(QMessageBox,'exec',return_value=QMessageBox.StandardButton.Yes):self.window.delete_saved_command_button.click()
        self.assertEqual(self.store.load().commands,[]);self.assertEqual(self.window.command_table.rowCount(),0)

    def test_cancel_saved_command_deletion_preserves_selection_and_database(self):
        from unittest.mock import patch
        from PySide6.QtWidgets import QMessageBox
        command=self.save_test_command('Keep <literal> name','250');before=self.store.load().commands
        with patch.object(QMessageBox,'exec',return_value=QMessageBox.StandardButton.No):self.window.delete_saved_command_button.click()
        self.assertEqual(self.config.commands,before);self.assertEqual(self.store.load().commands,before)
        self.assertEqual(self.window.selected_command(),command)

    def test_failed_saved_command_deletion_does_not_change_ui_or_config(self):
        import sqlite3
        from unittest.mock import patch
        from PySide6.QtWidgets import QMessageBox
        command=self.save_test_command('Keep after DB failure','260');before=self.store.load().commands
        with patch.object(QMessageBox,'exec',return_value=QMessageBox.StandardButton.Yes), patch.object(self.store,'save',side_effect=sqlite3.OperationalError('database is locked')), patch.object(self.window,'error') as error:
            self.window.delete_saved_command_button.click();error.assert_called_once_with('database is locked')
        self.assertEqual(self.config.commands,before);self.assertEqual(self.store.load().commands,before)
        self.assertEqual(self.window.selected_command(),command);self.assertEqual(self.window.command_table.rowCount(),1)

    def test_saved_command_deletion_is_unavailable_during_set(self):
        from unittest.mock import patch
        from PySide6.QtWidgets import QMessageBox
        self.save_test_command('Busy','240');self.window.pending.add('set');self.window.update_command_actions()
        for control in (self.window.edit_command_button,self.window.delete_saved_command_button,self.window.send_button):self.assertFalse(control.isEnabled())
        with patch.object(QMessageBox,'exec') as question:
            self.window.delete_saved_command();self.window.edit_saved_command();self.window.send_command();question.assert_not_called()
        self.assertEqual(len(self.store.load().commands),1)

    def wait_for_mib_index(self, previous):
        deadline=time.monotonic()+3
        while self.window.mib.index is previous and time.monotonic()<deadline:
            self.app.processEvents();time.sleep(.01)
        self.assertIsNot(self.window.mib.index,previous)

    def validate_mib_view(self, explicit=False):
        previous=self.window.mib.index
        self.window.mib.validate(explicit=explicit)
        self.wait_for_mib_index(previous)

    def test_mib_error_can_be_dismissed_until_explicit_check(self):
        with TemporaryDirectory() as tmp:
            file=Path(tmp)/'broken.mib';file.write_text('BROKEN-MIB DEFINITIONS ::= BEGIN\ninvalid !\nEND\n')
            mib=self.window.mib;mib.open_path(file)
            self.validate_mib_view()
            self.assertFalse(mib.diagnostics_panel.isHidden())
            self.assertTrue(mib.index.diagnostics[str(file.resolve())])
            mib.close_diagnostics.click()
            self.assertTrue(mib.diagnostics_panel.isHidden())
            self.validate_mib_view()
            self.assertTrue(mib.diagnostics_panel.isHidden())
            self.validate_mib_view(explicit=True)
            self.assertFalse(mib.diagnostics_panel.isHidden())
            self.assertGreater(mib.diagnostics.topLevelItemCount(),0)

    def test_mib_new_error_is_shown_without_repeating_acknowledged_error(self):
        with TemporaryDirectory() as tmp:
            bad=Path(tmp)/'first-bad.mib';bad.write_text('BROKEN-MIB DEFINITIONS ::= BEGIN\ninvalid !\nEND\n')
            good=Path(tmp)/'good.mib';good.write_text('GOOD-MIB DEFINITIONS ::= BEGIN\nIMPORTS enterprises FROM SNMPv2-SMI;\nx OBJECT IDENTIFIER ::= { enterprises 57777 }\nEND\n')
            other=Path(tmp)/'second-bad.mib';other.write_text(bad.read_text())
            mib=self.window.mib;mib.open_path(bad);self.validate_mib_view();mib.close_diagnostics.click()
            mib.open_path(good);self.validate_mib_view()
            self.assertTrue(mib.diagnostics_panel.isHidden())
            mib.open_path(other);self.validate_mib_view()
            self.assertFalse(mib.diagnostics_panel.isHidden())
            self.assertEqual(mib.diagnostics.topLevelItemCount(),1)
            item=mib.diagnostics.topLevelItem(0)
            from PySide6.QtCore import Qt
            self.assertEqual(item.data(0,Qt.ItemDataRole.UserRole)[0],str(other.resolve()))

    def test_mib_pending_validation_cannot_reopen_closed_error(self):
        with TemporaryDirectory() as tmp:
            file=Path(tmp)/'broken.mib';file.write_text('BROKEN-MIB DEFINITIONS ::= BEGIN\ninvalid !\nEND\n')
            mib=self.window.mib;mib.open_path(file);self.validate_mib_view()
            previous=mib.index;mib.check_mibs();mib.close_diagnostics.click()
            self.wait_for_mib_index(previous)
            self.assertTrue(mib.diagnostics_panel.isHidden())
            self.validate_mib_view()
            self.assertTrue(mib.diagnostics_panel.isHidden())

    def browser_agent(self):
        from snmpstudio.simulator import DEMO_DEVICE_ID
        self.window.demo_port.setValue(0);self.window.demo_dynamic.setChecked(False);self.window.toggle_demo_agent()
        device=next(d for d in self.config.devices if d.id==DEMO_DEVICE_ID)
        device.timeout=.2;device.retries=0
        self.window.refresh_devices();self.validate_mib_view()
        return device

    def select_mib_object(self,name):
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QTreeWidgetItemIterator
        iterator=QTreeWidgetItemIterator(self.window.mib.tree)
        while iterator.value():
            item=iterator.value();obj=item.data(0,Qt.ItemDataRole.UserRole)
            if obj and obj.name==name:
                self.window.mib.tree.setCurrentItem(item);return obj
            iterator+=1
        self.fail('Missing MIB object: '+name)

    def wait_for_browser_request(self):
        deadline=time.monotonic()+4
        while 'mib-query' in self.window.pending and time.monotonic()<deadline:
            self.app.processEvents();time.sleep(.01)
        self.assertNotIn('mib-query',self.window.pending)

    def test_browser_get_next_bulk_and_walk_stay_on_browser_page(self):
        self.browser_agent();self.select_mib_object('temperature')
        query=self.window.mib.query;root='1.3.6.1.4.1.55555'
        self.assertEqual(query.oid.text(),root+'.1.1.0')
        query.send_button.click();self.wait_for_browser_request()
        self.assertEqual(query.status.text(),'Успешно')
        self.assertEqual(query.values[0].value,253)
        self.assertEqual(query.table.horizontalHeaderItem(0).text(),'Название')
        self.assertEqual(query.table.item(0,0).text(),'temperature')
        self.assertIn('253',query.table.item(0,3).text())
        self.assertFalse(query.error_panel.isVisible())
        self.assertTrue(any(p.direction=='TX' for p in self.window.packets.values()))
        self.assertTrue(any(p.direction=='RX' for p in self.window.packets.values()))
        query.operation.setCurrentIndex(1);query.send_button.click();self.wait_for_browser_request()
        self.assertEqual(query.values[0].oid,root+'.1.2.0')
        query.operation.setCurrentIndex(2);query.oid.setText(root);query.max_repetitions.setValue(2)
        query.send_button.click();self.wait_for_browser_request()
        self.assertEqual(len(query.values),2)
        self.assertEqual(query.status.text(),'Успешно')
        self.select_mib_object('studio');query.walk_button.click();self.wait_for_browser_request()
        self.assertEqual(len(query.values),3)
        self.assertEqual(query.status.text(),'Успешно')
        self.assertIs(self.window.stack.currentWidget(),self.window.mib)
        self.assertTrue(self.window.mib.isAncestorOf(query))
        self.assertFalse(self.window.stack.widget(1).isAncestorOf(query))
        self.window.language.setCurrentIndex(1)
        self.assertEqual(query.status.text(),'Success')

    def test_browser_error_result_is_inline_and_dismissible(self):
        from unittest.mock import patch
        self.browser_agent();self.select_mib_object('temperature')
        query=self.window.mib.query;query.oid.setText('1.3.6.1.4.1.55555.999.0')
        with patch.object(self.window,'error') as modal:
            query.send_button.click();self.wait_for_browser_request();modal.assert_not_called()
        self.assertEqual(query.status.text(),'Ошибка')
        self.assertTrue(query.error_panel.isVisible())
        self.assertIn('NoSuch',query.error_text.toPlainText())
        self.assertTrue(query.table.item(0,4).text())
        query.close_error.click();self.app.processEvents()
        self.assertTrue(query.error_panel.isHidden())
        self.app.processEvents();self.assertTrue(query.error_panel.isHidden())
        query.oid.setText('1.3.6.1.4.1.55555.1.1.0')
        query.send_button.click();self.wait_for_browser_request()
        self.assertEqual(query.status.text(),'Успешно');self.assertTrue(query.error_panel.isHidden())

    def test_browser_transport_error_and_cancel_recover_controls(self):
        device=self.browser_agent();self.select_mib_object('temperature')
        self.window.toggle_demo_agent();query=self.window.mib.query
        query.send_button.click();self.wait_for_browser_request()
        self.assertEqual(query.status.text(),'Ошибка');self.assertTrue(query.error_panel.isVisible())
        query.oid.setText('bad-oid');query.send_button.click()
        self.assertEqual(query.status.text(),'Ошибка');self.assertNotIn('mib-query',self.window.pending)
        query.oid.setText('1.3.6.1.4.1.55555');device.timeout=2
        query.walk_button.click();self.assertTrue(query.busy)
        self.assertFalse(query.send_button.isEnabled());self.assertTrue(query.cancel_button.isEnabled())
        query.cancel_button.click();self.wait_for_browser_request()
        self.assertFalse(query.busy);self.assertTrue(query.send_button.isEnabled())
        self.assertFalse(query.cancel_button.isEnabled());self.assertTrue(self.window.mib.tree.isEnabled())
        self.assertEqual(query.status.text(),'Ошибка')
        self.assertEqual(query.error_text.toPlainText(),'Запрос отменён')

    def test_browser_v1_does_not_offer_getbulk(self):
        device=self.browser_agent();query=self.window.mib.query
        query.operation.setCurrentIndex(2);device.version='1';self.window.refresh_devices()
        self.assertEqual(query.operation.currentData(),'GET')
        self.assertFalse(query.operation.model().item(2).isEnabled())
        self.window.query_mib(device.id,'GETBULK','1.3.6.1.4.1.55555')
        self.assertEqual(query.status.text(),'Ошибка')
        self.assertIn('SNMPv2c',query.error_text.toPlainText())
        self.assertNotIn('mib-query',self.window.pending)
        self.assertFalse(self.window.demo_agent.requests)
        self.select_mib_object('studio');query.walk_button.click();self.wait_for_browser_request()
        self.assertEqual(query.status.text(),'Успешно');self.assertEqual(len(query.values),3)

    def test_browser_add_monitor_prefills_fields_locks_oid_and_persists_edits(self):
        from unittest.mock import patch
        from snmpstudio.dialogs import ParameterDialog
        device=self.browser_agent();obj=self.select_mib_object('temperature')
        query=self.window.mib.query
        def edit(dialog):
            self.assertTrue(dialog.oid.isReadOnly())
            self.assertEqual(dialog.oid.text(),obj.oid+'.0')
            self.assertEqual(dialog.device.currentData(),device.id)
            self.assertEqual(dialog.name.text(),obj.name);self.assertEqual(dialog.unit.text(),obj.units)
            dialog.name.setText('Supply temperature');dialog.unit.setText('°C')
            dialog.decimal_shift.setValue(1);dialog.offset.setText('1');dialog.period.setValue(3)
            dialog.accept();return dialog.result()
        with patch.object(ParameterDialog,'exec',edit):query.add_button.click()
        parameter=self.config.parameters[0]
        self.assertEqual(parameter.oid,obj.oid+'.0');self.assertEqual(parameter.name,'Supply temperature')
        self.assertEqual(parameter.factor,'0.1');self.assertEqual(parameter.unit,'°C');self.assertEqual(parameter.period,3)
        self.assertEqual(parameter.formatted(253),'26.30')
        self.assertEqual(self.store.load().parameters,[parameter])
        query.operation.setCurrentIndex(1);query.send_button.click();self.wait_for_browser_request()
        query.table.selectRow(0)
        with patch.object(ParameterDialog,'exec',lambda dialog:(dialog.accept() or dialog.result())):
            query.add_button.click()
        self.assertEqual(self.config.parameters[-1].oid,'1.3.6.1.4.1.55555.1.2.0')
        self.assertEqual(self.config.parameters[-1].name,'setPoint')

    def test_browser_does_not_add_branch_as_scalar_parameter(self):
        from unittest.mock import patch
        from snmpstudio.dialogs import ParameterDialog
        self.browser_agent();self.select_mib_object('studio')
        with patch.object(ParameterDialog,'exec') as dialog:
            self.window.mib.query.add_button.click();dialog.assert_not_called()
        self.assertEqual(self.config.parameters,[])
        self.assertFalse(self.window.mib.query.add_button.isEnabled())

    def tree_object(self,oid):
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QTreeWidgetItemIterator
        iterator=QTreeWidgetItemIterator(self.window.mib.tree)
        while iterator.value():
            item=iterator.value();obj=item.data(0,Qt.ItemDataRole.UserRole)
            if obj and obj.oid==oid:return item
            iterator+=1
        return None

    def test_browser_actions_follow_mib_read_write_permissions(self):
        from snmpstudio.mib import MibObject
        self.browser_agent();query=self.window.mib.query
        for number,(access,read,write) in enumerate([('read-only',True,False),('read-write',True,True),('read-create',True,True),('write-only',False,True),('not-accessible',False,False),('accessible-for-notify',False,False)],20):
            with self.subTest(access=access):
                obj=MibObject('ACCESS','access'+str(number),'OBJECT-TYPE',oid=f'1.3.6.1.4.1.55555.{number}',syntax='Integer32',access=access)
                self.window.mib.index.objects.append(obj);query.select_object(obj,self.window.mib.index.instance_oid(obj))
                self.assertEqual(query.add_button.isEnabled(),read)
                self.assertEqual(query.write_button.isEnabled(),write)

    def test_add_to_commands_opens_dialog_and_saves_edited_name_type_and_exact_instance(self):
        from unittest.mock import patch
        from snmpstudio.dialogs import CommandDialog
        from snmpstudio.mib import MibObject
        device=self.browser_agent();self.select_mib_object('setPoint');query=self.window.mib.query
        def edit(dialog):
            self.assertTrue(dialog.oid.isReadOnly());self.assertEqual(dialog.device.currentData(),device.id)
            self.assertEqual(dialog.name.text(),'setPoint');self.assertEqual(dialog.type.currentText(),'Integer32')
            dialog.name.setText('Supply target');dialog.type.setCurrentText('Gauge32');dialog.value.setText('250');dialog.verify.setChecked(True)
            dialog.accept();return dialog.result()
        with patch.object(CommandDialog,'exec',edit),patch.object(self.window.worker,'submit') as submit:
            query.write_button.click();submit.assert_not_called()
        self.assertEqual(self.window.stack.currentIndex(),3)
        command=self.config.commands[0];self.assertEqual(command['name'],'Supply target')
        self.assertEqual(command['rows'],[{'oid':'1.3.6.1.4.1.55555.1.2.0','type':'Gauge32','value':'250'}])
        self.assertTrue(command['verify']);self.assertEqual(self.store.load().commands,[command])
        self.assertEqual(self.window.command_table.item(0,0).text(),'Supply target')
        self.assertEqual(self.window.command_table.item(0,1).text(),command['rows'][0]['oid'])
        obj=MibObject('TEST','rowSetting','OBJECT-TYPE',oid='1.3.6.1.4.1.55555.10.4',syntax='Integer32',access='read-write')
        self.window.mib.index.objects.append(MibObject('TEST','row','OBJECT-TYPE',oid='1.3.6.1.4.1.55555.10',access='not-accessible'))
        self.window.mib.index.objects.append(obj);query.select_object(obj,obj.oid+'.42');self.window.navigate(2)
        def indexed(dialog):
            self.assertEqual(dialog.oid.text(),obj.oid+'.42');dialog.value.setText('5');dialog.accept();return dialog.result()
        with patch.object(CommandDialog,'exec',indexed):query.write_button.click()
        self.assertEqual(len(self.config.commands),2);self.assertEqual(self.config.commands[1]['rows'][0]['oid'],obj.oid+'.42')
        self.assertEqual(self.config.commands[0],command)

    def test_commands_validate_inline_and_show_set_success(self):
        from unittest.mock import patch
        from PySide6.QtWidgets import QMessageBox
        device=self.browser_agent();self.window.navigate(3)
        command=self.save_test_command('Set target','not-a-number',device=device.id)
        with patch.object(self.window,'error') as modal:self.window.send_button.click();modal.assert_not_called()
        self.assertEqual(self.window.command_status.text(),'Ошибка');self.assertNotIn('set',self.window.pending)
        command['rows'][0]['value']='270';command['verify']=True;self.window.commit_saved_command(command)
        with patch.object(QMessageBox,'exec',return_value=QMessageBox.StandardButton.Yes):self.window.send_button.click()
        self.assertFalse(self.window.command_table.isEnabled());self.assertFalse(self.window.send_button.isEnabled())
        deadline=time.monotonic()+4
        while 'set' in self.window.pending and time.monotonic()<deadline:self.app.processEvents();time.sleep(.01)
        self.assertNotIn('set',self.window.pending);self.assertEqual(self.window.command_status.text(),'Успешно')
        self.assertIn('270',self.window.command_result.toPlainText());self.assertIn('Контрольное чтение',self.window.command_result.toPlainText())
        self.assertIn('Set target',self.window.command_context.text());self.assertTrue(self.window.copy_command_result.isEnabled())
        self.window.clear_command_result_button.click();self.assertEqual(self.window.command_result.toPlainText(),'')
        self.assertFalse(self.window.copy_command_result.isEnabled())

    def test_full_device_walk_discovers_undocumented_and_missing_oids_per_device(self):
        from PySide6.QtCore import Qt
        from snmpstudio.models import Device
        device=self.browser_agent();query=self.window.mib.query;root='1.3.6.1.4.1.55555'
        extra=root+'.9.8.0';self.window.demo_agent.values[extra]=('Integer',99)
        query.full_walk_button.click();self.wait_for_browser_request()
        self.assertEqual(query.status.text(),'Успешно')
        self.assertEqual(len(query.values),8)
        item=self.tree_object(extra);self.assertIsNotNone(item);self.assertIn('Нет в MIB',item.text(0))
        self.assertTrue(item.data(0,Qt.ItemDataRole.UserRole+2))
        row=next(i for i,value in enumerate(query.values) if value.oid==extra)
        self.assertEqual(query.table.item(row,0).text(),'Нет в MIB')
        self.window.mib.tree.setCurrentItem(item)
        self.assertTrue(query.add_button.isEnabled());self.assertFalse(query.write_button.isEnabled())
        self.window.demo_agent.values.pop(extra);self.window.demo_agent.values.pop(root+'.1.1.0')
        query.full_walk_button.click();self.wait_for_browser_request()
        self.assertEqual(self.tree_object(extra).data(0,Qt.ItemDataRole.UserRole+1),'missing')
        self.assertEqual(self.tree_object(root+'.1.1').data(0,Qt.ItemDataRole.UserRole+1),'missing')
        self.window.demo_agent.values[root+'.1.1.0']=('Integer',260)
        query.full_walk_button.click();self.wait_for_browser_request()
        self.assertEqual(self.tree_object(root+'.1.1').data(0,Qt.ItemDataRole.UserRole+1),'present')
        other=Device(name='Other');self.config.devices.append(other);self.window.refresh_devices()
        query.device.setCurrentIndex(query.device.findData(other.id))
        self.assertIsNone(self.tree_object(extra))
        self.assertEqual(self.tree_object(root+'.1.1').data(0,Qt.ItemDataRole.UserRole+1),'')
        query.device.setCurrentIndex(query.device.findData(device.id))
        self.assertIsNotNone(self.tree_object(extra))

    def test_getnext_unknown_oid_is_inserted_without_marking_missing_objects(self):
        from PySide6.QtCore import Qt
        self.browser_agent();query=self.window.mib.query;root='1.3.6.1.4.1.55555'
        extra=root+'.1.1.5';self.window.demo_agent.values[extra]=('Integer',12)
        query.oid.setText(root+'.1.1.0');query.operation.setCurrentIndex(1)
        query.send_button.click();self.wait_for_browser_request()
        self.assertEqual(query.values[0].oid,extra)
        self.assertIsNotNone(self.tree_object(extra))
        self.assertTrue(self.tree_object(extra).data(0,Qt.ItemDataRole.UserRole+2))
        self.assertEqual(query.table.item(0,0).text(),'Нет в MIB')
        self.assertEqual(self.tree_object(root+'.1.2').data(0,Qt.ItemDataRole.UserRole+1),'')

    def test_cancelled_or_failed_walk_keeps_prior_device_observations(self):
        from PySide6.QtCore import Qt
        self.browser_agent();query=self.window.mib.query;root='1.3.6.1.4.1.55555'
        query.full_walk_button.click();self.wait_for_browser_request()
        self.window.toggle_demo_agent()
        query.full_walk_button.click();self.wait_for_browser_request()
        self.assertEqual(query.status.text(),'Ошибка')
        self.assertEqual(self.tree_object(root+'.1.1').data(0,Qt.ItemDataRole.UserRole+1),'present')

    def test_notification_controls_and_packet_types(self):
        import socket
        from snmpstudio.protocol import Value
        self.browser_agent();self.window.navigate(4)
        sock=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.close()
        self.window.listen_port.setValue(port);self.window.listen_button.click()
        self.assertFalse(self.window.listen_port.isEnabled())
        deadline=time.monotonic()+4
        while self.window.listener_pending and time.monotonic()<deadline:self.app.processEvents();time.sleep(.01)
        self.assertTrue(self.window.listener_running)
        self.assertFalse(self.window.listen_host.isEnabled());self.assertFalse(self.window.listen_port.isEnabled())
        self.assertEqual(self.window.listen_host.height(),self.window.listen_port.height())
        for kind in ('TRAP','INFORM'):
            self.window.on_notification({'type':kind,'address':'127.0.0.1','time':time.time(),'values':[Value('1.3.6.1.2.1.1.5.0','OctetString',b'edge-switch')]})
        self.assertEqual(self.window.notifications.item(0,1).text(),'TRAP')
        self.assertEqual(self.window.notifications.item(1,1).text(),'INFORM')
        self.assertEqual(self.window.notifications.item(0,4).text(),'edge-switch')
        self.assertTrue(self.window.clear_notifications_button.isEnabled())
        self.window.clear_notifications_button.click();self.assertEqual(self.window.notifications.rowCount(),0)
        self.window.listen_button.click()
        deadline=time.monotonic()+4
        while self.window.listener_pending and time.monotonic()<deadline:self.app.processEvents();time.sleep(.01)
        self.assertFalse(self.window.listener_running);self.assertTrue(self.window.listen_port.isEnabled())

    def test_many_saved_commands_fit_minimum_window_without_overlapping_actions(self):
        from PySide6.QtCore import QPoint
        self.window.navigate(3);self.window.resize(1200,760)
        for i in range(25):self.save_test_command('Command '+str(i),str(i))
        deadline=time.monotonic()+.2
        while time.monotonic()<deadline:self.app.processEvents();time.sleep(.01)
        table_bottom=self.window.command_table.mapTo(self.window,QPoint(0,self.window.command_table.height())).y()
        send_top=self.window.send_button.mapTo(self.window,QPoint()).y()
        self.assertGreaterEqual(send_top,table_bottom)
        self.assertEqual(self.window.size().width(),1200);self.assertEqual(self.window.size().height(),760)

    def test_full_device_walk_works_without_any_mib_file(self):
        self.browser_agent();mib=self.window.mib
        for i in reversed(range(mib.tabs.count())):mib.close_tab(i)
        self.validate_mib_view()
        self.assertEqual(mib.index.objects,[])
        self.assertTrue(mib.query.full_walk_button.isEnabled())
        mib.query.full_walk_button.click();self.wait_for_browser_request()
        self.assertEqual(mib.query.status.text(),'Успешно')
        self.assertEqual(len(mib.query.values),7)
        self.assertTrue(mib.query.oid.text())
        self.assertTrue(mib.query.send_button.isEnabled())
        self.assertTrue(all(mib.query.table.item(row,0).text()=='Нет в MIB' for row in range(7)))

    def test_bulk_end_of_view_is_success_and_localizes_result(self):
        self.browser_agent();self.select_mib_object('studio');query=self.window.mib.query
        query.operation.setCurrentIndex(2);query.send_button.click();self.wait_for_browser_request()
        self.assertEqual(query.status.text(),'Успешно');self.assertTrue(query.error_panel.isHidden())
        self.assertTrue(any(value.error=='EndOfMibView' for value in query.values))
        self.window.language.setCurrentIndex(1)
        row=next(i for i,value in enumerate(query.values) if value.error=='EndOfMibView')
        self.assertEqual(query.table.item(row,3).text(),'End of accessible OID space')
        self.assertEqual(query.status.text(),'Success')

    def test_request_context_survives_mib_revalidation_during_query(self):
        from snmpstudio.mib import MibIndex
        device=self.browser_agent();self.select_mib_object('temperature');mib=self.window.mib;query=mib.query
        requested=query.oid.text();query.send_button.click()
        changed=mib.current().toPlainText().replace('{ sensors 1 }','{ sensors 10 }')
        mib.apply_validation(mib.generation,MibIndex().rebuild({'changed':changed}))
        self.wait_for_browser_request()
        self.assertEqual(query.status.text(),'Успешно')
        self.assertEqual(query.result_context[0],device.id)
        self.assertEqual(query.result_context[3],requested)
        self.assertEqual(query.values[0].oid,requested)
        self.assertIn(requested,mib.observations[device.id].values)

    def test_oid_actions_live_in_header_and_inactive_actions_are_hidden(self):
        self.browser_agent();self.window.navigate(2);mib=self.window.mib;query=mib.query
        self.assertTrue(mib.object_card.isAncestorOf(query.add_button))
        self.assertTrue(mib.object_card.isAncestorOf(query.write_button))
        self.assertFalse(query.isAncestorOf(query.add_button))
        self.select_mib_object('temperature');self.app.processEvents()
        self.assertTrue(query.add_button.isVisible());self.assertTrue(query.write_button.isHidden())
        self.select_mib_object('setPoint');self.app.processEvents()
        self.assertTrue(query.add_button.isVisible());self.assertTrue(query.write_button.isVisible())
        self.assertEqual(mib.object_header.height(),38)
        query.send_button.click()
        self.assertTrue(query.add_button.isHidden());self.assertTrue(query.write_button.isHidden())
        self.wait_for_browser_request()
        self.assertTrue(query.add_button.isVisible());self.assertTrue(query.write_button.isVisible())
        self.select_mib_object('studio')
        self.assertTrue(query.add_button.isHidden());self.assertTrue(query.write_button.isHidden())

    def test_mib_toolbar_matches_search_edges_and_height(self):
        from PySide6.QtCore import QPoint
        mib=self.window.mib;self.window.navigate(2)
        for language in ('ru','en'):
            self.window.language.setCurrentIndex(self.window.language.findData(language))
            self.app.processEvents()
            self.assertEqual(mib.open_button.mapTo(mib,QPoint()).x(),mib.search.mapTo(mib,QPoint()).x())
            button_right=mib.validate_button.mapTo(mib,QPoint(mib.validate_button.width(),0)).x()
            search_right=mib.search.mapTo(mib,QPoint(mib.search.width(),0)).x()
            self.assertEqual(button_right,search_right)
            self.assertEqual(mib.open_button.height(),mib.search.height())
            self.assertEqual(mib.validate_button.height(),mib.search.height())

    def test_repeated_getnext_advances_oid_tree_and_keeps_result_and_button_position(self):
        from PySide6.QtCore import QPoint
        self.browser_agent();mib=self.window.mib;query=mib.query;root='1.3.6.1.4.1.55555'
        extra=root+'.1.2.5';self.window.demo_agent.values[extra]=('Integer',12)
        self.select_mib_object('temperature');query.operation.setCurrentIndex(1)
        mib.search.setText(root+'.1.1');mib.tree.currentItem().parent().setExpanded(False)
        self.app.processEvents();button_position=query.send_button.mapTo(self.window,QPoint())
        previous=query.oid.text()
        for oid,name in [(root+'.1.2.0','setPoint'),(extra,extra),(root+'.1.3.0','operatingMode')]:
            query.send_button.click();self.wait_for_browser_request();self.app.processEvents()
            self.assertEqual(query.oid.text(),oid)
            self.assertEqual(mib.selected().name,name)
            self.assertEqual(query.result_context[3],previous)
            self.assertEqual(query.values[0].oid,oid)
            self.assertEqual(query.table.item(0,1).text(),oid)
            self.assertEqual(query.operation.currentData(),'GETNEXT')
            self.assertEqual(query.status.text(),'Успешно')
            self.assertEqual(mib.object_title.toolTip(),name)
            self.assertEqual(query.send_button.mapTo(self.window,QPoint()),button_position)
            parent=mib.tree.currentItem().parent()
            while parent:
                self.assertTrue(parent.isExpanded());parent=parent.parent()
            previous=oid
        self.assertEqual(mib.search.text(),'')
        selected_oid=mib.selected().oid
        query.send_button.click();self.wait_for_browser_request()
        self.assertEqual(query.values[0].error,'EndOfMibView')
        self.assertEqual(query.oid.text(),previous)
        self.assertEqual(mib.selected().oid,selected_oid)
        self.assertEqual(query.status.text(),'Успешно')

    def test_getnext_error_or_cancel_does_not_advance(self):
        self.browser_agent();mib=self.window.mib;query=mib.query
        self.select_mib_object('temperature');query.operation.setCurrentIndex(1);oid=query.oid.text()
        device_id=query.device.currentData();self.window.secrets.session[f'{device_id}:community']='wrong'
        query.send_button.click();self.wait_for_browser_request()
        self.assertEqual(query.status.text(),'Ошибка');self.assertEqual(query.oid.text(),oid)
        self.assertEqual(mib.selected().name,'temperature')
        self.window.secrets.session[f'{device_id}:community']='public'
        query.send_button.click();query.cancel_button.click();self.wait_for_browser_request()
        self.assertEqual(query.oid.text(),oid);self.assertEqual(mib.selected().name,'temperature')

    def test_getnext_non_increasing_reply_keeps_target_and_displays_error(self):
        from snmpstudio.protocol import Value
        device=self.browser_agent();self.select_mib_object('temperature');query=self.window.mib.query;oid=query.oid.text()
        query.operation.setCurrentIndex(1);query.begin(device,'GETNEXT',oid)
        self.window.mib_request_context=query.result_context;self.window.pending.add('mib-query')
        self.window.on_result('mib-query',[Value(oid,'Integer',253)],'')
        self.assertEqual(query.oid.text(),oid)
        self.assertEqual(self.window.mib.selected().name,'temperature')
        self.assertEqual(query.status.text(),'Ошибка')
        self.assertTrue(query.error_panel.isVisible())
        self.assertEqual(query.values[0].value,253)

    def test_cancel_command_dialog_does_not_save_or_navigate(self):
        from unittest.mock import patch
        from snmpstudio.dialogs import CommandDialog
        self.browser_agent();self.select_mib_object('setPoint')
        with patch.object(CommandDialog,'exec',return_value=0),patch.object(self.window.worker,'submit') as submit:
            self.window.mib.query.write_button.click();submit.assert_not_called()
        self.assertEqual(self.config.commands,[]);self.assertEqual(self.window.stack.currentIndex(),2)

    def test_edit_saved_command_updates_in_place_without_duplicate_and_keeps_oid_locked(self):
        from unittest.mock import patch
        from snmpstudio.dialogs import CommandDialog
        device=self.browser_agent();command=self.save_test_command('Before','230',device=device.id)
        def edit(dialog):
            self.assertTrue(dialog.oid.isReadOnly());dialog.oid.setText('1.3.6.1.2.1.1.1.0')
            dialog.name.setText('After');dialog.value.setText('280');dialog.type.setCurrentText('Unsigned32')
            dialog.accept();return dialog.result()
        with patch.object(CommandDialog,'exec',edit):self.window.edit_command_button.click()
        self.assertEqual(len(self.config.commands),1);saved=self.config.commands[0]
        self.assertEqual(saved['id'],command['id']);self.assertEqual(saved['name'],'After')
        self.assertEqual(saved['rows'][0]['oid'],command['rows'][0]['oid']);self.assertEqual(saved['rows'][0]['value'],'280')
        self.assertEqual(self.store.load().commands,[saved])

    def test_command_dialog_validation_and_database_failure_leave_values_for_correction(self):
        from unittest.mock import patch
        from snmpstudio.dialogs import CommandDialog
        device=self.browser_agent();command=self.save_test_command('Original','240',device=device.id)
        dialog=CommandDialog(self.config,self.window.mib.index,command,self.window,on_save=self.window.commit_saved_command)
        dialog.value.setText('1001');dialog.accept()
        self.assertEqual(dialog.result(),0);self.assertFalse(dialog.error_panel.isHidden())
        self.assertEqual(self.config.commands,[command])
        dialog.value.setText('290');dialog.name.setText('Updated')
        with patch.object(self.store,'save',side_effect=RuntimeError('database is locked')):dialog.accept()
        self.assertEqual(dialog.result(),0);self.assertEqual(dialog.value.text(),'290')
        self.assertEqual(dialog.error_text.text(),'database is locked');self.assertEqual(self.config.commands,[command])
        dialog.accept();self.assertEqual(dialog.result(),1);self.assertEqual(self.config.commands[0]['name'],'Updated');dialog.deleteLater()

    def test_saved_commands_search_and_delete_use_identity_not_name_or_visible_row(self):
        from unittest.mock import patch
        from PySide6.QtWidgets import QMessageBox
        first=self.save_test_command('Same','220');second=self.save_test_command('Same','270',oid='1.3.6.1.4.1.55555.1.3.0')
        self.window.command_search.setText('.1.3.0');self.assertEqual(self.window.command_table.rowCount(),1)
        self.window.command_table.selectRow(0)
        with patch.object(QMessageBox,'exec',return_value=QMessageBox.StandardButton.Yes):self.window.delete_saved_command_button.click()
        self.assertEqual(self.store.load().commands,[first]);self.assertEqual(self.window.command_table.rowCount(),0)
        self.window.command_search.clear();self.assertEqual(self.window.command_table.rowCount(),1)
        self.assertIsNone(self.window.selected_command())

    def test_saved_command_with_removed_device_can_be_reassigned_without_changing_oid(self):
        from unittest.mock import patch
        from snmpstudio.dialogs import CommandDialog
        device=self.browser_agent();command=self.save_test_command('Reassign','230',device='deleted-device')
        self.assertFalse(self.window.send_button.isEnabled());self.assertTrue(self.window.edit_command_button.isEnabled())
        self.assertEqual(self.window.command_table.item(0,2).text(),'Устройство удалено')
        def edit(dialog):
            self.assertEqual(dialog.device.currentIndex(),-1);dialog.device.setCurrentIndex(dialog.device.findData(device.id));dialog.accept();return dialog.result()
        with patch.object(CommandDialog,'exec',edit):self.window.edit_command_button.click()
        self.assertTrue(self.window.send_button.isEnabled());self.assertEqual(self.config.commands[0]['device'],device.id)
        self.assertEqual(self.config.commands[0]['rows'],command['rows'])

    def test_legacy_multi_oid_command_preserves_grouping_through_edit_and_export(self):
        from unittest.mock import patch
        from snmpstudio.dialogs import CommandDialog
        device=self.browser_agent();command=self.save_test_command('Legacy group','220',device=device.id)
        command['rows'].append({'oid':'1.3.6.1.4.1.55555.1.3.0','type':'Integer32','value':'2'})
        self.window.commit_saved_command(command)
        def edit(dialog):
            self.assertEqual(len(dialog.row_fields),2);self.assertTrue(all(oid.isReadOnly() for oid,_,_ in dialog.row_fields))
            dialog.row_fields[1][2].setText('3');dialog.name.setText('Renamed group');dialog.accept();return dialog.result()
        with patch.object(CommandDialog,'exec',edit):self.window.edit_command_button.click()
        self.assertEqual(len(self.config.commands),1);self.assertEqual(len(self.window.command_rows()),2)
        self.assertEqual(self.config.commands[0]['rows'][1]['value'],'3')
        with TemporaryDirectory() as tmp:
            path=Path(tmp)/'saved.json';self.store.export(self.config,path);restored=Store.read_import(path)
            self.assertEqual(restored.commands,self.config.commands)

    def test_browser_set_fields_confirmation_write_and_readback(self):
        from unittest.mock import patch
        from PySide6.QtWidgets import QMessageBox
        self.browser_agent();self.select_mib_object('setPoint');query=self.window.mib.query
        self.assertTrue(query.set_fields.isHidden());query.operation.setCurrentIndex(query.operation.findData('SET'))
        self.assertFalse(query.set_fields.isHidden());self.assertEqual(query.set_type.currentText(),'Integer32')
        query.set_value.setText('275')
        with patch.object(QMessageBox,'exec',return_value=QMessageBox.StandardButton.No):query.send_button.click()
        self.assertFalse(query.busy);self.assertEqual(query.set_value.text(),'275');self.assertEqual(len(self.window.demo_agent.requests),0)
        with patch.object(QMessageBox,'exec',return_value=QMessageBox.StandardButton.Yes):query.send_button.click()
        self.assertTrue(query.busy);self.assertTrue(query.cancel_button.isHidden());self.assertFalse(query.set_value.isEnabled())
        self.wait_for_browser_request();self.assertEqual(query.status.text(),'Успешно');self.assertEqual(query.values[0].value,275)
        self.assertTrue(any(packet.operation=='SET' and packet.direction=='TX' for packet in self.window.packets.values()))
        self.assertTrue(any(packet.operation=='SET' and packet.direction=='RX' for packet in self.window.packets.values()))
        query.operation.setCurrentIndex(query.operation.findData('GET'));self.assertTrue(query.set_fields.isHidden())
        query.send_button.click();self.wait_for_browser_request();self.assertEqual(query.values[0].value,275)
        query.operation.setCurrentIndex(query.operation.findData('SET'));self.assertEqual(query.set_value.text(),'275')
        self.select_mib_object('operatingMode');self.assertEqual(query.set_value.text(),'')

    def test_browser_set_local_validation_and_failure_are_inline(self):
        from unittest.mock import patch
        from PySide6.QtWidgets import QMessageBox
        device=self.browser_agent();self.select_mib_object('setPoint');query=self.window.mib.query
        query.operation.setCurrentIndex(query.operation.findData('SET'));query.set_value.setText('1001')
        with patch.object(QMessageBox,'exec') as question:
            query.send_button.click();question.assert_not_called()
        self.assertEqual(query.status.text(),'Ошибка');self.assertFalse(query.error_panel.isHidden())
        self.assertFalse(self.window.demo_agent.requests)
        self.window.secrets.session[device.id+':write_community']='bad-write';device.retries=3;query.set_value.setText('260')
        with patch.object(QMessageBox,'exec',return_value=QMessageBox.StandardButton.Yes):query.send_button.click()
        self.wait_for_browser_request();self.assertEqual(query.status.text(),'Ошибка')
        self.assertEqual(len([packet for packet in self.window.packets.values() if packet.operation=='SET' and packet.direction=='TX']),1)

    def test_import_configuration_replaces_saved_command_list_and_preserves_identifiers(self):
        from unittest.mock import patch
        from PySide6.QtWidgets import QMessageBox,QFileDialog
        from snmpstudio.models import Device
        self.save_test_command('Old command','220');device=Device(name='Imported device')
        command={'id':'imported-command','name':'Imported command','device':device.id,'rows':[{'oid':'1.3.6.1.4.1.55555.1.2.0','type':'Integer32','value':'250'}],'verify':True}
        imported=Configuration(devices=[device],commands=[command])
        with TemporaryDirectory() as tmp:
            path=Path(tmp)/'config.json';self.store.export(imported,path)
            with patch.object(QFileDialog,'getOpenFileName',return_value=(str(path),'')),patch.object(QMessageBox,'question',return_value=QMessageBox.StandardButton.Yes),patch.object(self.window,'error') as error:
                self.window.import_config();error.assert_not_called()
        self.assertEqual(self.config.commands,[command]);self.assertEqual(self.store.load().commands,[command])
        self.assertEqual(self.window.command_table.rowCount(),1);self.assertEqual(self.window.command_table.item(0,0).text(),'Imported command')
        self.assertIsNone(self.window.selected_command());self.window.command_table.selectRow(0)
        self.assertEqual(self.window.selected_command()['id'],'imported-command');self.assertTrue(self.window.send_button.isEnabled())

    def test_browser_set_without_mib_does_not_claim_echo_is_a_read_observation(self):
        from unittest.mock import patch
        from PySide6.QtWidgets import QMessageBox
        self.browser_agent();mib=self.window.mib;query=mib.query
        for i in reversed(range(mib.tabs.count())):mib.close_tab(i)
        self.validate_mib_view();query.oid.setText('1.3.6.1.4.1.55555.1.2.0')
        query.operation.setCurrentIndex(query.operation.findData('SET'));query.set_value.setText('265')
        with patch.object(QMessageBox,'exec',return_value=QMessageBox.StandardButton.Yes):query.send_button.click()
        self.wait_for_browser_request();self.assertEqual(query.status.text(),'Успешно');self.assertEqual(query.values[0].value,265)
        self.assertTrue(query.add_button.isHidden());self.assertTrue(query.write_button.isHidden())
        self.assertEqual(query.table.item(0,0).text(),'Нет в MIB');self.assertEqual(query.table.item(0,1).text(),query.oid.text())
        query.operation.setCurrentIndex(query.operation.findData('GET'));query.send_button.click();self.wait_for_browser_request()
        self.assertFalse(query.add_button.isHidden())

    def test_browser_set_fields_are_not_clipped_at_minimum_window_size(self):
        from PySide6.QtCore import QPoint,QRect,QEvent
        self.browser_agent();self.select_mib_object('setPoint');query=self.window.mib.query
        self.window.resize(1200,760);self.window.navigate(2)
        for language in ('ru','en'):
            self.window.language.setCurrentIndex(self.window.language.findData(language))
            for theme in ('light','dark'):
                if self.config.theme!=theme:self.window.toggle_theme()
                for operation in ('SET','GETBULK','GET'):
                    query.operation.setCurrentIndex(query.operation.findData(operation))
                    for _ in range(4):self.app.sendPostedEvents(None,QEvent.Type.LayoutRequest);self.app.processEvents()
                    controls=[query.device,query.operation,query.oid]
                    if operation=='SET':controls += [query.set_type,query.set_value]
                    for control in controls:
                        parent=control.parentWidget()
                        while parent is not query:
                            self.assertTrue(parent.rect().contains(QRect(control.mapTo(parent,QPoint()),control.size())),(language,theme,operation,control.objectName(),parent.size(),control.geometry()))
                            parent=parent.parentWidget()
                    results=query.table.parentWidget()
                    send_bottom=query.send_button.mapTo(query,QPoint(0,query.send_button.height())).y()
                    self.assertGreaterEqual(results.y(),send_bottom+10)
                    self.assertGreaterEqual(results.y(),query.setup.geometry().bottom()+10)
                    self.assertEqual(self.window.width(),1200);self.assertEqual(self.window.height(),760)

    def test_add_command_manually_without_mib_saves_all_fields_without_sending(self):
        from unittest.mock import patch
        from snmpstudio.dialogs import CommandDialog
        from snmpstudio.models import Device
        device=Device(name='Manual device');self.config.devices.append(device);self.window.refresh_devices();self.window.navigate(3)
        self.assertEqual(self.window.add_command_button.text(),'Добавить');self.assertFalse(self.window.mib.index.objects)
        def fill(dialog):
            self.assertEqual(dialog.windowTitle(),'Новая команда');self.assertFalse(dialog.oid.isReadOnly())
            self.assertEqual(dialog.name.text(),'');self.assertEqual(dialog.oid.text(),'');self.assertEqual(dialog.value.text(),'')
            dialog.name.setText('Имя устройства');dialog.oid.setText('1.3.6.1.2.1.1.5.0')
            dialog.type.setCurrentText('OctetString');dialog.value.setText('  Тестовый узел  ');dialog.verify.setChecked(True)
            dialog.accept();return dialog.result()
        with patch.object(CommandDialog,'exec',fill),patch.object(self.window.worker,'submit') as submit:
            self.window.add_command_button.click();submit.assert_not_called()
        command=self.config.commands[0];self.assertEqual(command['name'],'Имя устройства');self.assertEqual(command['device'],device.id)
        self.assertEqual(command['rows'],[{'oid':'1.3.6.1.2.1.1.5.0','type':'OctetString','value':'  Тестовый узел  '}])
        self.assertTrue(command['verify']);self.assertEqual(self.store.load().commands,[command])
        self.assertEqual(self.window.selected_command(),command);self.assertEqual(self.window.stack.currentIndex(),3)
        self.assertTrue(self.window.send_button.isEnabled());self.window.language.setCurrentIndex(1)
        self.assertEqual(self.window.add_command_button.text(),'Add')

    def test_cancel_manual_command_does_not_save_or_leave_commands_page(self):
        from unittest.mock import patch
        from snmpstudio.dialogs import CommandDialog
        from snmpstudio.models import Device
        self.config.devices.append(Device());self.window.refresh_devices();self.window.navigate(3)
        with patch.object(CommandDialog,'exec',return_value=0),patch.object(self.window.worker,'submit') as submit:
            self.window.add_command_button.click();submit.assert_not_called()
        self.assertEqual(self.config.commands,[]);self.assertEqual(self.window.command_table.rowCount(),0)
        self.assertEqual(self.window.stack.currentIndex(),3)

    def test_manual_command_invalid_oid_and_type_stay_open_for_correction(self):
        from unittest.mock import patch
        from snmpstudio.dialogs import CommandDialog
        from snmpstudio.models import Device
        self.config.devices.append(Device());self.window.refresh_devices();self.window.navigate(3)
        def fill(dialog):
            dialog.name.setText('Target');dialog.oid.setText('bad-oid');dialog.value.setText('270');dialog.accept()
            self.assertEqual(dialog.result(),0);self.assertFalse(dialog.error_panel.isHidden());self.assertEqual(self.config.commands,[])
            dialog.oid.setText('1.3.6.1.4.1.55555.1.2.0');dialog.value.setText('bad-value');dialog.accept()
            self.assertEqual(dialog.result(),0);self.assertEqual(dialog.name.text(),'Target');self.assertEqual(self.config.commands,[])
            dialog.value.setText('270');dialog.accept();return dialog.result()
        with patch.object(CommandDialog,'exec',fill):self.window.add_command_button.click()
        self.assertEqual(self.store.load().commands[0]['rows'][0]['value'],'270')

    def open_constructor(self):
        self.open_test_mib();self.window.editor_enabled.setChecked(True);self.window.navigate(self.window.editor_page_index)
        mib=self.window.mib;mib.editor_mode.setCurrentIndex(0);mib.constructor.refresh(force=True);return mib,mib.constructor
    def constructor_select(self,builder,name):
        from PySide6.QtCore import Qt
        items=builder.tree.findItems(name+' · ',Qt.MatchFlag.MatchStartsWith|Qt.MatchFlag.MatchRecursive)
        self.assertTrue(items,name);builder.tree.setCurrentItem(items[0])
    def test_constructor_edits_source_undo_and_resynchronizes_manual_changes(self):
        mib,builder=self.open_constructor();self.constructor_select(builder,'temperature');editor=mib.current();original=editor.toPlainText()
        builder.fields['description'].setPlainText('Edited through form');builder.fields['unit'].setText('degC')
        self.assertTrue(builder.apply());self.assertIn('DESCRIPTION "Edited through form"',editor.toPlainText());self.assertTrue(editor.document().isModified())
        self.assertIn('Integer32 (-500..1500)',editor.toPlainText());editor.undo();self.assertEqual(editor.toPlainText(),original)
        editor.setPlainText(original.replace('Temperature in tenths of a degree Celsius.','Edited as source'))
        builder.refresh(force=True);self.constructor_select(builder,'temperature');self.assertEqual(builder.fields['description'].toPlainText(),'Edited as source')
        self.window.language.setCurrentIndex(1);self.assertEqual(mib.editor_mode.itemText(0),'Constructor')
    def test_constructor_adds_branch_parameter_notification_and_blocks_referenced_deletion(self):
        from unittest.mock import patch
        from PySide6.QtWidgets import QMessageBox
        mib,builder=self.open_constructor();self.constructor_select(builder,'studio')
        builder.kind.setCurrentIndex(builder.kind.findData('OBJECT IDENTIFIER'));builder.new_object();builder.fields['name'].setText('custom');builder.fields['number'].setText('20');self.assertTrue(builder.apply())
        builder.kind.setCurrentIndex(builder.kind.findData('OBJECT-TYPE'));builder.new_object();builder.fields['name'].setText('humidity');builder.fields['constraints'].setText('(0..100)');self.assertTrue(builder.apply())
        builder.kind.setCurrentIndex(builder.kind.findData('NOTIFICATION-TYPE'));builder.new_object();builder.fields['name'].setText('humidityAlarm');builder.fields['parent'].setText('custom');builder.fields['number'].setText('2');builder.fields['objects'].setText('humidity');self.assertTrue(builder.apply())
        self.constructor_select(builder,'humidity')
        before=mib.current().toPlainText()
        with patch.object(QMessageBox,'question',return_value=QMessageBox.StandardButton.Yes):builder.remove()
        self.assertEqual(mib.current().toPlainText(),before);self.assertIn('referenced',builder.message.text())
    def test_constructor_rejects_stale_or_invalid_source_without_losing_manual_text(self):
        mib,builder=self.open_constructor();self.constructor_select(builder,'temperature')
        builder.fields['description'].setPlainText('Form draft');mib.current().appendPlainText('-- manual change')
        source=mib.current().toPlainText();self.assertFalse(builder.apply());self.assertEqual(mib.current().toPlainText(),source)
        self.assertEqual(builder.fields['description'].toPlainText(),'Form draft')
        builder.dirty=False;mib.current().setPlainText(source.replace('MAX-ACCESS','MAX-ACCESS @'));builder.refresh(force=True)
        self.assertIsNone(builder.document);self.assertFalse(builder.add.isEnabled());self.assertFalse(builder.apply_button.isEnabled())
    def test_constructor_new_module_form_and_save_create_parseable_file(self):
        from unittest.mock import patch
        from snmpstudio.constructor import NewMibDialog
        from snmpstudio.mib import parse_source
        from PySide6.QtWidgets import QFileDialog
        self.window.editor_enabled.setChecked(True);mib=self.window.mib
        def fill(dialog):
            dialog.module_name.setText('MY-DEVICE-MIB');dialog.root_name.setText('myDevice');dialog.organization.setText('Acme');dialog.number.setText('56888');dialog.accept();return dialog.result()
        with patch.object(NewMibDialog,'exec',fill):mib.new_file()
        self.assertEqual(mib.editor_mode.currentIndex(),0);self.assertIn('myDevice MODULE-IDENTITY',mib.current().toPlainText());self.assertEqual(parse_source(mib.current().toPlainText())[2],[])
        with TemporaryDirectory() as tmp:
            path=Path(tmp)/'MY-DEVICE-MIB.mib'
            with patch.object(QFileDialog,'getSaveFileName',return_value=(str(path),'')):self.assertTrue(mib.save())
            self.assertEqual(path.read_text(),mib.current().toPlainText());self.assertFalse(mib.current().document().isModified())

    def start_notification_receiver(self):
        import socket
        sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.close()
        self.window.listen_port.setValue(port);self.window.listen_button.click()
        deadline=time.monotonic()+4
        while self.window.listener_pending and time.monotonic()<deadline:self.app.processEvents();time.sleep(.01)
        self.assertTrue(self.window.listener_running)
    def wait_notification_rows(self,count):
        deadline=time.monotonic()+5
        while (self.window.notifications.rowCount()<count or 'demo-notify' in self.window.pending) and time.monotonic()<deadline:self.app.processEvents();time.sleep(.01)
        self.assertGreaterEqual(self.window.notifications.rowCount(),count)
    def test_demo_trap_inform_end_to_end_mib_decode_and_wire_journal(self):
        self.browser_agent();self.window.navigate(4);self.start_notification_receiver()
        table=self.window.notifications
        for kind in ('TRAP','INFORM'):
            before=table.rowCount();self.window.demo_notification_kind.setCurrentText(kind);self.window.demo_notification_button.click()
            self.wait_notification_rows(before+5)
            self.assertEqual(table.item(before,1).text(),kind)
        names=[table.item(row,2).text() for row in range(table.rowCount())]
        self.assertIn('temperature',names);self.assertIn('operatingMode',names)
        row=names.index('operatingMode');self.assertEqual(table.item(row,4).text(),'running (2)')
        table.selectRow(row);self.assertIn('demoStatusChanged',self.window.notification_details.text())
        self.assertEqual(self.window.demo_notification_status.text(),'INFORM подтверждён')
        self.assertTrue(any(p.operation=='INFORM' and p.direction=='TX' for p in self.window.packets.values()))
        self.assertTrue(any(p.operation=='INFORM' and p.direction=='RX' and p.plaintext for p in self.window.packets.values()))
        self.window.notification_journal();self.assertEqual(self.window.stack.currentIndex(),5);self.assertIsNotNone(self.window.inspector.packet)
    def test_notification_mib_load_and_unload_redecode_original_values(self):
        from snmpstudio.protocol import Value
        data={'type':'TRAP','address':'127.0.0.1','time':time.time(),'values':[Value('1.3.6.1.6.3.1.1.4.1.0','ObjectIdentifier','1.3.6.1.4.1.55555.2.0.1'),Value('1.3.6.1.4.1.55555.1.3.0','Integer',2)]}
        self.window.on_notification(data);table=self.window.notifications
        self.assertEqual(table.item(1,2).text(),'Нет в MIB');self.assertEqual(table.item(1,4).text(),'2')
        self.open_test_mib();self.validate_mib_view()
        self.assertEqual(table.item(1,2).text(),'operatingMode');self.assertEqual(table.item(1,4).text(),'running (2)')
        self.assertIn('demoStatusChanged',table.item(0,4).text())
        for i in reversed(range(self.window.mib.tabs.count())):self.window.mib.close_tab(i)
        self.validate_mib_view();self.assertEqual(table.item(1,4).text(),'2')
    def test_notifications_received_by_demo_agent_appear_in_app_with_ack_packets(self):
        import asyncio
        from snmpstudio.simulator import DemoAgent
        self.browser_agent()
        with DemoAgent() as sender:result=asyncio.run(sender.send_notification('127.0.0.1',self.window.demo_agent.port,'INFORM'))
        self.assertTrue(result['acknowledged']);self.wait_notification_rows(5)
        self.window.notifications.selectRow(0)
        self.assertIn('Принято демоагентом',self.window.notification_details.text())
        self.assertTrue(any(p.operation=='INFORM' and p.direction=='RX' for p in self.window.packets.values()))
        self.assertTrue(any(p.operation=='INFORM' and p.direction=='TX' for p in self.window.packets.values()))

    def test_save_formats_oid_tree_keeps_comments_and_supports_undo(self):
        from snmpstudio.mib_format import format_mib
        self.window.editor_enabled.setChecked(True);mib=self.window.mib
        text='FORMAT-MIB DEFINITIONS ::= BEGIN\nIMPORTS enterprises FROM SNMPv2-SMI;\nroot OBJECT IDENTIFIER ::= { enterprises 59999 }\n-- Child documentation\nchild OBJECT IDENTIFIER ::= { root 1 }\nEND\n'
        with TemporaryDirectory() as tmp:
            path=Path(tmp)/'FORMAT-MIB.mib';path.write_text(text);mib.open_path(path);editor=mib.current();editor.appendPlainText('-- user note')
            draft=editor.toPlainText();self.assertTrue(mib.save())
            expected=format_mib(draft);self.assertEqual(path.read_text(),expected);self.assertEqual(editor.toPlainText(),expected)
            self.assertIn('\n    -- Child documentation\n    child OBJECT IDENTIFIER',expected);self.assertIn('-- user note',expected)
            self.assertFalse(editor.document().isModified());self.assertEqual(editor.original,expected.encode('utf-8'))
            editor.undo();self.assertEqual(editor.toPlainText(),draft);self.assertTrue(editor.document().isModified());self.assertEqual(path.read_text(),expected)
    def test_format_failure_or_external_file_change_keeps_draft_and_disk(self):
        from unittest.mock import patch
        from PySide6.QtWidgets import QMessageBox
        from snmpstudio.mib_format import MibFormatError
        self.window.editor_enabled.setChecked(True);mib=self.window.mib
        text='FORMAT-MIB DEFINITIONS ::= BEGIN IMPORTS enterprises FROM SNMPv2-SMI; root OBJECT IDENTIFIER ::= { enterprises 59999 } END'
        with TemporaryDirectory() as tmp:
            path=Path(tmp)/'FORMAT-MIB.mib';path.write_text(text);mib.open_path(path);editor=mib.current();editor.appendPlainText('-- draft');draft=editor.toPlainText();original=editor.original
            with patch('snmpstudio.editor.format_mib',side_effect=MibFormatError('Cannot format')),patch.object(QMessageBox,'warning'):
                self.assertFalse(mib.save())
            self.assertEqual(path.read_text(),text);self.assertEqual(editor.toPlainText(),draft);self.assertEqual(editor.original,original);self.assertTrue(editor.document().isModified())
            path.write_text('outside change')
            with patch.object(QMessageBox,'warning'):self.assertFalse(mib.save())
            self.assertEqual(path.read_text(),'outside change');self.assertEqual(editor.toPlainText(),draft);self.assertTrue(editor.document().isModified())
    def test_saving_invalid_draft_requires_explicit_confirmation_and_keeps_raw_text(self):
        from unittest.mock import patch
        from PySide6.QtWidgets import QMessageBox
        self.window.editor_enabled.setChecked(True);mib=self.window.mib
        text='FORMAT-MIB DEFINITIONS ::= BEGIN IMPORTS enterprises FROM SNMPv2-SMI; root OBJECT IDENTIFIER ::= { enterprises 59999 } END'
        with TemporaryDirectory() as tmp:
            path=Path(tmp)/'FORMAT-MIB.mib';path.write_text(text);mib.open_path(path);editor=mib.current();draft=text.replace('END','@ END');editor.setPlainText(draft)
            with patch.object(QMessageBox,'question',return_value=QMessageBox.StandardButton.No):self.assertFalse(mib.save())
            self.assertEqual(path.read_text(),text)
            with patch.object(QMessageBox,'question',return_value=QMessageBox.StandardButton.Yes):self.assertTrue(mib.save())
            self.assertEqual(path.read_text(),draft);self.assertEqual(editor.toPlainText(),draft)
