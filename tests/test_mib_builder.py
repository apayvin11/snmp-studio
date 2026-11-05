# SPDX-License-Identifier: Apache-2.0
from pathlib import Path
import unittest
from snmpstudio.mib_builder import BuilderDocument
from snmpstudio.mib import MibIndex
SOURCE=(Path(__file__).resolve().parents[1]/'snmpstudio/data/SNMPSTUDIO-DEMO-MIB.mib').read_text()
class BuilderTests(unittest.TestCase):
    def fields(self,doc,name='temperature'):return doc.fields(doc.node(name))
    def test_edit_preserves_unrelated_text_constraints_and_comments(self):
        source=SOURCE.replace('temperature OBJECT-TYPE','-- temperature OBJECT-TYPE fake\ntemperature OBJECT-TYPE').replace('::= { sensors 1 }','::= { sensors -- assignment comment\n 1 }')
        doc=BuilderDocument(source);fields=self.fields(doc);fields['description']='Changed temperature'
        result=doc.change(fields,'temperature')
        self.assertEqual(result,source.replace('Temperature in tenths of a degree Celsius.','Changed temperature'))
        self.assertIn('Integer32 (-500..1500)',result)
    def test_add_parameter_adds_imports_and_can_delete_only_leaf(self):
        doc=BuilderDocument(SOURCE);fields=self.fields(doc);fields.update(name='label',number='4',syntax='DisplayString',constraints='',unit='')
        result=doc.change(fields);index=MibIndex().rebuild({'test':result})
        self.assertEqual(index.resolve('label.0'),'1.3.6.1.4.1.55555.1.4.0');self.assertIn('DisplayString FROM SNMPv2-TC',result)
        changed=BuilderDocument(result)
        with self.assertRaises(ValueError):changed.delete('sensors')
        self.assertNotIn('label OBJECT-TYPE',changed.delete('label'))
    def test_add_units_uses_valid_clause_order(self):
        source=SOURCE.replace('    UNITS "degrees Celsius / 10"\n','');doc=BuilderDocument(source);fields=self.fields(doc);fields['unit']='C'
        result=doc.change(fields,'temperature');self.assertLess(result.index('UNITS'),result.index('MAX-ACCESS'))
    def test_invalid_constraints_duplicate_oid_and_name_do_not_change_original(self):
        doc=BuilderDocument(SOURCE);fields=self.fields(doc);fields.update(name='newValue',constraints='(not valid)',number='4')
        with self.assertRaises(ValueError):doc.change(fields)
        fields.update(constraints='',number='1')
        with self.assertRaises(ValueError):doc.change(fields)
        fields.update(name='setPoint',number='4')
        with self.assertRaises(ValueError):doc.change(fields)
        self.assertEqual(doc.text,SOURCE)
    def test_revision_description_and_forward_references_are_preserved(self):
        source=SOURCE.replace('    ::= { enterprises 55555 }','    REVISION "202611040000Z"\n    DESCRIPTION "First revision"\n    ::= { enterprises 55555 }')
        source=source.replace('sensors OBJECT IDENTIFIER', 'alarm NOTIFICATION-TYPE\n OBJECTS { temperature }\n STATUS current\n DESCRIPTION "Alarm"\n ::= { studio 2 }\n\nsensors OBJECT IDENTIFIER')
        doc=BuilderDocument(source);fields=self.fields(doc,'studio');fields['description']='Module description'
        result=doc.change(fields,'studio');self.assertIn('DESCRIPTION "First revision"',result);self.assertIn('DESCRIPTION "Module description"',result)
        self.assertEqual(doc.node('temperature').tokens[1].value,'OBJECT-TYPE')
    def test_new_module_and_multiple_import_groups(self):
        doc=BuilderDocument('TEST-MIB DEFINITIONS ::= BEGIN\nEND\n');fields={'name':'root','kind':'MODULE-IDENTITY','parent':'enterprises','number':'55555','updated':'202611050000Z','organization':'Acme','contact':'Test','description':'Test'}
        result=doc.change(fields);self.assertIn('MODULE-IDENTITY, enterprises FROM SNMPv2-SMI',result)
        fields.update(kind='OBJECT-TYPE',name='label',syntax='DisplayString',constraints='',unit='',access='read-only',status='current')
        text=doc.change(fields);self.assertEqual(text.count('IMPORTS'),1);self.assertIn('SNMPv2-TC',text)
    def test_invalid_source_never_becomes_a_rebuilt_document(self):
        with self.assertRaises(ValueError):BuilderDocument(SOURCE.replace('MAX-ACCESS','MAX-ACCESS @'))

    def test_rename_updates_references_but_preserves_comments_and_strings(self):
        source=SOURCE+'-- sensors outside declaration\n';doc=BuilderDocument(source);fields=self.fields(doc,'sensors');fields['name']='readings'
        result=doc.change(fields,'sensors');self.assertIn('readings OBJECT IDENTIFIER',result);self.assertIn('{ readings 1 }',result);self.assertIn('-- sensors outside declaration',result)
        context={'other':'OTHER-MIB DEFINITIONS ::= BEGIN IMPORTS sensors FROM SNMPSTUDIO-DEMO-MIB; other OBJECT IDENTIFIER ::= { sensors 9 } END'}
        with self.assertRaises(ValueError):BuilderDocument(source,context).change(fields,'sensors')
        with self.assertRaises(ValueError):BuilderDocument(source,context).delete('sensors')

    def test_smi_v1_access_and_mandatory_status_are_preserved(self):
        source='LEGACY-MIB DEFINITIONS ::= BEGIN\nIMPORTS OBJECT-TYPE FROM RFC-1212 enterprises FROM RFC1155-SMI;\nlegacy OBJECT IDENTIFIER ::= { enterprises 59999 }\nreading OBJECT-TYPE SYNTAX INTEGER ACCESS read-only STATUS mandatory DESCRIPTION "Old" ::= { legacy 1 }\nEND'
        doc=BuilderDocument(source);fields=self.fields(doc,'reading');fields['description']='New'
        result=doc.change(fields,'reading');self.assertEqual(result,source.replace('DESCRIPTION "Old"','DESCRIPTION "New"'))
