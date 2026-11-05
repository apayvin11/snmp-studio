# SPDX-License-Identifier: Apache-2.0
import asyncio
from pathlib import Path
import unittest
from snmpstudio.mib import MibIndex
from snmpstudio.protocol import Value,RequestError
from snmpstudio.notifications import decode_value,notification_identity
from snmpstudio.simulator import DemoAgent,DEMO_MIB,DEMO_NOTIFICATION_OID,MODE_OID

class DecodeTests(unittest.TestCase):
    def test_names_enums_units_and_notification_identity(self):
        index=MibIndex().rebuild({'demo':DEMO_MIB.read_text()})
        self.assertEqual(decode_value(index,Value(MODE_OID,'Integer',2),'Unknown')[:2],('operatingMode','running (2)'))
        self.assertIn('degrees Celsius / 10',decode_value(index,Value('1.3.6.1.4.1.55555.1.1.0','Integer',253),'Unknown')[1])
        data={'values':[Value('1.3.6.1.6.3.1.1.4.1.0','ObjectIdentifier',DEMO_NOTIFICATION_OID)]}
        self.assertEqual(notification_identity(index,data,'Unknown')[:2],(DEMO_NOTIFICATION_OID,'demoStatusChanged'))
        self.assertIn('demoStatusChanged',decode_value(index,data['values'][0],'Unknown')[1])
    def test_unknown_oids_and_bits_preserve_values(self):
        index=MibIndex().rebuild({'flags':'FLAGS-MIB DEFINITIONS ::= BEGIN IMPORTS OBJECT-TYPE,enterprises FROM SNMPv2-SMI; flags OBJECT-TYPE SYNTAX BITS { active(0), fault(9) } MAX-ACCESS read-only STATUS current DESCRIPTION "Flags" ::= { enterprises 59999 } END'})
        self.assertEqual(decode_value(index,Value('1.3.6.1.4.1.59999.0','OctetString',b'\x80\x40'),'Unknown')[:2],('flags','active, fault'))
        self.assertEqual(decode_value(index,Value('1.3.6.1.4.1.59998.0','Integer',17),'Unknown')[:2],('Unknown','17'))

    def test_legacy_trap_type_resolves_normalized_v1_oid(self):
        source='LEGACY-MIB DEFINITIONS ::= BEGIN IMPORTS enterprises FROM RFC1155-SMI TRAP-TYPE FROM RFC-1215; legacy OBJECT IDENTIFIER ::= { enterprises 59999 } alarm TRAP-TYPE ENTERPRISE legacy DESCRIPTION "Alarm" ::= 7 END'
        index=MibIndex().rebuild({'legacy':source})
        data={'values':[Value('1.3.6.1.6.3.1.1.4.1.0','ObjectIdentifier','1.3.6.1.4.1.59999.0.7')]}
        self.assertEqual(notification_identity(index,data,'Unknown')[1:3],('alarm','Alarm'))

class TextualConventionTests(unittest.TestCase):
    def test_imported_enumeration_and_decimal_display_hint(self):
        types='TYPES-MIB DEFINITIONS ::= BEGIN IMPORTS TEXTUAL-CONVENTION FROM SNMPv2-TC; MyStatus ::= TEXTUAL-CONVENTION STATUS current DESCRIPTION "Status" SYNTAX INTEGER { down(1), up(2) } Temperature ::= TEXTUAL-CONVENTION DISPLAY-HINT "d-1" STATUS current DESCRIPTION "Tenths" SYNTAX INTEGER END'
        device='DEVICE-MIB DEFINITIONS ::= BEGIN IMPORTS OBJECT-TYPE,enterprises FROM SNMPv2-SMI MyStatus,Temperature FROM TYPES-MIB; state OBJECT-TYPE SYNTAX MyStatus MAX-ACCESS read-only STATUS current DESCRIPTION "State" ::= { enterprises 59990 } temperature OBJECT-TYPE SYNTAX Temperature UNITS "C" MAX-ACCESS read-only STATUS current DESCRIPTION "Temperature" ::= { enterprises 59991 } END'
        index=MibIndex().rebuild({'types':types,'device':device})
        self.assertEqual(decode_value(index,Value('1.3.6.1.4.1.59990.0','Integer',2),'Unknown')[1],'up (2)')
        self.assertEqual(decode_value(index,Value('1.3.6.1.4.1.59991.0','Integer',253),'Unknown')[1],'25.3 C')
        index.rebuild({});self.assertEqual(decode_value(index,Value('1.3.6.1.4.1.59990.0','Integer',2),'Unknown')[0],'Unknown')

class DemoNotificationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.sender=DemoAgent();self.receiver=DemoAgent();self.events=[];self.packets=[]
        self.receiver.notification_callback=self.events.append;self.receiver.packet_callback=self.packets.append
    async def asyncTearDown(self):self.sender.close();self.receiver.close()
    async def test_trap_and_inform_real_udp_and_receipt_callbacks(self):
        for kind in ('TRAP','INFORM'):
            result=await self.sender.send_notification('127.0.0.1',self.receiver.port,kind)
            deadline=asyncio.get_running_loop().time()+2
            while len(self.events)<(1 if kind=='TRAP' else 2) and asyncio.get_running_loop().time()<deadline:await asyncio.sleep(.01)
            self.assertEqual(result['acknowledged'],kind=='INFORM')
        self.assertEqual([e['type'] for e in self.events],['TRAP','INFORM'])
        self.assertTrue(all(any(v.oid==MODE_OID for v in e['values']) for e in self.events))
        self.assertTrue(any(p.direction=='TX' and p.operation=='INFORM' for p in self.packets))
    async def test_v1_trap_is_normalized_for_mib_decoding(self):
        from pysnmp.hlapi.v3arch import asyncio as h
        engine=h.SnmpEngine()
        try:
            await h.send_notification(engine,h.CommunityData('public',mpModel=0),await h.UdpTransportTarget.create(('127.0.0.1',self.receiver.port),timeout=.2,retries=0),h.ContextData(),'trap',h.NotificationType(h.ObjectIdentity(DEMO_NOTIFICATION_OID)))
            deadline=asyncio.get_running_loop().time()+2
            while not self.events and asyncio.get_running_loop().time()<deadline:await asyncio.sleep(.01)
            self.assertTrue(self.events);self.assertEqual(self.events[0]['type'],'TRAP')
            self.assertTrue(any(v.oid=='1.3.6.1.6.3.1.1.4.1.0' and v.value==DEMO_NOTIFICATION_OID for v in self.events[0]['values']))
        finally:engine.close_dispatcher()
    async def test_inform_wrong_community_times_out_without_retries(self):
        self.sender.read_community='wrong';packets=[]
        with self.assertRaises(RequestError):await self.sender.send_notification('127.0.0.1',self.receiver.port,'INFORM',packets.append,timeout=.2)
        self.assertFalse(self.events);self.assertEqual(len([p for p in packets if p.direction=='TX']),1)
