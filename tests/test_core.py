# SPDX-License-Identifier: Apache-2.0
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest
from snmpstudio.models import Configuration,Device,Parameter,oid_tuple
from snmpstudio.storage import Store,atomic_write
from snmpstudio.mib import MibIndex,parse_source
from snmpstudio.ber import inspect_packet,safe_hex,flatten,decode_oid

SOURCE=Path(__file__).resolve().parents[1]/'snmpstudio/data/SNMPSTUDIO-DEMO-MIB.mib'


class ConfigTests(unittest.TestCase):
    def test_editor_opt_in_persists(self):
        store=Store(':memory:');config=Configuration();self.assertFalse(config.editor_enabled)
        config.editor_enabled=True;store.save(config);self.assertTrue(store.load().editor_enabled);store.close()
    def test_oid_validation(self):
        for oid in ['1.40.1','3.1','1.3.-1','1.3.x','1.3.4294967296','1']:
            with self.subTest(oid=oid),self.assertRaises(ValueError):oid_tuple(oid)
        self.assertEqual(oid_tuple('.1.3.6.1'),(1,3,6,1))
    def test_counter64_not_float(self):
        self.assertEqual(Parameter().formatted(18446744073709551615),'18446744073709551615')
    def test_scale_decimal(self):
        self.assertEqual(Parameter(factor='0.1',offset='1',decimals=1).formatted(253),'26.3')
    def test_config_roundtrip(self):
        device=Device();config=Configuration(devices=[device],parameters=[Parameter(device=device.id)])
        restored=Configuration.from_dict(json.loads(json.dumps(config.to_dict())))
        self.assertEqual(restored,config)
    def test_bad_device_reference(self):
        with self.assertRaises(ValueError):Configuration(parameters=[Parameter(device='missing')]).validate()
    def test_nonfinite_scale(self):
        for value in ['NaN','Infinity','bad']:
            with self.subTest(value=value),self.assertRaises(ValueError):Parameter(factor=value).validate()
    def test_atomic_save_conflict(self):
        with TemporaryDirectory() as tmp:
            file=Path(tmp)/'test.mib';atomic_write(file,'first');original=file.read_bytes();file.write_text('external')
            with self.assertRaises(RuntimeError):atomic_write(file,'editor',original)
            self.assertEqual(file.read_text(),'external')
    def test_import_rejects_future_schema(self):
        data=Configuration().to_dict();data['schema']=99
        with self.assertRaises(ValueError):Configuration.from_dict(data)
    def test_export_has_no_credentials(self):
        with TemporaryDirectory() as tmp:
            store=Store(':memory:');file=Path(tmp)/'config.json';store.export(Configuration(devices=[Device()]),file)
            self.assertNotIn('community',file.read_text());self.assertNotIn('password',file.read_text());store.close()

    def test_saved_commands_validate_legacy_and_new_records(self):
        from copy import deepcopy
        row={'oid':'1.3.6.1.4.1.55555.1.2.0','type':'Integer32','value':'230'}
        legacy={'name':'Legacy','device':None,'rows':[row]}
        Configuration(commands=[legacy]).validate()
        current={**legacy,'id':'saved-id','verify':True}
        config=Configuration(commands=[current]);restored=Configuration.from_dict(config.to_dict());self.assertEqual(restored.commands,[current])
        invalid=[{**current,'name':''},{**current,'rows':[]},{**current,'verify':'yes'},{**current,'id':''},{**current,'id':None},{**current,'rows':[{**row,'oid':'bad'}]},{**current,'rows':[{**row,'value':230}]}]
        for record in invalid:
            with self.subTest(record=record),self.assertRaises(ValueError):Configuration(commands=[record]).validate()
        with self.assertRaises(ValueError):Configuration(commands=[current,deepcopy(current)]).validate()


class MibTests(unittest.TestCase):
    def setUp(self):self.text=SOURCE.read_text();self.index=MibIndex().rebuild({'demo':self.text})
    def test_resolve(self):self.assertEqual(self.index.resolve('SNMPSTUDIO-DEMO-MIB::temperature.0'),'1.3.6.1.4.1.55555.1.1.0')
    def test_enumeration_and_units(self):
        self.assertEqual(self.index.describe('1.3.6.1.4.1.55555.1.3.0').enums[2],'running')
        self.assertIn('Celsius',self.index.describe('1.3.6.1.4.1.55555.1.1.0').units)
    def test_scalar_default_and_table_column_instance(self):
        table="""TABLE-MIB DEFINITIONS ::= BEGIN
IMPORTS OBJECT-TYPE, Integer32, enterprises FROM SNMPv2-SMI;
tables OBJECT IDENTIFIER ::= { enterprises 56666 }
sensorTable OBJECT-TYPE
    SYNTAX SEQUENCE OF SensorEntry
    MAX-ACCESS not-accessible
    STATUS current
    DESCRIPTION "Sensors"
    ::= { tables 1 }
sensorEntry OBJECT-TYPE
    SYNTAX SensorEntry
    MAX-ACCESS not-accessible
    STATUS current
    DESCRIPTION "Row"
    INDEX { sensorIndex }
    ::= { sensorTable 1 }
SensorEntry ::= SEQUENCE { sensorIndex Integer32, reading Integer32 }
sensorIndex OBJECT-TYPE
    SYNTAX Integer32
    MAX-ACCESS read-only
    STATUS current
    DESCRIPTION "Index"
    ::= { sensorEntry 1 }
reading OBJECT-TYPE
    SYNTAX Integer32
    MAX-ACCESS read-only
    STATUS current
    DESCRIPTION "Value"
    ::= { sensorEntry 2 }
END
"""
        self.index.rebuild({'scalar':self.text,'table':table})
        self.assertFalse(any(self.index.diagnostics.values()))
        scalar=next(x for x in self.index.objects if x.name=='temperature')
        column=next(x for x in self.index.objects if x.name=='reading')
        self.assertEqual(self.index.instance_oid(scalar),scalar.oid+'.0')
        self.assertEqual(self.index.instance_oid(column),column.oid)
        branch=next(x for x in self.index.objects if x.name=='sensorTable')
        self.assertEqual(self.index.instance_oid(branch),branch.oid)

    def test_syntax_error_line(self):
        _,_,errors=parse_source(self.text.replace('MAX-ACCESS read-only','MAX-ACCESS @'))
        self.assertTrue(errors);self.assertEqual(errors[0].severity,'error');self.assertGreater(errors[0].line,1)
    def test_missing_dependency(self):
        text='OTHER-MIB DEFINITIONS ::= BEGIN\nIMPORTS absent FROM MISSING-MIB;\nx OBJECT IDENTIFIER ::= { absent 1 }\nEND'
        index=MibIndex().rebuild({'missing':text});self.assertTrue(any('Missing import' in d.message for d in index.diagnostics['missing']))
    def test_cross_module_oid(self):
        text='OTHER-MIB DEFINITIONS ::= BEGIN\nIMPORTS sensors FROM SNMPSTUDIO-DEMO-MIB;\nother OBJECT IDENTIFIER ::= { sensors 25 }\nEND'
        index=MibIndex().rebuild({'a':self.text,'b':text});self.assertEqual(index.resolve('OTHER-MIB::other'),'1.3.6.1.4.1.55555.1.25')
    def test_duplicate_module(self):
        index=MibIndex().rebuild({'a':self.text,'b':self.text});self.assertTrue(any('Duplicate module' in d.message for d in index.diagnostics['b']))
    def test_source_change_updates_oid(self):
        self.index.rebuild({'demo':self.text.replace('{ sensors 1 }','{ sensors 10 }')});self.assertTrue(self.index.resolve('temperature.0').endswith('.10.0'))


class BerTests(unittest.TestCase):
    def message(self):
        from pyasn1.codec.ber import encoder
        from pysnmp.proto.api import v2c
        pdu=v2c.GetRequestPDU();v2c.apiPDU.set_defaults(pdu);v2c.apiPDU.set_varbinds(pdu,[('1.3.6.1.2.1.1.1.0',v2c.Null(''))])
        msg=v2c.Message();v2c.apiMessage.set_defaults(msg);v2c.apiMessage.set_community(msg,'secret-community');v2c.apiMessage.set_pdu(msg,pdu);return encoder.encode(msg)
    def test_actual_offsets_and_labels(self):
        raw=self.message();root=inspect_packet(raw);self.assertEqual(root.end,len(raw));self.assertTrue(any(n.label=='Request ID' for n in flatten(root)));self.assertTrue(any(n.value=='1.3.6.1.2.1.1.1.0' for n in flatten(root)))
    def test_masking_preserves_length(self):
        raw=self.message();masked=safe_hex(raw).split();self.assertEqual(len(masked),len(raw));self.assertIn('••',masked);self.assertNotIn('••',safe_hex(raw,True))
    def test_truncated_packet(self):
        with self.assertRaises(ValueError):inspect_packet(self.message()[:-1])
    def test_multibyte_length(self):
        raw=b'\x30\x81\x83\x04\x81\x80'+b'x'*128;root=inspect_packet(raw);self.assertEqual(root.children[0].end,len(raw))
    def test_multibyte_first_oid_arc(self):self.assertEqual(decode_oid(bytes([0x88,0x37,3])),'2.999.3')
    def test_bad_datagram_does_not_export_raw(self):self.assertIn('hidden',safe_hex(b'garbage-secret'))
