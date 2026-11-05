# SPDX-License-Identifier: Apache-2.0
import asyncio
import unittest
from pysnmp.hlapi.v3arch import asyncio as h
from pysnmp.entity import config
from pysnmp.entity.rfc3413 import context,cmdrsp
from pysnmp.carrier.asyncio.dgram import udp
from snmpstudio.models import Device
from snmpstudio.protocol import Session,typed_value
from snmpstudio.worker import Worker


class V3Tests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine=h.SnmpEngine()
        transport=udp.UdpAsyncioTransport().open_server_mode(('127.0.0.1',0));config.add_transport(self.engine,udp.DOMAIN_NAME,transport);await transport._lport
        self.port=transport.transport.get_extra_info('sockname')[1]
        config.add_v3_user(self.engine,'studio',h.usmHMAC192SHA256AuthProtocol,'auth-secret-123',h.usmAesCfb128Protocol,'priv-secret-123')
        config.add_vacm_user(self.engine,3,'studio','authPriv',(1,3,6),(1,3,6))
        ctx=context.SnmpContext(self.engine);builder=ctx.get_mib_instrum().get_mib_builder()
        scalar,instance=builder.import_symbols('SNMPv2-SMI','MibScalar','MibScalarInstance')
        self.oid=(1,3,6,1,4,1,55555,9)
        builder.export_symbols('STUDIO-TEST',scalar(self.oid,h.Integer32()).setMaxAccess('read-write'),instance(self.oid,(0,),h.Integer32(42)))
        self.responders=[cmdrsp.GetCommandResponder(self.engine,ctx),cmdrsp.SetCommandResponder(self.engine,ctx),cmdrsp.NextCommandResponder(self.engine,ctx),cmdrsp.BulkCommandResponder(self.engine,ctx)]
        self.packets=[];self.session=Session(Device(port=self.port,version='3',username='studio',timeout=.5,retries=0),{'auth_password':'auth-secret-123','priv_password':'priv-secret-123'},self.packets.append)
    async def asyncTearDown(self):self.session.close();self.engine.close_dispatcher()
    async def test_authpriv_get_and_encrypted_wire(self):
        values=await self.session.get(['.'.join(map(str,self.oid))+'.0']);self.assertEqual(values[0].value,42)
        from snmpstudio.ber import inspect_packet,flatten
        self.assertTrue(any(n.label=='Encrypted scoped PDU' for p in self.packets for n in flatten(inspect_packet(p.raw))))
        self.assertTrue(any(p.plaintext and '42' in p.decoded for p in self.packets if p.direction=='RX'))
    async def test_authpriv_set(self):
        oid='.'.join(map(str,self.oid))+'.0'
        await self.session.request('SET',[oid],[typed_value('Integer32','55')]);self.assertEqual((await self.session.get([oid]))[0].value,55)


class NotificationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.worker=Worker();self.notifications=[];self.worker.notification.connect(self.notifications.append)
        # Reserve an ephemeral port; listener itself uses the production code path.
        import socket
        sock=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);sock.bind(('127.0.0.1',0));self.port=sock.getsockname()[1];sock.close()
        await self.worker.listen('127.0.0.1',self.port,[(Device(),{'community':'public'})])
        self.sender=h.SnmpEngine()
    async def asyncTearDown(self):
        self.sender.close_dispatcher()
        if self.worker.listener:self.worker.listener.close()
    async def test_inform_acknowledged(self):
        result=await h.send_notification(self.sender,h.CommunityData('public'),await h.UdpTransportTarget.create(('127.0.0.1',self.port),timeout=.5,retries=0),h.ContextData(),'inform',h.NotificationType(h.ObjectIdentity('1.3.6.1.6.3.1.1.5.1')))
        self.assertFalse(result[0]);self.assertFalse(result[1]);self.assertEqual(len(self.notifications),1);self.assertEqual(self.notifications[0]['type'],'INFORM')

    async def test_trap_packet_types_for_v1_and_v2c(self):
        for version in (0,1):
            self.notifications.clear()
            await h.send_notification(self.sender,h.CommunityData('public',mpModel=version),await h.UdpTransportTarget.create(('127.0.0.1',self.port),timeout=.5,retries=0),h.ContextData(),'trap',h.NotificationType(h.ObjectIdentity('1.3.6.1.6.3.1.1.5.1')))
            deadline=asyncio.get_running_loop().time()+2
            while not self.notifications and asyncio.get_running_loop().time()<deadline:await asyncio.sleep(.01)
            self.assertEqual(len(self.notifications),1)
            self.assertEqual(self.notifications[0]['type'],'TRAP')
