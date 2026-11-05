# SPDX-License-Identifier: Apache-2.0
from pathlib import Path
import unittest
from pysmi.parser import SmiStarParser
from snmpstudio.mib_format import format_mib,MibFormatError

DEMO=(Path(__file__).resolve().parents[1]/'snmpstudio/data/SNMPSTUDIO-DEMO-MIB.mib').read_text()

class FormatTests(unittest.TestCase):
    def test_oid_hierarchy_uses_four_spaces_and_is_idempotent(self):
        result=format_mib(DEMO)
        self.assertIn('\nstudio MODULE-IDENTITY',result)
        self.assertIn('\n    sensors OBJECT IDENTIFIER',result)
        self.assertIn('\n        temperature OBJECT-TYPE',result)
        self.assertIn('\n            SYNTAX Integer32',result)
        self.assertEqual(format_mib(result),result)
    def test_comments_and_quoted_multiline_text_are_preserved(self):
        source='-- Copyright header\n-- License line\n'+DEMO.replace('sensors OBJECT','-- Children comment\nsensors OBJECT').replace('::= { studio 1 }','::= { studio 1 } -- inline branch comment')
        value='"First line -- not a comment\\nwith \\t text"'.replace('\\n','\n')
        source=source.replace('"Temperature in tenths of a degree Celsius."',value).replace('\nEND\n','\nEND -- module end\n')
        result=format_mib(source)
        self.assertIn('-- Copyright header\n-- License line',result)
        self.assertIn('    -- Children comment\n    sensors',result)
        self.assertIn('} -- inline branch comment',result);self.assertIn(value,result);self.assertIn('END -- module end',result)
        self.assertEqual(SmiStarParser().parse(source),SmiStarParser().parse(result))
        self.assertEqual(format_mib(result),result)
    def test_single_line_numeric_and_forward_oid_references(self):
        source='NUMERIC-MIB DEFINITIONS ::= BEGIN leaf OBJECT IDENTIFIER ::= { branch 1 } root OBJECT IDENTIFIER ::= { 1 3 6 1 4 1 59999 } branch OBJECT IDENTIFIER ::= { root 1 } END'
        result=format_mib(source)
        self.assertIn('\n        leaf OBJECT IDENTIFIER',result);self.assertIn('\nroot OBJECT IDENTIFIER',result);self.assertIn('\n    branch OBJECT IDENTIFIER',result)
    def test_tables_types_enums_defaults_and_notifications_keep_ast(self):
        source='''TABLE-MIB DEFINITIONS ::= BEGIN
IMPORTS OBJECT-TYPE, Integer32, NOTIFICATION-TYPE, enterprises FROM SNMPv2-SMI;
root OBJECT IDENTIFIER ::= { enterprises 59999 }
sensorTable OBJECT-TYPE SYNTAX SEQUENCE OF SensorEntry MAX-ACCESS not-accessible STATUS current DESCRIPTION "Rows" ::= { root 1 }
sensorEntry OBJECT-TYPE SYNTAX SensorEntry MAX-ACCESS not-accessible STATUS current DESCRIPTION "Row" INDEX { sensorIndex } ::= { sensorTable 1 }
SensorEntry ::= SEQUENCE { sensorIndex Integer32, reading INTEGER }
sensorIndex OBJECT-TYPE SYNTAX Integer32 MAX-ACCESS read-only STATUS current DESCRIPTION "Index" ::= { sensorEntry 1 }
reading OBJECT-TYPE SYNTAX INTEGER { down(0), up(1) } MAX-ACCESS read-write STATUS current DESCRIPTION "Value" DEFVAL { down } ::= { sensorEntry 2 }
alarm NOTIFICATION-TYPE OBJECTS { reading } STATUS current DESCRIPTION "Event" ::= { root 0 1 }
END'''
        result=format_mib(source);self.assertEqual(SmiStarParser().parse(source),SmiStarParser().parse(result));self.assertIn('\n            sensorIndex OBJECT-TYPE',result)
        self.assertIn('SensorEntry ::= SEQUENCE {\n    sensorIndex Integer32,\n    reading INTEGER\n}',result)
        self.assertEqual(format_mib(result),result)
    def test_smi_v1_and_multiple_modules_are_not_converted(self):
        legacy='LEGACY-MIB DEFINITIONS ::= BEGIN IMPORTS OBJECT-TYPE FROM RFC-1212 enterprises FROM RFC1155-SMI TRAP-TYPE FROM RFC-1215; root OBJECT IDENTIFIER ::= { enterprises 59999 } value OBJECT-TYPE SYNTAX INTEGER ACCESS read-only STATUS mandatory DESCRIPTION "Value" ::= { root 1 } alarm TRAP-TYPE ENTERPRISE root VARIABLES { value } DESCRIPTION "Event" ::= 7 END'
        other='OTHER-MIB DEFINITIONS ::= BEGIN IMPORTS root FROM LEGACY-MIB; branch OBJECT IDENTIFIER ::= { root 2 } END'
        source=legacy+'\n\n'+other;result=format_mib(source)
        self.assertIn('ACCESS read-only',result);self.assertIn('STATUS mandatory',result);self.assertIn('alarm TRAP-TYPE',result)
        self.assertEqual(SmiStarParser().parse(source),SmiStarParser().parse(result));self.assertEqual(format_mib(result),result)
    def test_invalid_input_does_not_return_modified_output(self):
        with self.assertRaises(MibFormatError):format_mib(DEMO.replace('MAX-ACCESS','MAX-ACCESS @'))

    def test_legacy_macro_and_exports_are_kept_verbatim(self):
        macro='''OBJECT-TYPE MACRO ::= BEGIN
    TYPE NOTATION ::= "SYNTAX" value(SYNTAX)
    -- keep this macro comment
    VALUE NOTATION ::= value(VALUE INTEGER)
END'''
        source='LEGACY-MIB DEFINITIONS ::= BEGIN\nEXPORTS root;\n'+macro+'\nroot OBJECT IDENTIFIER ::= { 1 3 6 1 4 1 59999 }\nEND\n'
        result=format_mib(source);self.assertIn(macro,result);self.assertIn('EXPORTS root;',result)
        self.assertEqual(format_mib(result),result)
    def test_layout_tabs_become_four_spaces_without_changing_literal_tabs(self):
        source=DEMO.replace('    ','\t').replace('"Temperature in tenths of a degree Celsius."','"Literal\ttab"')
        result=format_mib(source)
        self.assertIn('\n    sensors OBJECT IDENTIFIER',result);self.assertIn('"Literal\ttab"',result)
        self.assertNotIn('\n\t',result)
