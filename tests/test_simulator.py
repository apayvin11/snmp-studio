# SPDX-License-Identifier: Apache-2.0
"""Agent contract tests: independent UDP manager, MIB metadata, and CLI process."""
import asyncio
from pathlib import Path
import re
import socket
import sys
import unittest

from pysnmp.hlapi.v3arch import asyncio as h
from snmpstudio.mib import MibIndex
from snmpstudio.models import Device
from snmpstudio.protocol import Session, RequestError, typed_value
from snmpstudio.simulator import (DemoAgent, DEMO_MIB, DEMO_ROOT, TEMPERATURE_OID,
                                  SET_POINT_OID, MODE_OID, UPTIME_OID)


class DemoAgentTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.clock_value = 0.0
        self.agent = DemoAgent(simulate=True, clock=lambda: self.clock_value)
        self.session = Session(Device(port=self.agent.port, timeout=.3, retries=0),
                               {'community': 'public', 'write_community': 'private'})

    async def asyncTearDown(self):
        self.session.close()
        self.agent.close()

    async def write(self, oid, value, kind='Integer32'):
        return await self.session.request('SET', [oid], [typed_value(kind, str(value))])

    async def test_demo_mib_objects_are_all_available(self):
        index = MibIndex().rebuild({'demo': DEMO_MIB.read_text()})
        objects = [o for o in index.objects if o.kind == 'OBJECT-TYPE']
        self.assertEqual({o.name for o in objects}, {'temperature', 'setPoint', 'operatingMode'})
        values = await self.session.get([o.oid+'.0' for o in objects])
        self.assertTrue(all(not v.error and isinstance(v.value, int) for v in values))
        self.assertEqual(next(o for o in objects if o.name == 'temperature').access, 'read-only')

    async def test_public_cannot_write(self):
        self.session.secrets['write_community'] = 'public'
        with self.assertRaises(RequestError) as result:
            await self.write(SET_POINT_OID, 270)
        self.assertEqual(result.exception.status, 16)
        self.assertEqual((await self.session.get([SET_POINT_OID]))[0].value, 220)

    async def test_private_can_read_and_write(self):
        self.session.secrets['community'] = 'private'
        await self.write(SET_POINT_OID, 300)
        self.assertEqual((await self.session.get([SET_POINT_OID]))[0].value, 300)

    async def test_write_range_and_enumeration(self):
        for oid, invalid in [(SET_POINT_OID, -1), (SET_POINT_OID, 1001), (MODE_OID, 0), (MODE_OID, 4)]:
            with self.subTest(oid=oid, value=invalid), self.assertRaises(RequestError) as result:
                await self.write(oid, invalid)
            self.assertEqual(result.exception.status, 10)
            self.assertEqual(result.exception.index, 1)
        for oid, valid in [(SET_POINT_OID, 0), (SET_POINT_OID, 1000), (MODE_OID, 1), (MODE_OID, 2), (MODE_OID, 3)]:
            await self.write(oid, valid)
            self.assertEqual((await self.session.get([oid]))[0].value, valid)

    async def test_wrong_wire_type(self):
        with self.assertRaises(RequestError) as result:
            await self.write(SET_POINT_OID, 270, 'Gauge32')
        self.assertEqual(result.exception.status, 7)

    async def test_invalid_multi_set_is_atomic_and_identifies_binding(self):
        with self.assertRaises(RequestError) as result:
            await self.session.request('SET', [SET_POINT_OID, MODE_OID],
                                       [typed_value('Integer32', '300'), typed_value('Integer32', '9')])
        self.assertEqual((result.exception.status, result.exception.index), (10, 2))
        values = await self.session.get([SET_POINT_OID, MODE_OID])
        self.assertEqual([v.value for v in values], [220, 2])

    async def test_temperature_follows_setpoint_and_modes(self):
        await self.write(SET_POINT_OID, 300)
        self.clock_value = 10
        heating = (await self.session.get([TEMPERATURE_OID]))[0].value
        self.assertGreater(heating, 253)
        self.assertLess(heating, 300)
        await self.write(MODE_OID, 1)
        self.clock_value = 20
        standby = (await self.session.get([TEMPERATURE_OID]))[0].value
        self.assertLess(standby, heating)
        await self.write(MODE_OID, 3)
        self.clock_value = 100
        self.assertEqual((await self.session.get([TEMPERATURE_OID]))[0].value, standby)
        self.assertEqual((await self.session.get([UPTIME_OID]))[0].value, 10000)

    async def test_getbulk_returns_multiple_successive_values(self):
        rows = await self.session.request('GETBULK', [DEMO_ROOT])
        self.assertEqual([r.oid for r in rows if not r.error], [TEMPERATURE_OID, SET_POINT_OID, MODE_OID])
        self.assertEqual(rows[-1].error, 'EndOfMibView')
        self.assertEqual(len(self.agent.requests), 1)

    async def test_getbulk_non_repeaters_and_two_columns(self):
        target = await h.UdpTransportTarget.create(('127.0.0.1', self.agent.port), timeout=.3, retries=0)
        result = await h.bulk_cmd(self.session.engine, h.CommunityData('public'), target, h.ContextData(),
                                 1, 2, h.ObjectType(h.ObjectIdentity('1.3.6.1.2.1.1.1.0')),
                                 h.ObjectType(h.ObjectIdentity(DEMO_ROOT)), lookupMib=False)
        self.assertFalse(result[0]); self.assertFalse(result[1])
        self.assertEqual([str(oid) for oid, _ in result[3]], [UPTIME_OID, TEMPERATURE_OID, SET_POINT_OID])

    async def test_v1_walk_and_invalid_value(self):
        self.session.device.version = '1'
        rows = await self.session.walk(DEMO_ROOT)
        self.assertEqual([r.oid for r in rows], [TEMPERATURE_OID, SET_POINT_OID, MODE_OID])
        with self.assertRaises(RequestError) as result:
            await self.write(MODE_OID, 7)
        self.assertEqual(result.exception.status, 3)

    async def test_missing_instance_and_missing_object(self):
        rows = await self.session.get([TEMPERATURE_OID[:-1]+'1', DEMO_ROOT+'.99.0'])
        self.assertEqual([r.error for r in rows], ['NoSuchInstance', 'NoSuchObject'])

    async def test_malformed_datagram_does_not_kill_agent(self):
        transport = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            transport.sendto(b'invalid ASN.1', ('127.0.0.1', self.agent.port))
        finally:
            transport.close()
        self.assertEqual((await self.session.get([SET_POINT_OID]))[0].value, 220)

    async def test_close_releases_port(self):
        port = self.agent.port
        self.agent.close()
        self.agent.close()
        with DemoAgent(port=port) as restarted:
            self.assertEqual(restarted.port, port)

    async def test_ipv6_loopback(self):
        try:
            agent = DemoAgent('::1')
        except OSError as exc:
            self.skipTest(f'IPv6 loopback unavailable: {exc}')
        session = Session(Device(host='::1', port=agent.port, timeout=.3, retries=0), {'community': 'public'})
        try:
            self.assertEqual((await session.get([SET_POINT_OID]))[0].value, 220)
        finally:
            session.close(); agent.close()


class DemoAgentCliTests(unittest.IsolatedAsyncioTestCase):
    async def test_standalone_agent_and_graceful_shutdown(self):
        process = await asyncio.create_subprocess_exec(sys.executable, '-m', 'snmpstudio.simulator', '--port', '0', '--static',
                                                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        session = None
        try:
            line = (await asyncio.wait_for(process.stdout.readline(), 5)).decode()
            match = re.search(r'udp://127\.0\.0\.1:(\d+)', line)
            self.assertIsNotNone(match, line)
            session = Session(Device(port=int(match[1]), timeout=.3, retries=0),
                              {'community': 'public', 'write_community': 'private'})
            self.assertEqual((await session.get([TEMPERATURE_OID]))[0].value, 253)
            await session.request('SET', [MODE_OID], [typed_value('Integer32', '3')])
            self.assertEqual((await session.get([MODE_OID]))[0].value, 3)
        finally:
            if session: session.close()
            if process.returncode is None: process.terminate()
            _, error = await asyncio.wait_for(process.communicate(), 5)
        self.assertEqual(process.returncode, 0, error.decode())
