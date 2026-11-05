# SPDX-License-Identifier: Apache-2.0
import asyncio
import unittest
from snmpstudio.models import Device
from snmpstudio.protocol import Session,RequestError,typed_value
from snmpstudio.simulator import DemoAgent

ROOT='1.3.6.1.4.1.55555'


class WireTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.agent=DemoAgent();self.packets=[];self.session=Session(Device(port=self.agent.port,timeout=.2,retries=0),{'community':'public','write_community':'private'},self.packets.append)
    async def asyncTearDown(self):self.session.close();self.agent.close()
    async def test_combined_get(self):
        values=await self.session.get([ROOT+'.1.1.0',ROOT+'.1.2.0']);self.assertEqual([v.value for v in values],[253,220]);self.assertEqual(len(self.agent.requests),1)
    async def test_set_and_read(self):
        await self.session.request('SET',[ROOT+'.1.2.0'],[typed_value('Integer32','270')]);self.assertEqual((await self.session.get([ROOT+'.1.2.0']))[0].value,270)
    async def test_read_only_error(self):
        with self.assertRaises(RequestError) as result:await self.session.request('SET',[ROOT+'.1.1.0'],[typed_value('Integer32','270')])
        self.assertEqual(result.exception.status,17)
    async def test_walk_stops_at_subtree_end(self):
        values=await self.session.walk(ROOT);self.assertEqual(len(values),3);self.assertTrue(all(v.oid.startswith(ROOT+'.') for v in values))
    async def test_unknown_oid_does_not_hide_other_values(self):
        values=await self.session.get([ROOT+'.1.1.0',ROOT+'.999.0']);self.assertEqual(values[0].value,253);self.assertTrue(values[1].error)
    async def test_v1_partial_error_recovery(self):
        self.session.device.version='1';values=await self.session.get([ROOT+'.1.1.0',ROOT+'.999.0']);self.assertEqual(next(v for v in values if not v.error).value,253)
    async def test_counter64(self):self.assertEqual((await self.session.get(['1.3.6.1.2.1.31.1.1.1.6.1']))[0].value,18446744073709551600)
    async def test_real_packet_capture(self):
        await self.session.get([ROOT+'.1.1.0']);tx=next(p for p in self.packets if p.direction=='TX');rx=next(p for p in self.packets if p.direction=='RX');self.assertEqual(tx.raw,self.agent.requests[0]);self.assertEqual(tx.transaction,rx.transaction);self.assertTrue(rx.decoded)
    async def test_set_timeout_never_retries(self):
        self.session.secrets['write_community']='wrong';self.session.device.retries=3
        with self.assertRaisesRegex(RequestError,'unknown'):await self.session.request('SET',[ROOT+'.1.2.0'],[typed_value('Integer32','270')])
        self.assertEqual(len({p.id for p in self.packets if p.direction=='TX'}),1)
    async def test_too_big_splits_group(self):
        self.agent.max_bindings=1
        values=await self.session.get([ROOT+'.1.1.0',ROOT+'.1.2.0']);self.assertEqual([v.value for v in values],[253,220]);self.assertEqual(len(self.agent.requests),3)
    async def test_multi_set_error_does_not_commit_partial_update(self):
        with self.assertRaises(RequestError):await self.session.request('SET',[ROOT+'.1.2.0',ROOT+'.1.1.0'],[typed_value('Integer32','299'),typed_value('Integer32','300')])
        self.assertEqual((await self.session.get([ROOT+'.1.2.0']))[0].value,220)

    async def test_explicit_getnext_and_bulk_are_single_requests(self):
        values=await self.session.request('GETNEXT',[ROOT+'.1.1.0'])
        self.assertEqual(values[0].oid,ROOT+'.1.2.0')
        self.assertEqual(len(self.agent.requests),1)
        values=await self.session.request('GETBULK',[ROOT],max_repetitions=2)
        self.assertEqual([v.oid for v in values],[ROOT+'.1.1.0',ROOT+'.1.2.0'])
        self.assertEqual(len(self.agent.requests),2)
        from pyasn1.codec.ber import decoder
        from pysnmp.proto.api import v2c
        message,_=decoder.decode(self.agent.requests[-1],asn1Spec=v2c.Message())
        pdu=v2c.apiMessage.get_pdu(message)
        self.assertEqual(int(v2c.apiBulkPDU.get_max_repetitions(pdu)),2)
        self.assertEqual(int(v2c.apiBulkPDU.get_non_repeaters(pdu)),0)

    async def test_bulk_rejected_for_v1_before_network_io(self):
        self.session.device.version='1'
        with self.assertRaisesRegex(RequestError,'SNMPv2c'):
            await self.session.request('GETBULK',[ROOT])
        self.assertFalse(self.agent.requests)

    async def test_bulk_size_is_bounded(self):
        for count in (0,101):
            with self.subTest(count=count),self.assertRaises(RequestError):
                await self.session.request('GETBULK',[ROOT],max_repetitions=count)
        self.assertFalse(self.agent.requests)

    async def test_full_device_walk_includes_oids_outside_loaded_subtree(self):
        for version,count in [('2c',7),('1',6)]:
            self.session.device.version=version
            values=await self.session.walk('0.0',subtree=False)
            self.assertEqual(len(values),count)
            self.assertTrue(any(v.oid.startswith('1.3.6.1.2.1.') for v in values))
            self.assertTrue(any(v.oid.startswith(ROOT+'.') for v in values))
            self.assertEqual([v.oid for v in values],sorted((v.oid for v in values),key=lambda x:tuple(map(int,x.split('.')))))

    async def test_walk_does_not_report_success_for_an_error_varbind(self):
        from unittest.mock import AsyncMock,patch
        from snmpstudio.protocol import Value
        for value in [Value(ROOT+'.1.1.0','NoSuchObject','','NoSuchObject'),Value('1.3.6.1.2.1.1.1.0','Integer',1)]:
            with patch.object(self.session,'request',new=AsyncMock(return_value=[value])):
                with self.assertRaises(RequestError):await self.session.walk(ROOT)


class SetValueTests(unittest.TestCase):
    def test_octet_string_preserves_utf8_text_and_hex_accepts_arbitrary_bytes(self):
        text='  Тестовый узел  '
        self.assertEqual(typed_value('OctetString',text).asOctets(),text.encode('utf-8'))
        self.assertEqual(typed_value('OctetString','').asOctets(),b'')
        self.assertEqual(typed_value('HexString','FF 00 80').asOctets(),b'\xff\x00\x80')
