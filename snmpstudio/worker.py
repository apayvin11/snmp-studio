# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations
import asyncio
from dataclasses import asdict
import json
import threading
from PySide6.QtCore import QThread, Signal
from .protocol import Session, typed_value, to_value


class Worker(QThread):
    result = Signal(str, object, str)
    packet = Signal(object)
    notification = Signal(object)

    def __init__(self):
        super().__init__()
        self.loop = None
        self.ready = threading.Event()
        self.sessions = {}
        self.jobs = {}
        self.listener = None

    def run(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.ready.set()
        try:
            self.loop.run_forever()
        finally:
            tasks = asyncio.all_tasks(self.loop)
            for task in tasks:
                task.cancel()
            self.loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))
            for session in self.sessions.values():
                session.close()
            if self.listener:
                self.listener.close()
            self.loop.run_until_complete(self.loop.shutdown_asyncgens())
            self.loop.close()

    def submit(self, job, coroutine):
        if not self.ready.wait(3) or not self.loop or not self.isRunning():
            coroutine.close()
            return
        future = asyncio.run_coroutine_threadsafe(coroutine, self.loop)
        self.jobs[job] = future
        def done(f):
            self.jobs.pop(job, None)
            try:
                self.result.emit(job, f.result(), '')
            except BaseException as exc:
                self.result.emit(job, None, str(exc) or 'Cancelled')
        future.add_done_callback(done)

    def cancel(self, job):
        if job in self.jobs:
            self.jobs[job].cancel()

    def session(self, device, secrets):
        key = json.dumps([asdict(device), secrets], sort_keys=True)
        if key not in self.sessions:
            self.sessions[key] = Session(device, secrets, self.packet.emit)
        return self.sessions[key]

    async def read(self, device, secrets, parameters):
        session = self.session(device, secrets)
        output = {}
        for start in range(0, len(parameters), device.batch):
            group = parameters[start:start+device.batch]
            try:
                async with asyncio.timeout(65*(device.retries+1)):
                    values = await session.get(list(dict.fromkeys(p.oid for p in group)))
                by_oid = {v.oid: v for v in values}
                for param in group:
                    output[param.id] = by_oid.get(param.oid)
            except Exception as exc:
                for param in group:
                    output[param.id] = str(exc)
        return output

    async def query(self, device, secrets, operation, oid, max_repetitions=20):
        if operation not in ('GET','GETNEXT','GETBULK','WALK','WALK_ALL'):
            raise ValueError('Unsupported read operation')
        session=self.session(device,secrets)
        if operation=='WALK':return await session.walk(oid)
        if operation=='WALK_ALL':return await session.walk('0.0',limit=50000,subtree=False,timeout=120)
        async with asyncio.timeout(65*(device.retries+1)):
            if operation=='GET':return await session.get([oid])
            return await session.request(operation,[oid],max_repetitions=max_repetitions)

    async def walk(self, device, secrets, root):
        return await self.session(device, secrets).walk(root)

    async def write(self, device, secrets, rows, verify=False):
        session = self.session(device, secrets)
        values = [typed_value(row['type'], row['value']) for row in rows]
        async with asyncio.timeout(device.timeout+15):
            result = await session.request('SET', [row['oid'] for row in rows], values)
        if verify:
            return {'response':result, 'verification':await session.get([row['oid'] for row in rows])}
        return {'response':result}

    async def demo_notification(self,agent,host,port,kind):
        if self.listener:
            from pysnmp.entity import config
            config.add_v1_system(self.listener.engine,'snmpstudio-demo-notifications',agent.read_community)
        return await agent.send_notification(host,port,kind,self.packet.emit)

    async def listen(self, host, port, profiles):
        from pysnmp.entity import config
        from pysnmp.entity.rfc3413 import ntfrcv
        from pysnmp.carrier.asyncio.dgram import udp, udp6
        if self.listener:
            self.listener.close()
            self.listener = None
        if not port:
            return 'Stopped'
        from .models import Device
        d = Device(id='listener', name='Notifications')
        listener = Session(d, {}, self.packet.emit)
        listener.operation='NOTIFICATION'
        self.listener = listener
        try:
            ipv6 = ':' in host
            base = udp6.Udp6AsyncioTransport if ipv6 else udp.UdpAsyncioTransport
            domain = udp6.DOMAIN_NAME if ipv6 else udp.DOMAIN_NAME
            transport = listener.transport_class(base)().open_server_mode((host, port))
            config.add_transport(listener.engine, domain, transport)
            for number, (device, secrets) in enumerate(profiles):
                if device.version != '3':
                    value = secrets.get('community')
                    if value:
                        config.add_v1_system(listener.engine, f'community{number}', value)
                else:
                    auth = Session.auth(type('Profile', (), {'device':device,'secrets':secrets})())
                    from pysnmp.proto.rfc1902 import OctetString
                    config.add_v3_user(listener.engine, device.username, auth.authentication_protocol, auth.authentication_key, auth.privacy_protocol, auth.privacy_key, securityEngineId=OctetString(hexValue=device.engine_id) if device.engine_id else None)
            def received(engine, state, context_engine, context_name, bindings, ctx):
                point = engine.observer.get_execution_context('rfc3412.receiveMessage:request')
                from pysnmp.proto import rfc1905
                kind='INFORM' if point['pdu'].tagSet==rfc1905.InformRequestPDU.tagSet else 'TRAP'
                address=point['transportAddress'];endpoint=f'[{address[0]}]:{address[1]}' if ':' in address[0] else f'{address[0]}:{address[1]}'
                self.notification.emit({'transaction':listener.transaction,'type':kind,'address':endpoint,
                    'values':[to_value(oid, value) for oid,value in bindings], 'time':__import__('time').time()})
            listener.receiver = ntfrcv.NotificationReceiver(listener.engine, received)
            await transport._lport
            return f'{host}:{port}'
        except BaseException:
            listener.close()
            self.listener = None
            raise

    def stop(self):
        if self.loop and self.isRunning():
            self.loop.call_soon_threadsafe(self.loop.stop)
            self.wait(5000)
