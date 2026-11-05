# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations
import asyncio
from dataclasses import dataclass, field
import socket
import time
from pyasn1.codec.ber import encoder
from pyasn1.type import univ
from pysnmp.hlapi.v3arch import asyncio as snmp
from pysnmp.carrier.asyncio.dgram import udp, udp6
from .models import uid, oid_tuple


SET_TYPES=('Integer32','Unsigned32','Gauge32','Counter32','Counter64','TimeTicks',
    'OctetString','HexString','ObjectIdentifier','IpAddress')


def mib_set_type(obj):
    names={'INTEGER':'Integer32','Integer32':'Integer32','Unsigned32':'Unsigned32',
        'Gauge32':'Gauge32','Counter32':'Counter32','Counter64':'Counter64','TimeTicks':'TimeTicks',
        'OCTET STRING':'OctetString','OBJECT IDENTIFIER':'ObjectIdentifier','IpAddress':'IpAddress'}
    return names.get(obj.syntax,'OctetString') if obj else 'Integer32'


@dataclass
class Packet:
    device: str
    direction: str
    address: str
    raw: bytes
    operation: str = ''
    transaction: str = ''
    id: str = field(default_factory=uid)
    timestamp: float = field(default_factory=time.time)
    decoded: str = ''
    plaintext: bytes = b''


@dataclass
class Value:
    oid: str
    kind: str
    value: object
    error: str = ''


def to_value(oid, value):
    kind = type(value).__name__
    if kind in ('NoSuchObject', 'NoSuchInstance', 'EndOfMibView'):
        return Value(str(oid), kind, '', kind)
    if isinstance(value, univ.Integer):
        data = int(value)
    elif isinstance(value, univ.ObjectIdentifier):
        data = str(value)
    elif isinstance(value, univ.OctetString):
        data = value.prettyPrint() if kind == 'IpAddress' else value.asOctets()
    else:
        data = value.prettyPrint()
    return Value(str(oid), kind, data)


class RequestError(Exception):
    def __init__(self, message, status=0, index=0):
        super().__init__(message)
        self.status, self.index = status, index


class Session:
    def __init__(self, device, secrets, packet_callback=lambda packet: None):
        self.device, self.secrets, self.emit = device, secrets, packet_callback
        self.engine = snmp.SnmpEngine()
        self.lock = asyncio.Lock()
        self.operation, self.transaction = '', ''
        self.last_rx, self.pending_decoded = None, None
        self.engine.observer.register_observer(self.observe, 'rfc3412.sendPdu', 'rfc3412.receiveMessage:response', 'rfc3412.receiveMessage:request', 'rfc3412.returnResponsePdu')
        self.targets = {}

    def observe(self, engine, point, variables, context):
        pdu = variables.get('pdu')
        if pdu is None:
            return
        decoded = pdu.prettyPrint()
        plaintext = encoder.encode(pdu)
        if 'sendPdu' in point or 'returnResponse' in point:
            self.pending_decoded = decoded, plaintext
        elif self.last_rx:
            self.last_rx.decoded, self.last_rx.plaintext = decoded, plaintext
            self.emit(self.last_rx)

    def transport_class(self, base):
        owner = self
        class Audited(base):
            def send_message(self, message, address):
                super().send_message(message, address)
                packet = Packet(owner.device.id, 'TX', str(address), bytes(message), owner.operation, owner.transaction)
                if owner.pending_decoded:
                    packet.decoded, packet.plaintext = owner.pending_decoded
                    owner.pending_decoded = None
                owner.emit(packet)
            def datagram_received(self, message, address):
                if owner.device.id == 'listener':owner.transaction=uid()
                packet = Packet(owner.device.id, 'RX', str(address), bytes(message), owner.operation, owner.transaction)
                owner.last_rx = packet
                owner.emit(packet)
                super().datagram_received(message, address)
        return Audited

    async def target(self, write=False):
        key = bool(write)
        if key not in self.targets:
            addresses = await asyncio.get_running_loop().getaddrinfo(self.device.host, self.device.port, type=socket.SOCK_DGRAM)
            ipv6 = addresses[0][0] == socket.AF_INET6
            base = snmp.Udp6TransportTarget if ipv6 else snmp.UdpTransportTarget
            transport = self.transport_class(udp6.Udp6AsyncioTransport if ipv6 else udp.UdpAsyncioTransport)
            target_type = type('AuditedTarget', (base,), {'PROTO_TRANSPORT': transport})
            self.targets[key] = await target_type.create((self.device.host, self.device.port), timeout=self.device.timeout, retries=0 if write else self.device.retries)
        return self.targets[key]

    def auth(self, write=False):
        d = self.device
        if d.version != '3':
            community = self.secrets.get('write_community') if write else None
            community = community or self.secrets.get('community', '')
            if not community:
                raise RequestError('Community is not configured')
            return snmp.CommunityData(community, mpModel=0 if d.version == '1' else 1)
        auths = {'MD5':snmp.usmHMACMD5AuthProtocol, 'SHA1':snmp.usmHMACSHAAuthProtocol,
                 'SHA256':snmp.usmHMAC192SHA256AuthProtocol, 'SHA512':snmp.usmHMAC384SHA512AuthProtocol}
        auth_key = self.secrets.get('auth_password') if d.security != 'noAuthNoPriv' else None
        priv_key = self.secrets.get('priv_password') if d.security == 'authPriv' else None
        if d.security != 'noAuthNoPriv' and not auth_key or d.security == 'authPriv' and not priv_key:
            raise RequestError('SNMPv3 passwords are not configured')
        return snmp.UsmUserData(d.username, authKey=auth_key, privKey=priv_key,
            authProtocol=auths[d.auth] if auth_key else snmp.usmNoAuthProtocol,
            privProtocol=(snmp.usmAesCfb128Protocol if d.privacy == 'AES128' else snmp.usmDESPrivProtocol) if priv_key else snmp.usmNoPrivProtocol)

    async def request(self, operation, oids, values=None, *, max_repetitions=20):
        if operation == 'GETBULK':
            if self.device.version == '1':
                raise RequestError('GetBulkRequest requires SNMPv2c or SNMPv3')
            if not 1 <= max_repetitions <= 100:
                raise RequestError('max-repetitions must be between 1 and 100')
        async with self.lock:
            self.operation, self.transaction = operation, uid()
            target = await self.target(operation == 'SET')
            auth = self.auth(operation == 'SET')
            args = [self.engine, auth, target, snmp.ContextData(contextName=self.device.context, contextEngineId=bytes.fromhex(self.device.context_engine_id) if self.device.context_engine_id else None)]
            variables = [snmp.ObjectType(snmp.ObjectIdentity(oid), value) if values is not None else snmp.ObjectType(snmp.ObjectIdentity(oid)) for oid, value in zip(oids, values if values is not None else [None]*len(oids))]
            if operation == 'GETBULK':
                result = await snmp.bulk_cmd(*args, 0, max_repetitions, *variables, lookupMib=False)
            else:
                fn = {'GET':snmp.get_cmd, 'GETNEXT':snmp.next_cmd, 'SET':snmp.set_cmd}[operation]
                result = await fn(*args, *variables, lookupMib=False)
            indication, status, index, bindings = result
            if indication:
                suffix = ' · SET result is unknown; no automatic retry' if operation == 'SET' else ''
                raise RequestError(str(indication)+suffix)
            if status:
                raise RequestError(status.prettyPrint(), int(status), int(index))
            return [to_value(oid, value) for oid, value in bindings]

    async def get(self, oids):
        try:
            return await self.request('GET', oids)
        except RequestError as exc:
            if exc.status == 1 and len(oids) > 1:
                middle = len(oids)//2
                return await self.get(oids[:middle])+await self.get(oids[middle:])
            if self.device.version == '1' and exc.status == 2 and 0 < exc.index <= len(oids):
                bad = oids[exc.index-1]
                rest = oids[:exc.index-1]+oids[exc.index:]
                return ([Value(bad, '', '', 'noSuchName')]+await self.get(rest)) if rest else [Value(bad, '', '', 'noSuchName')]
            raise

    async def walk(self, root, limit=5000, *, subtree=True, timeout=30):
        prefix, current, output = oid_tuple(root), root, []
        async with asyncio.timeout(timeout):
            while len(output) < limit:
                try:
                    rows = await self.request('GETNEXT' if self.device.version == '1' else 'GETBULK', [current])
                except RequestError as exc:
                    # SNMPv1 signals the end of the view with noSuchName.
                    if self.device.version == '1' and exc.status == 2:
                        return output
                    raise
                if not rows:
                    return output
                for row in rows:
                    address = oid_tuple(row.oid)
                    if row.error=='EndOfMibView':return output
                    if row.error:raise RequestError(f'{row.oid}: {row.error}')
                    if address <= oid_tuple(current):
                        raise RequestError('Agent returned a non-increasing OID')
                    if subtree and address[:len(prefix)]!=prefix:return output
                    output.append(row)
                    current = row.oid
                    if len(output) >= limit:
                        raise RequestError(f'WALK reached the limit ({limit} objects)')
        return output

    def close(self):
        self.engine.close_dispatcher()


def typed_value(kind, text):
    types = {'Integer32':snmp.Integer32, 'Unsigned32':snmp.Unsigned32, 'Gauge32':snmp.Gauge32,
             'Counter32':snmp.Counter32, 'Counter64':snmp.Counter64, 'TimeTicks':snmp.TimeTicks,
             'OctetString':snmp.OctetString, 'ObjectIdentifier':snmp.ObjectIdentifier, 'IpAddress':snmp.IpAddress}
    if kind == 'OctetString':
        return snmp.OctetString(text.encode('utf-8'))
    if kind == 'HexString':
        return snmp.OctetString(bytes.fromhex(text))
    if kind not in types:
        raise ValueError('Unsupported SET type')
    return types[kind](text if kind in ('OctetString','ObjectIdentifier','IpAddress') else int(text))


def validate_set_row(index,oid,kind,text):
    """Apply the same SET validation in the browser and saved commands."""
    from .mib import parts
    oid=index.resolve(oid);typed_value(kind,text)
    obj=index.describe_instance(oid)
    if obj and obj.access in ('read-only','not-accessible','accessible-for-notify'):
        raise ValueError(f'{obj.name}: {obj.access}')
    if obj and kind in ('Integer32','Unsigned32','Gauge32'):
        numeric=int(text)
        if obj.enums and numeric not in obj.enums:raise ValueError(f'{obj.name}: expected one of {obj.enums}')
        for subtype in parts(obj.declaration,'integerSubType'):
            if not any(lo<=numeric<=hi for lo,hi in subtype[1]):raise ValueError(f'{obj.name}: out of MIB range {subtype[1]}')
    return {'oid':oid,'type':kind,'value':text}
