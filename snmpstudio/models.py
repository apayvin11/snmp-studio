# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations
from dataclasses import dataclass, field, asdict, fields
from decimal import Decimal, InvalidOperation
import math
import uuid


def uid():
    return uuid.uuid4().hex


def oid_tuple(text):
    try:
        result = tuple(int(x) for x in text.strip().strip('.').split('.'))
    except (ValueError, AttributeError):
        raise ValueError('Invalid numeric OID') from None
    if len(result) < 2 or len(result) > 128 or result[0] not in (0, 1, 2) or any(x < 0 or x > 4294967295 for x in result) or (result[0] < 2 and result[1] > 39):
        raise ValueError('Invalid numeric OID')
    return result


@dataclass
class Device:
    id: str = field(default_factory=uid)
    name: str = 'Device'
    host: str = '127.0.0.1'
    port: int = 161
    version: str = '2c'
    username: str = ''
    security: str = 'authPriv'
    auth: str = 'SHA256'
    privacy: str = 'AES128'
    context: str = ''
    context_engine_id: str = ''
    engine_id: str = ''
    timeout: float = 2.0
    retries: int = 1
    period: float = 1.0
    batch: int = 32
    enabled: bool = True

    def validate(self):
        if not self.id or len(self.id) > 64 or not self.name.strip() or not self.host.strip() or len(self.host) > 253:
            raise ValueError('Device: name and host are required')
        if self.version not in ('1', '2c', '3') or not 1 <= self.port <= 65535:
            raise ValueError('Device: version / port')
        if not .1 <= self.period <= 86400 or not .1 <= self.timeout <= 60 or not 0 <= self.retries <= 5 or not 1 <= self.batch <= 128:
            raise ValueError('Device: polling limits')
        for value in (self.context_engine_id, self.engine_id):
            if value:
                try: raw=bytes.fromhex(value)
                except ValueError: raise ValueError('Engine ID must be hexadecimal') from None
                if not 5 <= len(raw) <= 32: raise ValueError('Engine ID must contain 5–32 bytes')
        if self.version == '3' and not self.username:
            raise ValueError('SNMPv3: username is required')
        if self.security not in ('noAuthNoPriv', 'authNoPriv', 'authPriv') or self.auth not in ('MD5', 'SHA1', 'SHA256', 'SHA512') or self.privacy not in ('AES128', 'DES'):
            raise ValueError('SNMPv3: unsupported security options')


@dataclass
class Parameter:
    id: str = field(default_factory=uid)
    device: str = ''
    name: str = 'Parameter'
    oid: str = '1.3.6.1.2.1.1.1.0'
    unit: str = ''
    factor: str = '1'
    offset: str = '0'
    decimals: int = 2
    period: float = 0
    enabled: bool = True
    display: str = 'auto'

    def validate(self):
        oid_tuple(self.oid)
        if not self.id or len(self.id) > 64 or not self.name.strip() or len(self.name) > 200:
            raise ValueError('Parameter: name is required')
        for x in (self.factor, self.offset):
            try:
                if len(str(x)) > 64 or not Decimal(x).is_finite():
                    raise ValueError('Scale must be finite')
            except (InvalidOperation, TypeError):
                raise ValueError('Invalid scale') from None
        if not 0 <= self.decimals <= 12 or not (self.period == 0 or .1 <= self.period <= 86400) or self.display not in ('auto', 'text', 'hex', 'duration'):
            raise ValueError('Parameter: display / period')

    def formatted(self, value, kind=''):
        if isinstance(value, bytes):
            return value.hex(' ').upper() if self.display == 'hex' else value.decode('utf-8', 'backslashreplace')
        if isinstance(value, int):
            if kind == 'TimeTicks' and self.display in ('auto', 'duration'):
                seconds = value // 100
                days, seconds = divmod(seconds, 86400)
                h, seconds = divmod(seconds, 3600)
                m, s = divmod(seconds, 60)
                return f'{days}d {h:02}:{m:02}:{s:02}'
            scaled = Decimal(value) * Decimal(self.factor) + Decimal(self.offset)
            if Decimal(self.factor) == 1 and Decimal(self.offset) == 0:
                return str(value)
            return f'{scaled:.{self.decimals}f}'
        return str(value)


@dataclass
class Configuration:
    devices: list[Device] = field(default_factory=list)
    parameters: list[Parameter] = field(default_factory=list)
    mibs: list[str] = field(default_factory=list)
    commands: list[dict] = field(default_factory=list)
    language: str = 'ru'
    theme: str = 'dark'
    editor_enabled: bool = False

    def validate(self):
        if type(self.editor_enabled) is not bool:
            raise ValueError('Invalid editor setting')
        if self.language not in ('ru', 'en') or self.theme not in ('dark', 'light'):
            raise ValueError('Invalid interface options')
        if len(self.devices) > 500 or len(self.parameters) > 10000 or len(self.mibs) > 500:
            raise ValueError('Configuration is too large')
        ids = [d.id for d in self.devices]
        pids = [p.id for p in self.parameters]
        if len(set(ids)) != len(ids) or len(set(pids)) != len(pids):
            raise ValueError('Duplicate identifiers')
        for d in self.devices:
            d.validate()
        for p in self.parameters:
            p.validate()
            if p.device not in ids:
                raise ValueError('Parameter references unknown device')
        if any(not isinstance(x, str) or len(x) > 4096 for x in self.mibs):
            raise ValueError('Invalid MIB path')
        if not isinstance(self.commands,list) or len(self.commands) > 1000:
            raise ValueError('Too many commands')
        command_ids=[]
        for command in self.commands:
            if not isinstance(command,dict) or not isinstance(command.get('name'),str) or not command['name'].strip() or len(command['name'])>200:
                raise ValueError('Command: name is required')
            if 'id' in command:
                identifier=command['id']
                if not isinstance(identifier,str) or not identifier or len(identifier)>64:raise ValueError('Invalid command identifier')
                command_ids.append(identifier)
            if command.get('device') is not None and not isinstance(command['device'],str):raise ValueError('Invalid command device')
            if type(command.get('verify',False)) is not bool:raise ValueError('Invalid command verification option')
            rows=command.get('rows')
            if not isinstance(rows,list) or not rows:raise ValueError('Command: expected at least one OID')
            for row in rows:
                if not isinstance(row,dict) or not isinstance(row.get('oid'),str) or not isinstance(row.get('type'),str) or not isinstance(row.get('value'),str):raise ValueError('Invalid command value')
                oid_tuple(row['oid'])
        if len(command_ids)!=len(set(command_ids)):raise ValueError('Duplicate command identifiers')

    def to_dict(self):
        return {'format': 'snmpstudio', 'schema': 1, **asdict(self)}

    @classmethod
    def from_dict(cls, data):
        if data.get('format') != 'snmpstudio' or data.get('schema') != 1:
            raise ValueError('Unsupported configuration format')
        kwargs = {k: v for k, v in data.items() if k in {f.name for f in fields(cls)}}
        kwargs['devices'] = [Device(**x) for x in data.get('devices', [])]
        kwargs['parameters'] = [Parameter(**x) for x in data.get('parameters', [])]
        config = cls(**kwargs)
        config.validate()
        return config
