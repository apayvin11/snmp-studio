# SPDX-License-Identifier: Apache-2.0
"""Local SNMPv1/v2c agent implementing SNMPSTUDIO-DEMO-MIB.

Run independently: python -m snmpstudio.simulator --port 1161
"""
from __future__ import annotations

import argparse
from collections import deque
import logging
import math
from pathlib import Path
import signal
import socket
import threading
import time

from . import APP_NAME

from pyasn1.codec.ber import decoder, encoder
from pysnmp.proto import api
from pysnmp.proto.api import v2c
from pysnmp.proto.api.verdec import decode_message_version

DEMO_DEVICE_ID = 'snmpstudio-local-demo'
DEMO_ROOT = '1.3.6.1.4.1.55555'
TEMPERATURE_OID = DEMO_ROOT + '.1.1.0'
SET_POINT_OID = DEMO_ROOT + '.1.2.0'
MODE_OID = DEMO_ROOT + '.1.3.0'
DEMO_NOTIFICATION_OID = DEMO_ROOT + '.2.0.1'
TRAP_OID = '1.3.6.1.6.3.1.1.4.1.0'
UPTIME_OID = '1.3.6.1.2.1.1.3.0'
DEMO_MIB = Path(__file__).parent / 'data' / 'SNMPSTUDIO-DEMO-MIB.mib'
LOG = logging.getLogger(__name__)


class DemoAgent:
    """A bounded UDP test agent; writes affect only its in-memory model."""

    def __init__(self, host='127.0.0.1', port=0, *, read_community='public',
                 write_community='private', simulate=False, clock=time.monotonic):
        if not read_community or not write_community:
            raise ValueError('Read and write communities must not be empty')
        self.host = host
        self.read_community = read_community
        self.write_community = write_community
        self.simulate = simulate
        self.clock = clock
        self._started = self._last_update = clock()
        self._temperature = 253.0
        self.notification_callback=lambda data:None
        self.packet_callback=lambda packet:None
        self._lock=threading.RLock()
        self.notifications = deque(maxlen=1024)
        self.requests = deque(maxlen=1024)
        self.max_bindings = 1000
        self.values = {
            '1.3.6.1.2.1.1.1.0': ('OctetString', f'{APP_NAME} local demonstration agent'.encode('utf-8')),
            UPTIME_OID: ('TimeTicks', 0),
            '1.3.6.1.2.1.1.5.0': ('OctetString', b'edge-switch-01'),
            TEMPERATURE_OID: ('Integer', 253),
            SET_POINT_OID: ('Integer', 220),
            MODE_OID: ('Integer', 2),
            '1.3.6.1.2.1.31.1.1.1.6.1': ('Counter64', 18446744073709551600),
        }
        self.socket = socket.socket(socket.AF_INET6 if ':' in host else socket.AF_INET,
                                    socket.SOCK_DGRAM)
        try:
            self.socket.bind((host, port))
            self.socket.settimeout(.1)
        except BaseException:
            self.socket.close()
            raise
        self.port = self.socket.getsockname()[1]
        self.running = True
        self.thread = threading.Thread(target=self.run, name='SNMPStudio-demo-agent', daemon=True)
        self.thread.start()

    @property
    def endpoint(self):
        return f'[{self.host}]:{self.port}' if ':' in self.host else f'{self.host}:{self.port}'

    def run(self):
        while self.running:
            try:
                data, address = self.socket.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                previous=self.notifications[-1] if self.notifications else None
                response = self.respond(data)
                if response:
                    self.socket.sendto(response, address)
                    LOG.info('%s:%s RX %d bytes / TX %d bytes', address[0], address[1], len(data), len(response))
                if self.notifications and self.notifications[-1] is not previous:
                    from .protocol import Packet
                    from .models import uid
                    received=self.notifications[-1];transaction=uid()
                    LOG.info('%s:%s %s notification received (%d bytes)',address[0],address[1],received['type'],len(data))
                    for direction,raw in [('RX',data)]+([('TX',response)] if response else []):
                        version=int(decode_message_version(raw));proto=api.PROTOCOL_MODULES[version]
                        message,_=decoder.decode(raw,asn1Spec=proto.Message());pdu=proto.apiMessage.get_pdu(message)
                        self.packet_callback(Packet(DEMO_DEVICE_ID,direction,str(address),raw,received['type'],transaction,decoded=pdu.prettyPrint(),plaintext=encoder.encode(pdu)))
                    endpoint=f'[{address[0]}]:{address[1]}' if ':' in address[0] else f'{address[0]}:{address[1]}'
                    self.notification_callback({**{key:value for key,value in received.items() if key!='raw'},'address':endpoint,'transaction':transaction,'receiver':'demo'})
            except Exception:
                # Malformed or unsupported datagrams do not terminate the listener.
                LOG.debug('Ignored invalid SNMP datagram', exc_info=True)

    def _advance(self):
        now = self.clock()
        elapsed = max(0.0, now - self._last_update)
        self._last_update = now
        self.values[UPTIME_OID] = ('TimeTicks', int(max(0, now - self._started) * 100) % (1 << 32))
        if self.simulate:
            mode = self.values[MODE_OID][1]
            target = self.values[SET_POINT_OID][1] if mode == 2 else 253 if mode == 1 else self._temperature
            self._temperature += (target - self._temperature) * -math.expm1(-elapsed / 10)
            self.values[TEMPERATURE_OID] = ('Integer', round(self._temperature))

    def respond(self,data):
        with self._lock:return self._respond(data)

    def _respond(self, data):
        version = int(decode_message_version(data))
        if version not in (api.SNMP_VERSION_1, api.SNMP_VERSION_2C):
            return None
        proto = api.PROTOCOL_MODULES[version]
        message, tail = decoder.decode(data, asn1Spec=proto.Message())
        if tail:
            return None
        community = str(proto.apiMessage.get_community(message))
        if community not in (self.read_community, self.write_community):
            return None
        pdu = proto.apiMessage.get_pdu(message)
        if version == api.SNMP_VERSION_2C and pdu.tagSet in (v2c.SNMPv2TrapPDU.tagSet,v2c.InformRequestPDU.tagSet):
            from .protocol import to_value
            kind='INFORM' if pdu.tagSet==v2c.InformRequestPDU.tagSet else 'TRAP'
            self.notifications.append({'type':kind,'time':time.time(),'raw':bytes(data),'values':[to_value(oid,value) for oid,value in proto.apiPDU.get_varbinds(pdu)]})
            return encoder.encode(proto.apiMessage.get_response(message)) if kind=='INFORM' else None
        if version == api.SNMP_VERSION_1 and pdu.tagSet==proto.TrapPDU.tagSet:
            from .protocol import to_value
            from pysnmp.proto.proxy.rfc2576 import v1_to_v2
            normalized=v1_to_v2(pdu)
            self.notifications.append({'type':'TRAP','time':time.time(),'raw':bytes(data),'values':[to_value(oid,value) for oid,value in v2c.apiPDU.get_varbinds(normalized)]})
            return None
        is_set = pdu.tagSet == proto.SetRequestPDU.tagSet
        is_next = pdu.tagSet == proto.GetNextRequestPDU.tagSet
        is_bulk = version == api.SNMP_VERSION_2C and pdu.tagSet == v2c.GetBulkRequestPDU.tagSet
        if not (is_set or is_next or is_bulk or pdu.tagSet == proto.GetRequestPDU.tagSet):
            return None
        self.requests.append(bytes(data))
        self._advance()
        response = proto.apiMessage.get_response(message)
        rpdu = proto.apiMessage.get_pdu(response)
        bindings = proto.apiPDU.get_varbinds(pdu)

        def fail(status, index=0, echo=True):
            proto.apiPDU.set_error_status(rpdu, status)
            proto.apiPDU.set_error_index(rpdu, index)
            proto.apiPDU.set_varbinds(rpdu, bindings if echo else [])
            return encoder.encode(response)

        if len(bindings) > self.max_bindings:
            return fail(1, echo=False)
        if is_set and community != self.write_community:
            return fail(16 if version else 4, 1 if bindings else 0)

        accessible = {
            key: item for key, item in self.values.items()
            if version or item[0] != 'Counter64'
        }
        ordered = sorted(accessible, key=lambda key: tuple(map(int, key.split('.'))))

        def encoded_value(key):
            kind, value = accessible[key]
            return proto.ObjectIdentifier(key), getattr(proto, kind)(value)

        def successor(oid):
            key = next((key for key in ordered if tuple(map(int, key.split('.'))) > tuple(oid)), None)
            return encoded_value(key) if key else (oid, v2c.EndOfMibView(''))

        result = []
        if is_set:
            updates = {}
            for index, (oid, value) in enumerate(bindings, 1):
                key = str(oid)
                if key not in self.values:
                    return fail(11 if version else 2, index)
                if key not in (SET_POINT_OID, MODE_OID):
                    return fail(17 if version else 4, index)
                if value.tagSet != proto.Integer.tagSet:
                    return fail(7 if version else 3, index)
                number = int(value)
                if (key == SET_POINT_OID and not 0 <= number <= 1000) or (key == MODE_OID and number not in (1, 2, 3)):
                    return fail(10 if version else 3, index)
                updates[key] = ('Integer', number)
                result.append((oid, value))
            # No state changes until all bindings and the complete reply validate.
            proto.apiPDU.set_varbinds(rpdu, result)
            raw = encoder.encode(response)
            if len(raw) > 65507:
                return fail(1, echo=False)
            self.values.update(updates)
            return raw
        if is_bulk:
            non_repeaters = min(int(v2c.apiBulkPDU.get_non_repeaters(pdu)), len(bindings))
            repetitions = min(int(v2c.apiBulkPDU.get_max_repetitions(pdu)), self.max_bindings)
            result = [successor(oid) for oid, _ in bindings[:non_repeaters]]
            cursors = [oid for oid, _ in bindings[non_repeaters:]]
            for _ in range(repetitions):
                if not cursors or len(result) + len(cursors) > self.max_bindings:
                    break
                row = [successor(oid) for oid in cursors]
                result.extend(row)
                cursors = [oid for oid, _ in row]
                if all(value.tagSet == v2c.EndOfMibView.tagSet for _, value in row):
                    break
        elif is_next:
            for index, (oid, _) in enumerate(bindings, 1):
                item = successor(oid)
                if not version and item[1].tagSet == v2c.EndOfMibView.tagSet:
                    return fail(2, index)
                result.append(item)
        else:
            for index, (oid, _) in enumerate(bindings, 1):
                key = str(oid)
                if key in accessible:
                    result.append(encoded_value(key))
                elif not version:
                    return fail(2, index)
                else:
                    known_instance = key.rsplit('.', 1)[0] in {k.rsplit('.', 1)[0] for k in self.values}
                    result.append((oid, v2c.NoSuchInstance('') if known_instance else v2c.NoSuchObject('')))
        proto.apiPDU.set_varbinds(rpdu, result)
        raw = encoder.encode(response)
        return fail(1, echo=False) if len(raw) > 65507 else raw

    async def send_notification(self,host,port,kind='TRAP',packet_callback=lambda packet:None,timeout=1.0):
        import asyncio
        from pysnmp.hlapi.v3arch import asyncio as snmp
        from .models import Device,uid
        from .protocol import Session,RequestError
        if not self.running:raise RuntimeError('Demo agent is stopped')
        if kind not in ('TRAP','INFORM'):raise ValueError('Expected TRAP or INFORM')
        with self._lock:
            self._advance()
            bindings=[(oid,snmp.Integer32(self.values[oid][1])) for oid in (TEMPERATURE_OID,SET_POINT_OID,MODE_OID)]
        session=Session(Device(id=DEMO_DEVICE_ID,host=host,port=port,timeout=timeout,retries=0),{'community':self.read_community},packet_callback)
        session.operation=kind;session.transaction=uid()
        try:
            notification=snmp.NotificationType(snmp.ObjectIdentity(DEMO_NOTIFICATION_OID)).add_varbinds(*bindings)
            async with asyncio.timeout(timeout+3):
                error,status,_,_=await snmp.send_notification(session.engine,session.auth(),await session.target(),snmp.ContextData(),kind.lower(),notification)
            if error or status:raise RequestError(str(error or status))
            return {'type':kind,'acknowledged':kind=='INFORM','oid':DEMO_NOTIFICATION_OID}
        finally:session.close()

    def close(self):
        if not self.running:
            return
        self.running = False
        self.socket.close()
        self.thread.join(1)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def demo_configuration(port, language='ru', theme='dark'):
    """The same device/parameter template for GUI and isolated --demo mode."""
    from .models import Configuration, Device, Parameter
    device = Device(id=DEMO_DEVICE_ID, name='Демоагент' if language == 'ru' else 'Demo agent', host='127.0.0.1', port=port)
    names = ('Температура', 'Уставка', 'Режим работы', 'Имя устройства', 'Время работы') if language == 'ru' else (
        'Temperature', 'Set point', 'Operating mode', 'Device name', 'Uptime')
    specs = [(TEMPERATURE_OID, '0.1', '°C', 1), (SET_POINT_OID, '0.1', '°C', 1),
             (MODE_OID, '1', '', 2), ('1.3.6.1.2.1.1.5.0', '1', '', 2), (UPTIME_OID, '1', '', 2)]
    parameters = [Parameter(device=device.id, name=name, oid=oid, factor=factor, unit=unit, decimals=decimals)
                  for name, (oid, factor, unit, decimals) in zip(names, specs)]
    return Configuration(devices=[device], parameters=parameters, language=language, theme=theme)


def main(argv=None):
    parser = argparse.ArgumentParser(description=f'{APP_NAME} demo agent for SNMPSTUDIO-DEMO-MIB (SNMPv1/v2c)')
    parser.add_argument('--host', default='127.0.0.1', help='Numeric IPv4/IPv6 listen address')
    parser.add_argument('--port', type=int, default=1161, help='UDP port, 0 selects an unused port (default: 1161)')
    parser.add_argument('--read-community', default='public')
    parser.add_argument('--write-community', default='private')
    parser.add_argument('--static', action='store_true', help='Keep temperature fixed instead of simulating the process')
    parser.add_argument('--verbose', action='store_true', help='Log request/response byte counts')
    args = parser.parse_args(argv)
    if not 0 <= args.port <= 65535:
        parser.error('Port must be in the range 0…65535')
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format='%(asctime)s %(message)s')
    try:
        agent = DemoAgent(args.host, args.port, read_community=args.read_community,
                          write_community=args.write_community, simulate=not args.static)
    except (OSError, ValueError) as exc:
        parser.exit(1, f'Could not start demo agent: {exc}\n')
    stopped = threading.Event()
    previous = {}
    try:
        for sig in (signal.SIGINT, signal.SIGTERM):
            previous[sig] = signal.signal(sig, lambda *_: stopped.set())
        print(f'{APP_NAME} demo agent: udp://{agent.endpoint} (SNMPv1/v2c)', flush=True)
        print(f'MIB: {DEMO_MIB}\nSubtree: {DEMO_ROOT}\nPress Ctrl+C to stop.', flush=True)
        stopped.wait()
    finally:
        agent.close()
        for sig, handler in previous.items():
            signal.signal(sig, handler)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
