# SPDX-License-Identifier: Apache-2.0
from pathlib import Path
import unittest
from snmpstudio.discovery import DeviceObservations
from snmpstudio.mib import MibIndex,MibObject
from snmpstudio.protocol import Value

ROOT='1.3.6.1.4.1.55555'

class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        text=(Path(__file__).resolve().parents[1]/'snmpstudio/data/SNMPSTUDIO-DEMO-MIB.mib').read_text()
        self.index=MibIndex().rebuild({'demo':text});self.state=DeviceObservations()
        self.temperature=next(obj for obj in self.index.objects if obj.name=='temperature')
        self.setpoint=next(obj for obj in self.index.objects if obj.name=='setPoint')
    def test_completed_walk_marks_missing_and_reappearing_oids(self):
        value=Value(ROOT+'.1.1.0','Integer',253);extra=Value(ROOT+'.9.0','Integer',99)
        self.state.record('WALK',ROOT,[value,extra])
        self.assertEqual(self.state.object_status(self.temperature,self.index),'present')
        self.assertEqual(self.state.object_status(self.setpoint,self.index),'missing')
        self.state.record('WALK',ROOT,[])
        self.assertIn(extra.oid,self.state.missing)
        self.assertEqual(self.state.object_status(self.temperature,self.index),'missing')
        self.state.record('GET',value.oid,[value])
        self.assertEqual(self.state.object_status(self.temperature,self.index),'present')
    def test_narrow_walk_does_not_mark_other_branches_missing(self):
        temperature=Value(ROOT+'.1.1.0','Integer',253);setpoint=Value(ROOT+'.1.2.0','Integer',220)
        self.state.record('WALK_ALL','0.0',[temperature,setpoint])
        self.state.record('WALK',ROOT+'.1.1',[])
        self.assertEqual(self.state.object_status(self.temperature,self.index),'missing')
        self.assertEqual(self.state.object_status(self.setpoint,self.index),'present')
    def test_getnext_does_not_imply_absence_of_other_objects(self):
        self.state.record('GETNEXT',ROOT,[Value(ROOT+'.1.2.0','Integer',220)])
        self.assertEqual(self.state.object_status(self.temperature,self.index),'')
        self.assertEqual(self.state.object_status(self.setpoint,self.index),'present')
    def test_no_such_instance_marks_only_that_oid(self):
        oid=ROOT+'.1.1.0';self.state.record('GET',oid,[Value(oid,'NoSuchInstance','','NoSuchInstance')])
        self.assertEqual(self.state.object_status(self.temperature,self.index),'missing')
        self.assertEqual(self.state.object_status(self.setpoint,self.index),'')

    def test_unresolved_mib_object_is_not_reported_missing(self):
        self.state.record('WALK',ROOT,[])
        unresolved=MibObject('PARTIAL-MIB','unresolved','OBJECT-TYPE',access='read-only')
        self.assertEqual(self.index.instance_oid(unresolved),'')
        self.assertEqual(self.state.object_status(unresolved,self.index),'')
