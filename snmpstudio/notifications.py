# SPDX-License-Identifier: Apache-2.0
"""Resolve notification OIDs and varbinds using loaded SMI definitions."""
from decimal import Decimal
import re
from .models import Parameter
from .mib import parts
TRAP_OID='1.3.6.1.6.3.1.1.4.1.0'
STANDARD={'1.3.6.1.2.1.1.3.0':'sysUpTime','1.3.6.1.6.3.1.1.4.1.0':'snmpTrapOID',
 '1.3.6.1.6.3.1.1.4.3.0':'snmpTrapEnterprise','1.3.6.1.2.1.1.5.0':'sysName'}


def definition(index,oid):
    cache=index._notification_cache
    if oid not in cache and len(cache)>=10000:cache.clear()
    if oid not in cache:cache[oid]=index.describe_instance(oid) or next((obj for obj in index.objects if obj.oid==oid),None)
    return cache[oid]


def type_chain(index,obj):
    seen=set();result=[]
    while obj and (obj.module,obj.name) not in seen:
        seen.add((obj.module,obj.name));result.append(obj)
        refs=list(parts(obj.declaration,'row'))
        if not refs:break
        name=refs[0][1];module=obj.module
        for owner,names in index.imports.get(obj.module,{}).items():
            if name in names:module=owner;break
        obj=next((item for item in index.objects if item.module==module and item.name==name and item.kind=='TYPE'),None)
    return result


def decode_value(index,value,unknown):
    obj=definition(index,value.oid)
    name=obj.name if obj else STANDARD.get(value.oid,unknown)
    text=value.error or Parameter().formatted(value.value,value.kind)
    chain=type_chain(index,obj);enums=next((item.enums for item in chain if item.enums),{})
    hints=[hint[1] for item in chain for hint in parts(item.declaration,'DISPLAY-HINT')]
    hint=hints[0] if hints else ''
    if not value.error and isinstance(value.value,int):
        if value.value in enums:text=f'{enums[value.value]} ({value.value})'
        elif re.fullmatch(r'd-\d{1,2}',hint):
            shift=int(hint[2:])
            if shift<=12:text=f'{Decimal(value.value).scaleb(-shift):.{shift}f}'
    if not value.error and isinstance(value.value,bytes) and re.fullmatch(r'1x[:.-]?',hint):
        text=(value.value.hex(hint[2:]) if hint[2:] else value.value.hex()).upper()
    if not value.error and obj and isinstance(value.value,bytes):
        specs=[spec for item in chain for spec in parts(item.declaration,'BITS')]
        if specs:
            labels={int(position):name for name,position in specs[0][1]}
            active=[labels.get(bit,f'bit({bit})') for bit in range(len(value.value)*8) if value.value[bit//8] & (1 << (7-bit%8))]
            text=', '.join(active) if active else '—'
    if not value.error and value.kind=='ObjectIdentifier':
        target=definition(index,str(value.value))
        if target:text=f'{target.name} ({value.value})'
    unit=obj.units if obj else ''
    return name,text+((' '+unit) if unit and not value.error else ''),obj.description if obj else ''


def notification_identity(index,data,unknown):
    oid=next((str(value.value) for value in data['values'] if value.oid==TRAP_OID),'')
    obj=definition(index,oid) if oid else None
    return oid,obj.name if obj else unknown,obj.description if obj else ''
